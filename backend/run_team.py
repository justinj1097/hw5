"""Run the agent team on tickets from the command line (the FastAPI backend and dashboard build on the same Team).

Run from backend/:
    ../.venv/bin/python run_team.py --ticket 102
    ../.venv/bin/python run_team.py --all --reset      # full run: reset the DB first (shop rule 7)
"""

from __future__ import annotations

import argparse
import asyncio
import json

from agents import Team
from reset_db import reset


async def main() -> None:
    parser = argparse.ArgumentParser(description="Campus Customs agent team")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--ticket", type=int, action="append", help="ticket id (repeatable)")
    group.add_argument("--all", action="store_true", help="every open ticket, oldest first")
    parser.add_argument("--reset", action="store_true", help="reset campus_customs_new.db to the original first")
    args = parser.parse_args()

    if args.reset:
        print("Database reset:", reset())
    async with Team() as team:
        ids = args.ticket
        if args.all:
            open_tickets = await team.mcp.direct_call_tool("list_open_tickets", {})
            ids = [t["id"] for t in open_tickets["tickets"]]
        for ticket_id in ids:
            decision = await team.run_ticket(ticket_id)
            print(json.dumps(decision.model_dump(), indent=2))


if __name__ == "__main__":
    asyncio.run(main())
