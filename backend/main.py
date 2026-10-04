"""Campus Customs operations API (FastAPI) - what the React dashboard calls.

Run from backend/:   uvicorn main:app --reload --port 8000

- Shop data and approvals go through the same MCP server the agents use (one long-lived stdio connection
  opened at startup); this file never touches the database with its own SQL.
- POST /api/tickets/{id}/run starts the agent team in the background and returns a run id at once; the board
  polls /api/events and /api/runs/{run_id} to watch it. Only one team run at a time, because every ticket
  draws on the same cash and the order of requests matters.
- Only POST /api/approvals/{id}/approve moves money, and only after a person clicks Approve.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from agents import Team, decide_request, make_mcp_toolset
from audit import AuditLog
from config import AUDIT_PATH, MODEL_NAME, WORKING_DB
from reset_db import reset

# ---------------------------------------------------------------- app state

_mcp = make_mcp_toolset()          # app-level MCP connection for reads and human approvals
_run_lock = asyncio.Lock()         # one agent-team run at a time
_runs: dict[str, dict[str, Any]] = {}
_tasks: set[asyncio.Task] = set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await _mcp.__aenter__()
    try:
        yield
    finally:
        for task in _tasks:
            task.cancel()
        await _mcp.__aexit__(None, None, None)


app = FastAPI(title="Campus Customs Ops API", version="1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["GET", "POST"], allow_headers=["Content-Type"])


async def mcp_call(tool: str, args: dict | None = None) -> dict:
    try:
        return await _mcp.direct_call_tool(tool, args or {})
    except Exception as exc:  # MCP server down or tool crashed
        raise HTTPException(502, f"MCP tool {tool} failed: {exc}") from exc


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- request / response bodies

class Decision(BaseModel):
    approved_by: str = Field(min_length=1, max_length=60, description="Name of the person approving.")


class Rejection(BaseModel):
    rejected_by: str = Field(min_length=1, max_length=60)
    reason: str = Field(default="Rejected on the dashboard.", max_length=500)


class RunInfo(BaseModel):
    run_id: str
    ticket_id: int
    status: Literal["running", "completed", "failed"]
    started_at: str
    finished_at: str | None = None
    decision: dict | None = None
    error: str | None = None


# ---------------------------------------------------------------- 1. tickets

@app.get("/api/tickets")
async def list_tickets() -> dict:
    """All tickets with open/resolved status, board status, and counts of pending approvals and drafts."""
    return await mcp_call("list_tickets")


@app.get("/api/tickets/{ticket_id}")
async def get_ticket(ticket_id: int) -> dict:
    """One ticket with its linked lease/invoice and everything on the board for it (notes, requests, drafts)."""
    t = await mcp_call("get_ticket", {"ticket_id": ticket_id})
    if not t.get("found"):
        raise HTTPException(404, t.get("error", "Ticket not found."))
    return t


class ManagerNote(BaseModel):
    author: str = Field(min_length=1, max_length=60, description="The person writing the note.")
    note: str = Field(min_length=1, max_length=1000)


@app.post("/api/tickets/{ticket_id}/note")
async def add_manager_note(ticket_id: int, body: ManagerNote) -> dict:
    """A person pins a decision or instruction on the ticket (e.g. how to handle a blocked ticket). The Boss reads it
    from get_ticket on its next run. Notes never move money - payments still need the approve route."""
    author = " ".join(body.author.split())
    result = await mcp_call("add_ticket_note", {"ticket_id": ticket_id, "note": body.note.strip(), "author": f"{author} (manager)"})
    if not result.get("ok"):
        raise HTTPException(404, result.get("error", "Could not add the note."))
    AuditLog(uuid.uuid4().hex[:12], ticket_id).add("human_decision", agent="human", tool_name="add_ticket_note",
                                                   tool_args={"author": author, "note": body.note}, result="note pinned")
    return result


# ---------------------------------------------------------------- 2. run the agent team

async def _run_team(run_id: str, ticket_id: int) -> None:
    info = _runs[run_id]
    try:
        async with Team() as team:
            decision = await team.run_ticket(ticket_id, run_id=run_id)
        info.update(status="completed", decision=decision.model_dump())
    except Exception as exc:
        info.update(status="failed", error=f"{type(exc).__name__}: {exc}"[:500])
    finally:
        info["finished_at"] = _now()
        _run_lock.release()


@app.post("/api/tickets/{ticket_id}/run", status_code=202)
async def run_ticket(ticket_id: int) -> RunInfo:
    """Start the Boss (and whoever it delegates to) on one ticket. Returns at once; poll /api/runs/{run_id}."""
    t = await mcp_call("get_ticket", {"ticket_id": ticket_id})
    if not t.get("found"):
        raise HTTPException(404, t.get("error", "Ticket not found."))
    if _run_lock.locked():
        busy = next((r for r in _runs.values() if r["status"] == "running"), None)
        raise HTTPException(409, f"The team is already working ticket {busy['ticket_id'] if busy else '?'}; wait for it to finish.")
    await _run_lock.acquire()
    run_id = uuid.uuid4().hex[:12]
    _runs[run_id] = {"run_id": run_id, "ticket_id": ticket_id, "status": "running", "started_at": _now()}
    task = asyncio.create_task(_run_team(run_id, ticket_id))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return RunInfo(**_runs[run_id])


@app.get("/api/runs")
async def list_runs() -> list[RunInfo]:
    """Team runs started since the server came up, newest first."""
    return [RunInfo(**r) for r in sorted(_runs.values(), key=lambda r: r["started_at"], reverse=True)]


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str) -> RunInfo:
    """One run's status, and the Boss's decision once it finishes."""
    if run_id not in _runs:
        raise HTTPException(404, "Unknown run id (runs are kept in memory until the server restarts).")
    return RunInfo(**_runs[run_id])


# ---------------------------------------------------------------- 3. agent events

_SHOWN = {"agent_started", "model_response", "tool_call", "tool_result", "delegation", "validation_retry",
          "agent_finished", "run_started", "run_finished", "error", "human_decision"}


def _event_view(e: dict) -> dict:
    """Trim an audit record to what the board shows: who, what they said, which tool, with what."""
    said = e.get("text") if e["event"] in ("model_response", "agent_finished", "agent_started") else None
    return {k: v for k, v in {
        "timestamp": e["timestamp"], "run_id": e["run_id"], "ticket_id": e.get("ticket_id"), "step": e["step"],
        "agent": e["agent"], "depth": e["depth"], "chain": e.get("chain"), "event": e["event"],
        "said": said, "tool": e.get("tool_name"), "tool_args": e.get("tool_args"),
        "result": None if e["event"] in ("model_response", "agent_finished") else e.get("result"),
        "model": e.get("model_name"), "stop_reason": e.get("stop_reason"),
        "tokens": (e.get("input_tokens") or 0) + (e.get("output_tokens") or 0) or None,
    }.items() if v is not None}


@app.get("/api/events")
async def events(limit: int = Query(100, ge=1, le=1000), ticket_id: int | None = None, run_id: str | None = None,
                 after: str | None = Query(None, description="Only events with a timestamp after this ISO time (for polling).")) -> dict:
    """Recent agent events from output/audit_trail.json, oldest first within the window."""
    try:
        trail = json.loads(AUDIT_PATH.read_text() or "[]") if AUDIT_PATH.exists() else []
    except json.JSONDecodeError:
        trail = []
    picked = [e for e in trail if e.get("event") in _SHOWN
              and (ticket_id is None or e.get("ticket_id") == ticket_id)
              and (run_id is None or e.get("run_id") == run_id)
              and (after is None or e["timestamp"] > after)]
    picked = picked[-limit:]
    return {"count": len(picked), "latest": picked[-1]["timestamp"] if picked else after,
            "running": [r["run_id"] for r in _runs.values() if r["status"] == "running"],
            "events": [_event_view(e) for e in picked]}


# ---------------------------------------------------------------- 4. human approvals

@app.get("/api/approvals")
async def approvals(status: Literal["pending", "executed", "rejected", "failed", "all"] = "pending") -> dict:
    """Payment and purchase-order requests the agents prepared (pending ones wait for a person),
    each with the payee's name so the board can show who the money goes to."""
    data = await mcp_call("list_approval_requests", {"status": status})
    if data["requests"]:
        vendors = {v["id"]: v for v in (await mcp_call("list_vendors"))["vendors"]}
        leases = {l["id"]: l for l in (await mcp_call("get_cash_position"))["leases"]}
        for r in data["requests"]:
            if r["kind"] == "rent":
                lease = leases.get(r["ref_id"], {})
                r["payee"], r["what"] = lease.get("landlord"), f"Rent · {lease.get('space_name', 'lease ' + str(r['ref_id']))}"
            else:
                vendor = vendors.get(r["vendor_id"], {})
                r["payee"] = vendor.get("name")
                r["what"] = (f"Invoice #{r['ref_id']}" if r["kind"] == "invoice"
                             else f"Purchase order · {r['qty']} × {r['sku']} {r['size']}")
                r["vendor_can_ship"] = vendor.get("can_ship")
                r["vendor_open_invoice_ids"] = vendor.get("open_invoice_ids", [])
    return data


async def _request_ticket(request_id: int) -> int | None:
    """Which ticket a request belongs to, so the audit trail links the human decision to it."""
    reqs = (await mcp_call("list_approval_requests", {"status": "all"}))["requests"]
    return next((r["ticket_id"] for r in reqs if r["id"] == request_id), None)


@app.post("/api/approvals/{request_id}/approve")
async def approve(request_id: int, body: Decision) -> dict:
    """A person clicked Approve: pay the request. This is the only route that changes cash.
    The MCP server refuses overdrafts, changed amounts, and orders to a vendor with an unpaid invoice."""
    result = await decide_request(_mcp, request_id, body.approved_by, approve=True, ticket_id=await _request_ticket(request_id))
    if not result.get("ok"):
        raise HTTPException(409, result.get("error", "Payment refused."))
    return result


@app.post("/api/approvals/{request_id}/reject")
async def reject(request_id: int, body: Rejection) -> dict:
    """A person clicked Reject: decline the request (no money moves)."""
    result = await decide_request(_mcp, request_id, body.rejected_by, approve=False, reason=body.reason,
                                  ticket_id=await _request_ticket(request_id))
    if not result.get("ok"):
        raise HTTPException(409, result.get("error", "Could not reject."))
    return result


# ---------------------------------------------------------------- 5. cash

@app.get("/api/cash")
async def cash() -> dict:
    """Current checking balance from cash_accounts, plus open invoices, pending requests, and payments made."""
    pos = await mcp_call("get_cash_position")
    paid = await mcp_call("list_payments")
    checking = next((a for a in pos["accounts"] if a["name"] == "checking"), None)
    if checking is None:
        raise HTTPException(404, "No checking account in cash_accounts.")
    return {"account": "checking", "balance": checking["balance"], "as_of": checking["date"], "today": pos["today"],
            "pending_approval_total": pos["pending_approval_total"], "checking_after_all_pending": pos["checking_after_all_pending"],
            "open_invoices": pos["open_invoices"], "payments": paid["payments"], "total_paid": paid["total_paid"]}


# ---------------------------------------------------------------- 6. reset

@app.post("/api/reset")
async def reset_database() -> dict:
    """Shop rule 7: copy the original campus_customs.db over the working copy. The audit trail is kept."""
    if _run_lock.locked():
        raise HTTPException(409, "An agent run is in progress; reset after it finishes.")
    md5 = reset()
    return {"ok": True, "database": WORKING_DB.name, "md5": md5, "reset_at": _now(),
            "note": "Tickets, cash, invoices and leases are back to the original values; board tables were cleared."}


@app.get("/api/health")
async def health() -> dict:
    return {"ok": True, "model": MODEL_NAME, "database": WORKING_DB.name}
