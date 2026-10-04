"""Append-only audit trail: output/audit_trail.json, a JSON list of AuditEntry records.

Each agent-loop step is appended as it happens, so nested delegations show up in the order they ran and a crash
mid-run still leaves everything up to that point. Writes are serialised with a lock and done by writing a temp file
and atomically replacing the old one. The file is never cleared by the app; a damaged file is set aside, not deleted.
"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from typing import Any

from config import AUDIT_PATH
from models import AuditEntry

_LOCK = threading.Lock()
_SECRETISH = re.compile(r"(?i)(sk-[A-Za-z0-9_\-]{8,}|approval_code['\"]?\s*[:=]\s*['\"]?[0-9a-f]{16,})")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def clip(value: Any, limit: int = 600) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    text = _SECRETISH.sub("[redacted]", text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def append(entries: list[AuditEntry]) -> None:
    with _LOCK:
        AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        existing: list = []
        if AUDIT_PATH.exists():
            try:
                existing = json.loads(AUDIT_PATH.read_text() or "[]")
            except json.JSONDecodeError:
                AUDIT_PATH.rename(AUDIT_PATH.with_suffix(f".corrupt-{int(datetime.now().timestamp())}.json"))
        existing.extend(e.model_dump(exclude_none=True) for e in entries)
        tmp = AUDIT_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(existing, indent=1))
        os.replace(tmp, AUDIT_PATH)


class AuditLog:
    """Numbers the steps of one run and appends each one immediately."""

    def __init__(self, run_id: str, ticket_id: int | None) -> None:
        self.run_id = run_id
        self.ticket_id = ticket_id
        self.step = 0

    def add(self, event: str, *, agent: str = "team", depth: int = 0, chain: tuple[str, ...] | list[str] = (),
            tool_args: dict | None = None, **fields: Any) -> None:
        self.step += 1
        if tool_args is not None:
            tool_args = {k: ("[redacted]" if k == "approval_code" else clip(v, 200)) for k, v in tool_args.items()}
        append([AuditEntry(timestamp=_now(), run_id=self.run_id, ticket_id=self.ticket_id, agent=agent, depth=depth,
                           chain=list(chain), step=self.step, event=event, tool_args=tool_args, **fields)])
