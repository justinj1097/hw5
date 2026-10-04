"""Paths, model, and limits for the Campus Customs agent team. Secrets come from the environment or the
workspace .env; nothing secret is hard-coded or printed."""

from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
HW5_DIR = BACKEND_DIR.parent
DATA_DIR = HW5_DIR / "data"
ORIGINAL_DB = DATA_DIR / "campus_customs.db"
WORKING_DB = Path(os.getenv("CAMPUS_CUSTOMS_DB", DATA_DIR / "campus_customs_new.db")).resolve()
PROMPTS_DIR = BACKEND_DIR / "prompts"
AUDIT_PATH = HW5_DIR / "output" / "audit_trail.json"
MCP_SERVER = HW5_DIR / "mcp_server" / "server.py"
MCP_PYTHON = sys.executable


def load_env() -> None:
    """Read KEY=VALUE lines from the nearest .env (backend/, HW5/, or up to the workspace root) without overriding real env vars."""
    for folder in (BACKEND_DIR, *BACKEND_DIR.parents):
        path = folder / ".env"
        if path.is_file():
            for line in path.read_text().splitlines():
                key, sep, value = line.partition("=")
                key = key.strip()
                if sep and key and not key.startswith("#") and key not in os.environ:
                    os.environ[key] = value.strip().strip('"').strip("'")
            return


load_env()

PORTKEY_API_KEY = os.getenv("PORTKEY_API_KEY", "")
PORTKEY_BASE_URL = "https://api.portkey.ai/v1"
PORTKEY_PROVIDER = "openai"

# The only model allowed in this homework. Not read from the environment on purpose, so no other
# model can sneak in; every response's served model name is checked against MODEL_FAMILY at runtime.
MODEL_NAME = "gpt-6-luna"
MODEL_FAMILY = "gpt-6-luna"

# Signs human approvals. Shared with the MCP server process through its environment; never given to a model.
APPROVAL_SECRET = os.getenv("CC_APPROVAL_SECRET") or secrets.token_hex(32)

# ---- Token / loop limits (one budget shared by every agent in a ticket run) ----
MAX_DELEGATION_DEPTH = 2          # Boss (0) -> specialist (1) -> one more hop (2)
MAX_DELEGATIONS_PER_RUN = 8       # total delegate() calls across the whole team for one ticket
TEAM_REQUEST_LIMIT = 40           # model requests, all agents combined
TEAM_TOOL_CALLS_LIMIT = 70        # tool calls, all agents combined
TEAM_TOTAL_TOKENS_LIMIT = 400_000 # input + output tokens, all agents combined
TOOL_RESULT_AUDIT_CHARS = 600     # how much of a tool result the audit trail keeps
REASONING_EFFORT = {"boss": "medium", "inventory": "low", "accounting": "medium",
                    "facilities": "low", "customer_service": "low"}
