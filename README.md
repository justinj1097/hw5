# HW5: Campus Customs Multi-Agent Operations

An agent team runs the back office of Campus Customs.
- **Five PydanticAI agents:** Boss, Inventory, Accounting, Facilities and Customer Service. Any agent can delegate to any other.
- **Shared tools:** every shop fact and action goes through one **FastMCP server** over a SQLite database.
- **Backend:** a **FastAPI** app runs the team.
- **Dashboard:** a **React** board lets a person watch the agents and approve every payment.

Every agent uses **gpt-6-luna through Portkey**, and the code checks the served model name on every response.

```
React board :5173 ──▶ FastAPI backend/main.py :8000 ──▶ agent team (backend/agents.py) ──stdio──▶ MCP server (mcp_server/server.py) ──▶ data/campus_customs_new.db
```

## What's where

| Path | What it is |
| --- | --- |
| `data/campus_customs.db` | The **original** shop database. Never modified; used to reset |
| `data/campus_customs_new.db` | The **working copy**, the only file the MCP server and backend use. It's committed as it was left by the full three-ticket run (checking $152, all tickets resolved) |
| `mcp_server/` | The FastMCP server (20 tools) and its README |
| `backend/` | `main.py` (API routes), `agents.py` (the team and agent loop), `models.py` (data types), `prompts/*.md` (one prompt per agent), `config.py`, `audit.py`, `reset_db.py`, `run_team.py`, and `tests/` |
| `frontend/` | The React + Vite + TypeScript dashboard |
| `output/` | `harness.md` (full system description), `mcp_smoke.json`, `desk_tickets.html` (plan vs actual, cash, reflection), `design.md`, `resolved_tickets.json`, `resolved_board.html` (with screenshots), `audit_trail.json`, `github_url.txt` |
| `.mcp.json` | Connects Claude Code to the MCP server |
| `AI_prompts.md` | Log of the prompts used to build this |

## Setup (once)

You need Python 3.11+, Node 20+, and a Portkey API key.

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```
```bash
cp .env.example .env
```

Then put your key in `.env` as `PORTKEY_API_KEY=...`. The `.env` file is git-ignored and never committed.

```bash
cd frontend && npm install
```

## Getting a clean database

The working copy changes as tickets are resolved: payments, cash, invoices, the lease, and the board tables. To start clean, **copy the original over the working copy**, using any one of these:

```bash
cp data/campus_customs.db data/campus_customs_new.db && chmod u+w data/campus_customs_new.db
```
```bash
cd backend && ../.venv/bin/python reset_db.py
```

Or click **Reset shop** on the board, which calls `POST /api/reset`.

**Before a full three-ticket run, always reset first** (shop rule 7). The committed working copy is the state *after* the graded run, so reset before running again.

## Running it

**1. MCP server.** The backend starts it automatically over stdio, so you don't need to start it yourself for the app. To run it on its own, or to use it from Claude Code (`.mcp.json` points at it):

```bash
.venv/bin/python mcp_server/server.py
```

**2. FastAPI backend** (http://localhost:8000). Run it from `backend/`:

```bash
cd backend && ../.venv/bin/python -m uvicorn main:app --reload --port 8000
```

**3. React board** (http://localhost:5173). It calls the backend at `http://localhost:8000`, and the backend's CORS settings allow this origin:

```bash
cd frontend && npm run dev
```

## A full three-ticket run on the board

1. Click **Reset shop**. Checking should read **$3,400.00** and all three tickets **Open**.
2. Pick **101** and click **Send to the team**. Watch the live floor. When the run finishes, type your name under *Signing as* and approve the cheques in the tray: the invoice first, then the purchase order.
3. Do the same for **102** (rent).
4. Run **103**. If the Boss marks it **Blocked** (the 12-hoodie restock can't be funded), write your decision in the note box and click **Pin note & send back to the team**.
5. When every ticket shows **RESOLVED**, checking should be $3,400 − $840 − $8 − $2,400 = **$152.00**.

Command-line alternative (resets first, then works every open ticket; payments still have to be approved on the board):

```bash
cd backend && ../.venv/bin/python run_team.py --all --reset
```

## Tests (offline, no AI calls)

These check the MCP server's money rules on a temporary copy of the database: no payment without human approval, no overdrafts, the vendor-block rule, the table updates, and drafts never sent.

```bash
cd backend && ../.venv/bin/python -m pytest -q
```

## API routes

| Route | Does |
| --- | --- |
| `GET /api/tickets` | All tickets, open or resolved |
| `GET /api/tickets/{id}` | One ticket with its board history |
| `POST /api/tickets/{id}/run` | Start the agent team on a ticket (runs in the background) |
| `GET /api/runs/{run_id}` | A run's status and the Boss's decision |
| `GET /api/events` | Recent agent events: what each agent said and which tools it used |
| `GET /api/approvals` | Payment and purchase-order requests waiting on a person |
| `POST /api/approvals/{id}/approve` | A person approves a payment. The only route that moves cash |
| `POST /api/approvals/{id}/reject` | Decline a request |
| `POST /api/tickets/{id}/note` | Pin a manager decision for the Boss |
| `GET /api/cash` | Checking balance from `cash_accounts` |
| `POST /api/reset` | Reset the working copy from the original |

See `output/harness.md` for the tables, every MCP tool, the agents, the safety rules and the token limits.
