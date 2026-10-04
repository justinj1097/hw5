"""Offline checks that the MCP server itself enforces the shop rules (no AI calls).
Each test runs on a fresh temporary copy of the original database."""

from __future__ import annotations

import hashlib
import hmac
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest
from fastmcp import Client

HW5 = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HW5 / "mcp_server"))
import server  # noqa: E402

SECRET = "test-secret"


def code(action: str, request_id: int, person: str) -> str:
    return hmac.new(SECRET.encode(), f"{action}:{request_id}:{person}".encode(), hashlib.sha256).hexdigest()


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "campus_customs_new.db"
    shutil.copyfile(HW5 / "data" / "campus_customs.db", path)
    monkeypatch.setattr(server, "DB_PATH", path)
    monkeypatch.setenv("CC_APPROVAL_SECRET", SECRET)
    return path


async def call(name: str, args: dict | None = None) -> dict:
    async with Client(server.mcp) as c:
        return (await c.call_tool(name, args or {}, raise_on_error=False)).structured_content


def q(path, sql, *args):
    with sqlite3.connect(path) as conn:
        return conn.execute(sql, args).fetchall()


async def test_reads_match_database(db):
    assert (await call("get_shop_today"))["date_today"] == "2026-08-31"
    s = await call("check_stock", {"sku": "CC-HOOD-NAVY", "size": "M", "qty_needed": 20})
    assert (s["on_hand"], s["shortfall"], s["unit_cost"]) == (8, 12, 22.0)
    v = await call("check_vendor_invoices", {"invoice_id": 501})
    assert v["can_ship"] is False and v["open_invoices"][0]["days_overdue"] == 3 and v["lead_days"] == 5


async def test_request_does_not_move_money(db):
    r = await call("request_payment_approval", {"kind": "invoice", "ref_id": 501, "reason": "t", "requested_by": "accounting", "ticket_id": 101})
    assert r["ok"] and r["amount"] == 840.0
    assert q(db, "SELECT COUNT(*) FROM payments")[0][0] == 0
    assert q(db, "SELECT balance FROM cash_accounts")[0][0] == 3400.0
    dup = await call("request_payment_approval", {"kind": "invoice", "ref_id": 501, "reason": "t", "requested_by": "accounting"})
    assert not dup["ok"] and "Already waiting" in dup["error"]


async def test_pay_refused_without_human_code(db):
    r = await call("request_payment_approval", {"kind": "rent", "ref_id": 1, "reason": "t", "requested_by": "accounting"})
    bad = await call("execute_approved_request", {"request_id": r["request_id"], "approved_by": "accounting", "approval_code": "nope"})
    assert not bad["ok"]
    assert q(db, "SELECT COUNT(*) FROM payments")[0][0] == 0


async def test_human_approval_pays_and_updates_tables(db):
    inv = await call("request_payment_approval", {"kind": "invoice", "ref_id": 501, "reason": "t", "requested_by": "accounting"})
    rent = await call("request_payment_approval", {"kind": "rent", "ref_id": 1, "reason": "t", "requested_by": "accounting"})
    ok = await call("execute_approved_request", {"request_id": inv["request_id"], "approved_by": "Justin", "approval_code": code("execute", inv["request_id"], "Justin")})
    assert ok["ok"] and ok["checking_balance"] == 2560.0
    assert q(db, "SELECT status FROM invoices WHERE id = 501")[0][0] == "paid"
    ok2 = await call("execute_approved_request", {"request_id": rent["request_id"], "approved_by": "Justin", "approval_code": code("execute", rent["request_id"], "Justin")})
    assert ok2["next_due"] == "2026-10-02" and ok2["checking_balance"] == 160.0
    assert q(db, "SELECT kind, amount, paid_at, approved_by FROM payments ORDER BY id") == [
        ("invoice", 840.0, "2026-08-31", "Justin"), ("rent", 2400.0, "2026-08-31", "Justin")]


async def test_no_negative_balance_and_vendor_block(db):
    po = await call("request_purchase_order", {"vendor_id": 1, "sku": "CC-HOOD-NAVY", "size": "M", "qty": 12, "reason": "t", "requested_by": "accounting"})
    assert po["ok"] and po["amount"] == 264.0 and po["blocked_by_open_invoices"] == [501]
    blocked = await call("execute_approved_request", {"request_id": po["request_id"], "approved_by": "J", "approval_code": code("execute", po["request_id"], "J")})
    assert not blocked["ok"] and "rule 3" in blocked["error"]
    for kind, ref in (("invoice", 501), ("rent", 1)):
        r = await call("request_payment_approval", {"kind": kind, "ref_id": ref, "reason": "t", "requested_by": "accounting"})
        await call("execute_approved_request", {"request_id": r["request_id"], "approved_by": "J", "approval_code": code("execute", r["request_id"], "J")})
    broke = await call("execute_approved_request", {"request_id": po["request_id"], "approved_by": "J", "approval_code": code("execute", po["request_id"], "J")})
    assert not broke["ok"] and "Not enough cash" in broke["error"]
    assert q(db, "SELECT balance FROM cash_accounts")[0][0] == 160.0


async def test_drafts_and_status_rules(db):
    d = await call("save_draft_message", {"recipient": "Tauhid Zaman", "subject": "Your tee", "body": "Hi", "drafted_by": "customer_service", "ticket_id": 101})
    assert d["ok"] and d["sent"] is False
    await call("request_payment_approval", {"kind": "invoice", "ref_id": 501, "reason": "t", "requested_by": "accounting", "ticket_id": 101})
    res = await call("update_ticket_status", {"ticket_id": 101, "status": "resolved", "note": "x", "updated_by": "boss"})
    assert not res["ok"]
    disc = await call("check_discount", {"sku": "CC-HOOD-NAVY", "qty": 20, "unit_price": 20})
    assert disc["at_or_below_cost"] is True


async def test_role_guard_blocks_human_tools():
    sys.path.insert(0, str(HW5 / "backend"))
    from agents import HUMAN_ONLY_TOOLS, ROLE_TOOLS
    for role, tools in ROLE_TOOLS.items():
        assert not tools & HUMAN_ONLY_TOOLS, role
    assert {"request_payment_approval", "request_purchase_order"} <= ROLE_TOOLS["accounting"]
    assert all("request_payment_approval" not in t for r, t in ROLE_TOOLS.items() if r != "accounting")


async def test_last_approval_resolves_waiting_ticket(db):
    r = await call("request_payment_approval", {"kind": "rent", "ref_id": 1, "reason": "t", "requested_by": "accounting", "ticket_id": 102})
    await call("update_ticket_status", {"ticket_id": 102, "status": "waiting_on_approval", "note": "x", "updated_by": "boss"})
    await call("execute_approved_request", {"request_id": r["request_id"], "approved_by": "J", "approval_code": code("execute", r["request_id"], "J")})
    assert q(db, "SELECT status FROM tickets WHERE id = 102")[0][0] == "resolved"
    assert (await call("list_tickets"))["tickets"][1]["pending_approvals"] == 0
