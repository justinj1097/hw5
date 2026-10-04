"""The Campus Customs agent team: Boss, Inventory, Accounting, Facilities, Customer Service.

- One PydanticAI agent per role, all on gpt-6-luna through Portkey (the served model name is checked on
  every response; anything else stops the run).
- Instructions come from backend/prompts/<role>.md plus a short live note (who you are, who is up the chain,
  how many delegations are left).
- Shop facts and actions come only from the MCP server (mcp_server/server.py) over data/campus_customs_new.db.
  Each role sees only its allowlisted MCP tools; the human-only pay/reject tools are hidden from every agent
  and the server refuses them anyway without a human approval code.
- Full connectivity: every agent has a `delegate` tool that can hand a task to any other teammate. Loops are
  blocked (you can't delegate to someone already up the chain), depth and count are capped, and every agent in
  a ticket run draws on one shared token/request budget.
- Every loop step is appended to output/audit_trail.json.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
import uuid
from typing import Any

from openai import AsyncOpenAI
from pydantic_ai import Agent, CallToolsNode, ModelRequestNode, ModelRetry, RunContext
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.mcp import MCPToolset, StdioTransport
from pydantic_ai.messages import RetryPromptPart, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.usage import RunUsage, UsageLimits

from audit import AuditLog, clip
from config import (APPROVAL_SECRET, MAX_DELEGATION_DEPTH, MAX_DELEGATIONS_PER_RUN, MCP_PYTHON, MCP_SERVER,
                    MODEL_FAMILY, MODEL_NAME, PORTKEY_API_KEY, PORTKEY_BASE_URL, PORTKEY_PROVIDER, PROMPTS_DIR,
                    REASONING_EFFORT, TEAM_REQUEST_LIMIT, TEAM_TOOL_CALLS_LIMIT, TEAM_TOTAL_TOKENS_LIMIT,
                    TOOL_RESULT_AUDIT_CHARS, WORKING_DB)
from models import AGENT_NAMES, AgentDeps, AgentName, BossDecision, DelegationResult, SpecialistReport, TeamState

# ---------------------------------------------------------------- which MCP tools each role may use

_COMMON = {"get_shop_today", "list_open_tickets", "get_ticket", "add_ticket_note", "list_approval_requests"}
ROLE_TOOLS: dict[AgentName, frozenset[str]] = {
    "boss": frozenset(_COMMON | {"update_ticket_status", "check_stock", "list_vendors", "check_vendor_invoices",
                                 "get_rent_due", "get_cash_position", "check_discount", "list_payments"}),
    "inventory": frozenset(_COMMON | {"check_stock", "list_vendors", "check_vendor_invoices", "estimate_restock"}),
    "accounting": frozenset(_COMMON | {"get_cash_position", "check_vendor_invoices", "list_vendors", "check_discount",
                                       "list_payments", "get_rent_due", "estimate_restock", "check_stock",
                                       "request_payment_approval", "request_purchase_order"}),
    "facilities": frozenset(_COMMON | {"get_rent_due", "get_cash_position", "save_draft_message"}),
    "customer_service": frozenset(_COMMON | {"check_stock", "check_discount", "save_draft_message"}),
}
HUMAN_ONLY_TOOLS = frozenset({"execute_approved_request", "reject_request"})
# Attribution fields are filled in by the code, not the model, so the board always shows who really acted.
_AUTHOR_FIELDS = {"request_payment_approval": "requested_by", "request_purchase_order": "requested_by",
                  "save_draft_message": "drafted_by", "update_ticket_status": "updated_by", "add_ticket_note": "author"}

ROLE_BLURBS: dict[AgentName, str] = {
    "boss": "reads tickets, decides who works on them, makes the final call, sets ticket status",
    "inventory": "stock by SKU/size, shortfalls, which vendor can restock, lead times and arrival dates",
    "accounting": "cash, invoices, margins and discounts; the only role that can file payment or purchase-order approval requests",
    "facilities": "the shop space: leases, rent amounts and due dates, landlord notes and drafts",
    "customer_service": "drafts messages to customers and student orgs (saved on the board, never sent)",
}


def approval_code(action: str, request_id: int, person: str) -> str:
    """The code the MCP server checks before money moves. Only backend code that holds the secret can make it."""
    return hmac.new(APPROVAL_SECRET.encode(), f"{action}:{request_id}:{person}".encode(), hashlib.sha256).hexdigest()


async def _guard_and_record(ctx: RunContext[AgentDeps], call_tool: Any, name: str, args: dict[str, Any]) -> Any:
    """Runs around every MCP call an agent makes: enforces the role allowlist, stamps the real author,
    and remembers ids the server actually created (agents may only report those)."""
    deps = ctx.deps
    if name in HUMAN_ONLY_TOOLS or name not in ROLE_TOOLS[deps.agent]:
        return {"ok": False, "error": f"'{name}' is not available to {deps.agent}. Payments need a human; delegate instead."}
    if name in _AUTHOR_FIELDS:
        args = {**args, _AUTHOR_FIELDS[name]: deps.agent}
    result = await call_tool(name, args)
    if isinstance(result, dict) and result.get("ok"):
        if "request_id" in result:
            deps.state.request_ids.add(int(result["request_id"]))
        if "draft_id" in result:
            deps.state.draft_ids.add(int(result["draft_id"]))
        if name == "update_ticket_status":
            deps.state.tickets_updated.add(int(result["ticket_id"]))
    return result


def make_mcp_toolset() -> MCPToolset:
    env = {k: v for k, v in os.environ.items() if k != "PORTKEY_API_KEY"}  # the MCP server never needs the AI key
    env.update(CAMPUS_CUSTOMS_DB=str(WORKING_DB), CC_APPROVAL_SECRET=APPROVAL_SECRET)
    return MCPToolset(StdioTransport(MCP_PYTHON, [str(MCP_SERVER)], env=env),
                      process_tool_call=_guard_and_record, tool_error_behavior="retry", max_retries=2)


# ---------------------------------------------------------------- the delegate tool (full connectivity)

async def delegate(ctx: RunContext[AgentDeps], to: AgentName, task: str) -> DelegationResult:
    """Hand a focused task to another teammate and get their structured report back.

    Any agent may delegate to any other agent. Write the task so it stands alone: the ticket id, the exact
    SKU/size/qty or invoice/lease id, what you already know (with numbers), and what you need back.
    You cannot delegate to yourself or to anyone already up your chain, and delegations are limited per ticket.

    Args:
        to: boss | inventory | accounting | facilities | customer_service
        task: The self-contained request for that teammate.
    """
    d = ctx.deps
    st = d.state
    refusal = None
    if to not in AGENT_NAMES:
        refusal = f"Unknown teammate '{to}'."
    elif to == d.agent:
        refusal = "You can't delegate to yourself - do the work with your own tools."
    elif to in d.chain:
        refusal = f"{to} is already up your chain ({' -> '.join(d.chain)}); delegating back would loop. Report what you found instead."
    elif d.depth + 1 > MAX_DELEGATION_DEPTH:
        refusal = f"Delegation depth limit ({MAX_DELEGATION_DEPTH}) reached. Finish with your own tools and report blockers."
    elif st.delegations >= MAX_DELEGATIONS_PER_RUN:
        refusal = f"This ticket has used all {MAX_DELEGATIONS_PER_RUN} delegations. Finish with what you have."
    if refusal:
        st.audit.add("delegation", agent=d.agent, depth=d.depth, chain=d.chain, tool_name="delegate",
                     tool_args={"to": to, "task": task}, result=f"refused: {refusal}")
        return DelegationResult(ok=False, to=to, error=refusal)

    st.delegations += 1
    st.audit.add("delegation", agent=d.agent, depth=d.depth, chain=d.chain, tool_name="delegate",
                 tool_args={"to": to, "task": task}, result=f"delegation {st.delegations}/{MAX_DELEGATIONS_PER_RUN}")
    child = AgentDeps(agent=to, depth=d.depth + 1, chain=(*d.chain, to), state=st, team=d.team)
    try:
        output = await d.team.run_agent(to, task, child, usage=ctx.usage)
    except UsageLimitExceeded:
        raise  # the shared budget is spent: stop the whole ticket run
    except Exception as exc:  # a teammate failing shouldn't crash the caller; it gets told and can carry on
        return DelegationResult(ok=False, to=to, error=f"{type(exc).__name__}: {clip(str(exc), 300)}")
    return DelegationResult(ok=True, to=to, report=output.model_dump())


# ---------------------------------------------------------------- building agents

def load_prompt(role: AgentName) -> str:
    return (PROMPTS_DIR / f"{role}.md").read_text()


def build_agent(role: AgentName, model: OpenAIResponsesModel, mcp: MCPToolset) -> Agent:
    output_type = BossDecision if role == "boss" else SpecialistReport
    agent: Agent[AgentDeps, Any] = Agent(
        model, name=role, deps_type=AgentDeps, output_type=output_type, instructions=load_prompt(role),
        toolsets=[mcp.filtered(lambda ctx, td: td.name in ROLE_TOOLS[ctx.deps.agent])],
        tools=[delegate], model_settings={"openai_reasoning_effort": REASONING_EFFORT[role]}, retries=2,
    )

    @agent.instructions
    def live_context(ctx: RunContext[AgentDeps]) -> str:
        d = ctx.deps
        others = "\n".join(f"- {n}: {ROLE_BLURBS[n]}" + (" (up your chain - don't delegate back)" if n in d.chain else "")
                           for n in AGENT_NAMES if n != d.agent)
        return (f"## This run\nYou are **{d.agent}**. Ticket in focus: {d.state.ticket_id}. "
                f"Delegation chain: {' -> '.join(d.chain)} (depth {d.depth} of max {MAX_DELEGATION_DEPTH}). "
                f"Delegations left for this ticket: {MAX_DELEGATIONS_PER_RUN - d.state.delegations}.\n"
                f"Teammates you can delegate to:\n{others}")

    @agent.output_validator
    def check_output(ctx: RunContext[AgentDeps], out: Any) -> Any:
        st = ctx.deps.state
        claimed_requests = set(out.approvals_awaiting_human if isinstance(out, BossDecision) else out.approval_request_ids)
        claimed_drafts = set(out.customer_draft_ids if isinstance(out, BossDecision) else out.draft_ids)
        if isinstance(out, BossDecision):
            # The Boss may also cite requests that were already pending before this run (it saw them via tools).
            unknown = claimed_requests - st.request_ids - st.known_pending_ids
        else:
            unknown = claimed_requests - st.request_ids
        if unknown:
            raise ModelRetry(f"Approval request ids {sorted(unknown)} were not returned by any tool this run. Report only real ids.")
        if bad := claimed_drafts - st.draft_ids - st.known_draft_ids:
            raise ModelRetry(f"Draft ids {sorted(bad)} were not returned by any tool. Report only drafts that exist.")
        if isinstance(out, SpecialistReport):
            out.agent = ctx.deps.agent
            return out
        if st.ticket_id is not None and out.ticket_id != st.ticket_id:
            raise ModelRetry(f"This run is for ticket {st.ticket_id}, not {out.ticket_id}.")
        if out.final_status == "resolved" and out.approvals_awaiting_human:
            raise ModelRetry("A ticket can't be resolved while payments wait on a human. Use 'waiting_on_approval'.")
        if ctx.deps.depth == 0 and st.ticket_id is not None and st.ticket_id not in st.tickets_updated:
            raise ModelRetry(f"Call update_ticket_status for ticket {st.ticket_id} with your final status before finishing.")
        return out

    return agent


# ---------------------------------------------------------------- the team

class Team:
    """Owns one Portkey client, one MCP server process, and the five agents. Use as `async with Team() as team:`."""

    def __init__(self) -> None:
        if not PORTKEY_API_KEY:
            raise RuntimeError("PORTKEY_API_KEY is not set (put it in the workspace .env).")
        self.client: AsyncOpenAI | None = None
        self.mcp = make_mcp_toolset()
        self.agents: dict[AgentName, Agent] = {}

    async def __aenter__(self) -> "Team":
        self.client = AsyncOpenAI(api_key=PORTKEY_API_KEY, base_url=PORTKEY_BASE_URL,
                                  default_headers={"x-portkey-api-key": PORTKEY_API_KEY, "x-portkey-provider": PORTKEY_PROVIDER})
        model = OpenAIResponsesModel(MODEL_NAME, provider=OpenAIProvider(openai_client=self.client))
        await self.mcp.__aenter__()
        self.agents = {role: build_agent(role, model, self.mcp) for role in AGENT_NAMES}
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.mcp.__aexit__(*exc)
        if self.client is not None:
            await self.client.close()

    @staticmethod
    def limits() -> UsageLimits:
        return UsageLimits(request_limit=TEAM_REQUEST_LIMIT, tool_calls_limit=TEAM_TOOL_CALLS_LIMIT,
                           total_tokens_limit=TEAM_TOTAL_TOKENS_LIMIT)

    async def run_agent(self, role: AgentName, prompt: str, deps: AgentDeps, usage: RunUsage | None = None) -> Any:
        """One agent loop, every step appended to the audit trail."""
        audit, where = deps.state.audit, {"agent": role, "depth": deps.depth, "chain": deps.chain}
        audit.add("agent_started", text=clip(prompt, 800), **where)
        started = time.perf_counter()
        stop = "error"
        try:
            async with self.agents[role].iter(prompt, deps=deps, usage=usage, usage_limits=self.limits()) as run:
                async for node in run:
                    if isinstance(node, CallToolsNode):
                        resp = node.model_response
                        served = resp.model_name or ""
                        deps.state.served_models.add(served)
                        if not served.startswith(MODEL_FAMILY):
                            audit.add("error", model_name=served, text=f"Model guard: served by '{served}', not {MODEL_FAMILY}.", **where)
                            raise RuntimeError(f"Model guard: response came from '{served}', only {MODEL_FAMILY} is allowed.")
                        text = " ".join(p.content for p in resp.parts if isinstance(p, TextPart)).strip()
                        calls = [p for p in resp.parts if isinstance(p, ToolCallPart)]
                        audit.add("model_response", model_name=served, input_tokens=resp.usage.input_tokens,
                                  output_tokens=resp.usage.output_tokens, stop_reason=resp.finish_reason,
                                  text=clip(text, 500) if text else None,
                                  result=f"tool calls: {[c.tool_name for c in calls]}" if calls else None, **where)
                        for c in calls:
                            if c.tool_name != "delegate":  # delegate logs its own 'delegation' step
                                audit.add("tool_call", tool_name=c.tool_name, tool_args=c.args_as_dict(), **where)
                    elif isinstance(node, ModelRequestNode):
                        for part in node.request.parts:
                            if isinstance(part, ToolReturnPart):
                                audit.add("tool_result", tool_name=part.tool_name,
                                          result=clip(part.model_response_str(), TOOL_RESULT_AUDIT_CHARS), **where)
                            elif isinstance(part, RetryPromptPart):
                                audit.add("validation_retry", tool_name=part.tool_name,
                                          result=clip(part.model_response(), 400), **where)
                output = run.result.output
            stop = "completed"
            audit.add("agent_finished", stop_reason=stop, duration_ms=int((time.perf_counter() - started) * 1000),
                      text=clip(getattr(output, "summary", None) or getattr(output, "decision", ""), 800),
                      result=clip(output.model_dump(), 1500), **where)
            return output
        except UsageLimitExceeded as exc:
            stop = "usage_limit"
            audit.add("agent_finished", stop_reason=stop, text=str(exc), **where)
            raise
        except Exception as exc:
            if stop == "error":
                audit.add("agent_finished", stop_reason="error", text=clip(f"{type(exc).__name__}: {exc}", 500), **where)
            raise

    async def _board_snapshot(self, ticket_id: int | None) -> tuple[set[int], set[int]]:
        """Approval requests and drafts that already exist for the ticket, so the Boss can cite them."""
        if ticket_id is None:
            return set(), set()
        t = await self.mcp.direct_call_tool("get_ticket", {"ticket_id": ticket_id})
        if not isinstance(t, dict) or not t.get("found"):
            return set(), set()
        return ({r["id"] for r in t.get("approval_requests", []) if r["status"] == "pending"},
                {d["id"] for d in t.get("drafts", [])})

    async def run_ticket(self, ticket_id: int, run_id: str | None = None) -> BossDecision:
        """The Boss works one ticket, delegating as needed. One shared budget for the whole team."""
        run_id = run_id or uuid.uuid4().hex[:12]
        audit = AuditLog(run_id, ticket_id)
        state = TeamState(run_id=run_id, ticket_id=ticket_id, audit=audit)
        state.known_pending_ids, state.known_draft_ids = await self._board_snapshot(ticket_id)
        audit.add("run_started", text=f"Boss works ticket {ticket_id} (model {MODEL_NAME}).")
        deps = AgentDeps(agent="boss", depth=0, chain=("boss",), state=state, team=self)
        usage = RunUsage()
        started, stop = time.perf_counter(), "error"
        try:
            decision = await self.run_agent("boss", (
                f"Work ticket {ticket_id}. Start with get_ticket({ticket_id}). Gather the facts, delegate to the right "
                "teammates, make the call, set the ticket status on the board, and return your BossDecision."), deps, usage=usage)
            stop = "completed"
            return decision
        except UsageLimitExceeded:
            stop = "usage_limit"
            raise
        finally:
            audit.add("run_finished", stop_reason=stop, duration_ms=int((time.perf_counter() - started) * 1000),
                      input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
                      result=f"requests={usage.requests} tool_calls={usage.tool_calls} delegations={state.delegations} "
                             f"served_models={sorted(state.served_models)}")

    async def decide_request(self, request_id: int, person: str, approve: bool, reason: str = "") -> dict:
        return await decide_request(self.mcp, request_id, person, approve, reason)


async def decide_request(mcp: MCPToolset, request_id: int, person: str, approve: bool, reason: str = "",
                         ticket_id: int | None = None) -> dict:
    """The HUMAN path (dashboard): approve-and-pay or reject a request through the MCP server.
    direct_call_tool skips the agent guard on purpose - this is a person, not an agent - and the server still
    checks the approval code, the balance, and the vendor block."""
    person = " ".join(person.split())[:60]
    if not person:
        raise ValueError("A human approver name is required.")
    audit = AuditLog(uuid.uuid4().hex[:12], ticket_id)
    if approve:
        result = await mcp.direct_call_tool("execute_approved_request", {
            "request_id": request_id, "approved_by": person, "approval_code": approval_code("execute", request_id, person)})
    else:
        result = await mcp.direct_call_tool("reject_request", {
            "request_id": request_id, "rejected_by": person, "approval_code": approval_code("reject", request_id, person),
            "reason": reason or "Rejected by a human."})
    audit.add("human_decision", agent="human", tool_name="execute_approved_request" if approve else "reject_request",
              tool_args={"request_id": request_id, "person": person}, result=clip(result, 600),
              stop_reason="ok" if isinstance(result, dict) and result.get("ok") else "refused")
    return result
