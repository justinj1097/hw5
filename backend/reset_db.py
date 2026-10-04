"""Shop rule 7: reset the working database to the original values before a full run.

Copies data/campus_customs.db (never modified) over data/campus_customs_new.db. The board tables the MCP server
adds (approval_requests, drafts, purchase_orders, ticket_updates) disappear with it. The audit trail is NOT touched.
Run from backend/:  ../.venv/bin/python reset_db.py
"""

from __future__ import annotations

import hashlib
import shutil

from config import ORIGINAL_DB, WORKING_DB


def _md5(path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def reset() -> str:
    if WORKING_DB.resolve() == ORIGINAL_DB.resolve():
        raise SystemExit("Working DB path points at the original - refusing.")
    for suffix in ("-wal", "-shm", "-journal"):
        WORKING_DB.with_name(WORKING_DB.name + suffix).unlink(missing_ok=True)
    shutil.copyfile(ORIGINAL_DB, WORKING_DB)  # copyfile: the copy is writable even though the original is read-only
    if _md5(WORKING_DB) != _md5(ORIGINAL_DB):
        raise SystemExit("Reset failed: working copy doesn't match the original.")
    return _md5(WORKING_DB)


if __name__ == "__main__":
    print(f"Reset {WORKING_DB.name} from {ORIGINAL_DB.name} (md5 {reset()}).")
