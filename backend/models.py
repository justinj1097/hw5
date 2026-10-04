"""Data types for the Campus Customs agent team: agent names, run state shared across delegations,
the structured reports agents must return, and the audit-trail record."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from audit import AuditLog

AgentName = Literal["boss", "inventory", "accounting", "facilities", "customer_service"]
AGENT_NAMES: tuple[AgentName, ...] = ("boss", "inventory", "accounting", "facilities", "customer_service")
TicketStatus = Literal["open", "in_progress", "waiting_on_approval", "blocked", "resolved"]


# ---------------------------------------------------------------- run state

@dataclass
class TeamState:
    """Shared by every agent working one ticket run (the Boss and everyone it delegates to)."""
    run_id: str
    ticket_id: int | None
    audit: "AuditLog"
    delegations: int = 0
    # ids that MCP tools actually returned during this run; agents may only report ids from these sets
    request_ids: set[int] = field(default_factory=set)
    draft_ids: set[int] = field(default_factory=set)
    tickets_updated: set[int] = field(default_factory=set)
    # what was already on the board for this ticket when the run started (the Boss may cite these)
    known_pending_ids: set[int] = field(default_factory=set)
    known_draft_ids: set[int] = field(default_factory=set)
    served_models: set[str] = field(default_factory=set)


@dataclass
class AgentDeps:
    """Per-agent deps: who I am, how deep in the delegation chain, and the shared run state."""
    agent: AgentName
    depth: int
    chain: tuple[AgentName, ...]
    state: TeamState
    team: Any  # the Team object (agents.Team) - used by the delegate tool to run another agent


# ---------------------------------------------------------------- agent outputs

class Fact(BaseModel):
    """One fact the agent relied on, with the MCP tool it came from (so a reviewer can check it)."""
    label: str = Field(description="What the fact is, e.g. 'CC-HOOD-NAVY M on hand'.")
    value: str = Field(description="The value exactly as the tool returned it, e.g. '8'.")
    source_tool: str = Field(description="Name of the MCP tool that returned it, e.g. 'check_stock'.")


class SpecialistReport(BaseModel):
    """What Inventory, Accounting, Facilities, and Customer Service hand back to whoever delegated to them."""
    agent: AgentName
    ticket_id: int | None = None
    summary: str = Field(description="2-4 sentences: what you found and what you did.")
    facts: list[Fact] = Field(default_factory=list, description="Numbers and dates you used, each from a tool.")
    actions_taken: list[str] = Field(default_factory=list, description="Board changes you made (requests, drafts, notes).")
    approval_request_ids: list[int] = Field(default_factory=list, description="Approval requests YOU created this run.")
    draft_ids: list[int] = Field(default_factory=list, description="Drafts YOU saved this run.")
    blockers: list[str] = Field(default_factory=list, description="What stops the ticket from being finished.")
    needs_human: bool = Field(description="True if a person must approve or decide something.")
    recommended_next_steps: list[str] = Field(default_factory=list)


class BossDecision(BaseModel):
    """The Boss's final call on one ticket."""
    ticket_id: int
    final_status: TicketStatus
    decision: str = Field(description="The call you made and why, in 2-5 sentences, citing the facts.")
    delegated_to: list[AgentName] = Field(default_factory=list)
    facts: list[Fact] = Field(default_factory=list)
    approvals_awaiting_human: list[int] = Field(default_factory=list, description="Approval request ids a person must decide.")
    customer_draft_ids: list[int] = Field(default_factory=list, description="Draft ids saved for this ticket (never sent).")
    blockers: list[str] = Field(default_factory=list)
    next_steps_for_human: list[str] = Field(default_factory=list)


class DelegationResult(BaseModel):
    """What the delegate tool returns to the agent that called it."""
    ok: bool
    to: AgentName
    report: dict[str, Any] | None = None
    error: str | None = None


# ---------------------------------------------------------------- audit trail

class AuditEntry(BaseModel):
    """One step of an agent loop in output/audit_trail.json."""
    timestamp: str
    run_id: str
    ticket_id: int | None
    agent: AgentName | Literal["team", "human"]
    depth: int
    chain: list[str]
    step: int
    event: Literal["run_started", "agent_started", "model_response", "tool_call", "tool_result", "delegation",
                   "validation_retry", "agent_finished", "run_finished", "error", "human_decision"]
    model_name: str | None = None
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    result: str | None = None
    text: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    stop_reason: str | None = None
    duration_ms: int | None = None
