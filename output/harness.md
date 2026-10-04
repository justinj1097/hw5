# Harness

How Campus Customs Multi-Agent Operations is built and controlled. Five PydanticAI agents (on gpt-6-luna through Portkey) work tickets using one FastMCP server over `data/campus_customs_new.db`. A FastAPI backend runs them, and a person watches and approves money on a React board.

**Quick map**

| Part | Where | Section |
| --- | --- | --- |
| Database tables (shop tables plus the board tables the MCP server adds) | `data/` | Problem 1 |
| MCP tools (20) and the tables each uses | `mcp_server/server.py` | Problems 2 and 4 |
| The five agents, delegation, agent loop, audit trail | `backend/agents.py`, `backend/prompts/`, `backend/models.py` | Problem 4 |
| API routes | `backend/main.py` | Problem 6 |
| Dashboard | `frontend/` | Problem 7 |
| Safety rules and token limits | code and prompts | Problem 4 (safety), plus the final rule list in Problem 8 |
| Full run: results and cash | `output/desk_tickets.html`, `output/resolved_tickets.json` | Problem 8 |

```
React board (5173) ──HTTP──▶ FastAPI main.py (8000) ──▶ Team: Boss ⇄ Inventory ⇄ Accounting ⇄ Facilities ⇄ Customer Service
        ▲                           │   (human approve / reject / note)        │  (every agent's tools)
        └──── polls events ◀── audit_trail.json ◀──────────────┘  └──stdio──▶ FastMCP server ──▶ campus_customs_new.db
```

## Problem 1: The database

- **Original (never modified):** `data/campus_customs.db`. It's read-only on disk (`chmod a-w`), so nothing can change it by accident.
- **Working copy:** `data/campus_customs_new.db`. The MCP server and backend read and write only this file.
- **Reset:** copy the original over the working copy. This is required before every full run.

### Tables, fields, and why each matters to the agents

**`desk`** (1 row): `date_today`, `notes`
The shop's "today" (**2026-08-31**). Every overdue or due-soon decision must use this date, never the computer's clock.

**`tickets`** (3 rows): `id`, `type`, `requester`, `subject`, `sku`, `size`, `qty`, `lease_id` → `leases.id`, `invoice_id` → `invoices.id`, `status`, `notes`, `created_at`
The work queue. The Boss reads each ticket to decide who handles it. `type` hints at the department, the link columns point to the records involved, and `status` tracks progress on the board.

**`inventory`** (10 rows, key `sku` + `size`): `sku`, `name`, `size`, `qty`, `location`
Stock by SKU and size. Inventory uses it to see whether an order can be filled now, and how large any shortfall is.

**`pricing`** (4 rows, key `sku`): `sku`, `unit_cost`, `list_price`
Cost and price per SKU. Accounting uses it to check a discount's margin, and to price a purchase order (`unit_cost` × quantity).

**`vendors`** (3 rows): `id`, `name`, `specialty`, `lead_days`
Who can restock what, and how long it takes. `specialty` decides which vendor fits an item. `lead_days` gives the restock date (rule 2: lead times come from here).

**`invoices`** (1 row): `id`, `vendor_id` → `vendors.id`, `amount`, `due_date`, `status`, `description`
Bills owed to vendors. An `open` invoice is unpaid, and it's overdue when `due_date` is before `desk.date_today`. Rule 3: a vendor won't ship new product while it has an open unpaid invoice, so this table can block a restock.

**`payments`** (0 rows): `id`, `kind`, `ref_id`, `amount`, `account`, `paid_at`, `approved_by`
The record of money actually paid out. Each payment must name the human who approved it (`approved_by`, rule 4) and point to what it paid: `kind` plus `ref_id`, such as an invoice or lease id. It starts empty.

**`cash_accounts`** (1 row): `name`, `balance`, `date`
Available cash: `checking` holds **$3,400**. Accounting checks this before proposing any payment. The pay tool must refuse rather than overdraw (rule 5), and money only goes out (rule 6).

**`leases`** (1 row): `id`, `space_name`, `landlord`, `monthly_rent`, `next_due`, `notes`
The shop space. Facilities uses it for rent amounts and due dates. Paying rent should move `next_due` forward a month.

**Board tables.** The MCP server creates these in the working copy on its first write. A reset removes them, because the original file doesn't have them.

| Table | Fields | Why it matters |
| --- | --- | --- |
| `approval_requests` | `id`, `ticket_id`, `kind` (invoice / rent / purchase_order), `ref_id`, `vendor_id`, `sku`, `size`, `qty`, `amount`, `reason`, `requested_by`, `status` (pending / executed / rejected), `shop_date`, `created_at`, `decided_by`, `decided_at`, `payment_id`, `decision_note` | The approval tray. Agents can only add `pending` rows; only a person turns one into a payment |
| `purchase_orders` | `id`, `request_id`, `vendor_id`, `sku`, `size`, `qty`, `unit_cost`, `total`, `ordered_on`, `eta` | Restocks that were actually ordered after approval. ETA is the shop date plus `lead_days` |
| `drafts` | `id`, `ticket_id`, `recipient`, `subject`, `body`, `drafted_by`, `status` ('draft'), `created_at` | Messages that stay on the board and are never sent (rule 8) |
| `ticket_updates` | `id`, `ticket_id`, `author`, `status`, `note`, `created_at` | The board log: agent notes, status changes, payments, and manager notes |

### The three open tickets and how they link

| Ticket | What it asks | Linked records | What the data shows |
| --- | --- | --- | --- |
| **101** `customer_order` · Tauhid Zaman · "Bulldog tee" | 1 × `CC-TEE-WHITE` size **S** | `inventory` (CC-TEE-WHITE, S); `pricing`; **`invoice_id` 501** → `invoices` → `vendors` 1 | **0 in stock** in S (other sizes: M 5, L 3, XL 2). The restock vendor is **Bulldog Print Co** ("apparel reprint", 5-day lead time), and the ticket links to its **invoice 501, $840, due 2026-08-28, still `open`**: 3 days overdue. Under rule 3 that unpaid invoice blocks the reprint, so the order can't be filled until it's paid, with human approval. A restock ordered on 08-31 arrives 2026-09-05 at the earliest. |
| **102** `rent_notice` · Elm City Properties · "Rent due" | Rent payment | **`lease_id` 1** → `leases` | Chapel Street shop, **$2,400**, `next_due` **2026-09-02**: due in 2 days, not yet overdue. It needs a payment with human approval, after which `payments` gains a row, `checking` drops, and `next_due` should move to 2026-10-02. |
| **103** `price_override` · Yale AI Club · "Bulk hoodie discount" | 20 × `CC-HOOD-NAVY` size **M**, bulk discount | `inventory` (CC-HOOD-NAVY, M); `pricing`; no vendor or invoice link | Only **8 in stock** in M, a shortfall of **12**. Cost is $22 and list price $58, so the margin is 62% at list. Any discount must keep a margin above cost; even at cost the price couldn't go below $22. Restocking hoodies would also mean **Bulldog Print Co** (apparel), which is blocked by the same invoice 501. |

### What links the tickets

- **Cash is the shared constraint.** Paying invoice 501 ($840) and the rent ($2,400) uses $3,240 of the $3,400, leaving **$160**. That has to cover any restock purchase order (12 hoodies × $22 = $264 on its own would be too much). Since no money comes in (rule 6), the agents can't fund everything, and the pay tool must refuse anything that would overdraw.
- **One vendor blocks two tickets.** Paying invoice 501 unlocks both the tee reprint (101) and the hoodie restock (103).
- **Every payment waits for a human.** The agents can only prepare payments and purchase orders. A person approves them on the dashboard before any cash moves.

## Problem 2: MCP server tools

`mcp_server/server.py` is a FastMCP server, and every agent on the team uses it as its tool box.

- **Database:** it opens only `data/campus_customs_new.db`, and it exits if pointed at the original.
- **Read-only for now:** these three tools open SQLite with `mode=ro`, so they can't change data.
- **No invented data:** a missing row returns `found: false` with an error rather than a guess.
- **Dates:** "overdue" and "days until due" are always measured from `desk.date_today`.

| Tool | Tables it reads | Ticket it unlocks | Why it's the right tool for that ticket |
| --- | --- | --- | --- |
| `check_stock(sku, size, qty_needed)` | `inventory`, `pricing` | **103** (Yale AI Club, 20 × CC-HOOD-NAVY M at a bulk discount). It also confirms 101's tee is at 0. | Ticket 103 depends on two numbers the agents mustn't guess. The tool returns `on_hand` 8, `shortfall` 12 and `can_fill` false, so the club can't get all 20 today. It also returns `unit_cost` $22 against `list_price` $58, so Accounting can offer a discount without dropping below cost. |
| `check_vendor_invoices(invoice_id=501)` | `vendors`, `invoices`, `desk` | **101** (Tauhid Zaman's white tee, size S) | Ticket 101 links to invoice 501. This tool follows that link to Bulldog Print Co and reports $840 due 2026-08-28, 3 days overdue against `desk.date_today`, with `can_ship` false. That's the rule-3 block keeping the size-S reprint off the shelf. It also returns the 5-day `lead_days`, so once the invoice is paid the agents can promise a real restock date. |
| `get_rent_due(lease_id=1)` | `leases`, `desk`, `cash_accounts` | **102** (Elm City Properties rent notice) | Ticket 102 links to lease 1, and this tool reads the actual rent ($2,400), the `next_due` date (2026-09-02, 2 days from shop "today", not overdue), and whether checking ($3,400) covers it. Facilities can then confirm the email's claim and queue a payment for human approval, without paying anything itself. |

## Problem 4: The agent team

### Layout

| File | What it holds |
| --- | --- |
| `backend/prompts/boss.md`, `inventory.md`, `accounting.md`, `facilities.md`, `customer_service.md` | One detailed prompt per agent: role and scope, its tools, the shop rules it enforces, a step-by-step procedure, and its limits |
| `backend/models.py` | `AgentName`, `TeamState` and `AgentDeps` (run state shared across delegations), `Fact`, `SpecialistReport`, `BossDecision`, `DelegationResult`, `AuditEntry` |
| `backend/agents.py` | The five PydanticAI agents, the per-role MCP tool allowlists, the `delegate` tool, the audited agent loop, the model guard, and `Team` (`run_ticket`, plus `decide_request` for humans) |
| `backend/audit.py` | The append-only `output/audit_trail.json` writer |
| `backend/config.py` | Paths, `MODEL_NAME = "gpt-6-luna"`, the Portkey settings, and the token and loop limits |
| `backend/run_team.py` / `backend/reset_db.py` | Command-line runner (`--ticket 102`, `--all --reset`) and the rule-7 reset |
| `backend/tests/test_mcp_rules.py` | 7 offline tests showing the MCP server enforces the money rules itself |

### Agents

Every agent runs on **gpt-6-luna through Portkey**. Specialists return a `SpecialistReport`; the Boss returns a `BossDecision`. Any agent can `delegate(to, task)` to any other.

| Agent | Job | MCP tools it can use (besides the common five*) |
| --- | --- | --- |
| **Boss** | Reads the ticket, decides who works on it, weighs the whole cash picture, makes the final call, and sets the board status | `update_ticket_status`, `check_stock`, `list_vendors`, `check_vendor_invoices`, `get_rent_due`, `get_cash_position`, `check_discount`, `list_payments` |
| **Inventory** | Stock by SKU and size, shortfalls, which vendor fits, whether it can ship, restock cost and arrival date | `check_stock`, `list_vendors`, `check_vendor_invoices`, `estimate_restock` |
| **Accounting** | Cash, invoices, margins, discounts. The **only** role that can file payment and purchase-order requests | `get_cash_position`, `check_vendor_invoices`, `list_vendors`, `check_discount`, `list_payments`, `get_rent_due`, `estimate_restock`, `check_stock`, `request_payment_approval`, `request_purchase_order` |
| **Facilities** | Lease, rent and landlord matters. Checks the notice against the lease and hands the payment to Accounting | `get_rent_due`, `get_cash_position`, `save_draft_message` |
| **Customer Service** | Honest drafts to customers and student orgs, saved on the board and never sent | `check_stock`, `check_discount`, `save_draft_message` |

\*Common to all five: `get_shop_today`, `list_open_tickets`, `get_ticket`, `add_ticket_note`, `list_approval_requests`.

**How delegation works (full connectivity).** Every agent has the same `delegate(to, task)` tool. It runs the target agent's own loop, with its own prompt and allowlist, and returns that agent's structured report. The guards:
- an agent can't delegate to itself, or to anyone already up its chain (which would create a loop);
- delegation goes at most 2 levels deep (Boss → specialist → one more);
- each ticket allows at most 8 delegations;
- every agent in a ticket run draws on one shared usage budget.

**The agent loop.** `Team.run_agent` drives `agent.iter()`:
- **Model responses:** the served model name is checked against `gpt-6-luna`; anything else stops the run. Text, tool calls, tokens and finish reason are recorded.
- **Tool calls and results:** recorded as they happen.
- **Output validator:** sends the agent back to fix its answer if it:
  - reports approval-request or draft ids that no tool returned;
  - marks a ticket `resolved` while approvals are pending;
  - finishes as Boss without writing the ticket's status to the board.

### MCP tools (`mcp_server/server.py`) and the tables they use

| Tool | Tables | Used by |
| --- | --- | --- |
| `get_shop_today` | desk | all |
| `list_open_tickets` | tickets | all |
| `list_tickets` | tickets, approval_requests, drafts | the dashboard API (`GET /api/tickets`) |
| `get_ticket` | tickets, leases, invoices, vendors, ticket_updates, approval_requests, drafts | all |
| `update_ticket_status` | tickets (writes), approval_requests (reads), ticket_updates (writes) | Boss |
| `add_ticket_note` | ticket_updates (writes) | all, plus the manager-note route |
| `check_stock` | inventory, pricing | Boss, Inventory, Accounting, Customer Service |
| `list_vendors` | vendors, invoices, desk | Boss, Inventory, Accounting |
| `check_vendor_invoices` | vendors, invoices, desk | Boss, Inventory, Accounting |
| `estimate_restock` | vendors, inventory, pricing, invoices, cash_accounts, approval_requests, desk | Inventory, Accounting |
| `get_rent_due` | leases, cash_accounts, desk | Boss, Accounting, Facilities |
| `get_cash_position` | cash_accounts, invoices, vendors, leases, approval_requests, desk | Boss, Accounting, Facilities |
| `check_discount` | pricing | Boss, Accounting, Customer Service |
| `list_payments` | payments | Boss, Accounting |
| `list_approval_requests` | approval_requests | all |
| `request_payment_approval` | invoices or leases, cash_accounts, desk (read); approval_requests, ticket_updates (write) | Accounting |
| `request_purchase_order` | vendors, inventory, pricing, invoices, cash_accounts (read); approval_requests, ticket_updates (write) | Accounting |
| `save_draft_message` | drafts, ticket_updates (write) | Facilities, Customer Service |
| `execute_approved_request` | **Human only.** approval_requests, invoices, leases, vendors, pricing, cash_accounts, desk (read); payments, cash_accounts, invoices / leases / purchase_orders, approval_requests, ticket_updates (write) | the dashboard, never an agent |
| `reject_request` | approval_requests, ticket_updates | the dashboard, never an agent |

**Board tables.** `approval_requests`, `purchase_orders`, `drafts` and `ticket_updates` are created by the MCP server in the working copy. They disappear when `reset_db.py` copies the original back.

**What happens when a person approves a payment.** `execute_approved_request`:
1. adds a `payments` row with `approved_by` set to the person's name;
2. lowers `cash_accounts.balance`;
3. updates what was paid for:
   - **invoice:** `invoices.status` becomes `'paid'`;
   - **rent:** `leases.next_due` moves forward one month;
   - **purchase order:** a `purchase_orders` row is added, with an ETA of today plus `lead_days`.

### Audit trail (`output/audit_trail.json`)

Every agent-loop step is appended as it happens: one JSON record per step, under a lock, with an atomic file replace. The file is never wiped; a damaged file is set aside rather than deleted.

Each record has:
- `timestamp`, `run_id`, `ticket_id`;
- `agent`, `depth`, `chain` (for example `["boss","facilities","accounting"]`), `step`;
- `event`: `run_started`, `agent_started`, `model_response`, `tool_call`, `tool_result`, `delegation`, `validation_retry`, `agent_finished`, `run_finished`, `error` or `human_decision`;
- `model_name` (the model that actually served the response);
- `tool_name` and `tool_args` (shortened);
- `result` (shortened to 600 characters);
- `input_tokens` / `output_tokens`, `stop_reason`, `duration_ms`.

Approval codes and key-like strings are redacted. `run_finished` records the totals: requests, tool calls, delegations, and every model name that served the run.

**Test run.** The team ran once on ticket 102 (rent):
- Boss → Facilities and Accounting, and Facilities → Accounting at depth 2;
- Accounting filed approval request 1 for $2,400 rent; nothing was paid;
- Boss set ticket 102 to `waiting_on_approval`;
- 85 audit steps, 17 model requests, 27 tool calls, about 63k input and 4k output tokens, 41 seconds;
- every response was served by `gpt-6-luna-global`.

That run showed Boss and Facilities both bringing in Accounting, so the Boss prompt now gives each sub-task one owner. The database was reset afterwards.

### Safety

**Guardrails for real customers and real money**
- **Money only moves with a person's approval, and the server enforces it.**
  - Agents can only *file* requests.
  - The pay and reject tools are left off every agent's allowlist, and a wrapper (`process_tool_call`) blocks them a second time.
  - The MCP server refuses them unless the call carries an HMAC approval code tied to the request id and the approver's name. Only the backend holds the key for that code, and it never appears in any prompt. So even Claude Code, connected through `.mcp.json`, can't pay anything.
- **Amounts come from the database, not the model.** Request amounts are read from the invoice, lease or pricing row, and read again at payment time. If an amount changed in between, the payment is refused.
- **No overdrafts, no double payments.**
  - The pay tool refuses any payment that would push a balance below zero.
  - Duplicate pending requests are refused.
  - A paid invoice can't be paid again.
  - Each payment runs in a single database transaction (`BEGIN IMMEDIATE`).
- **The vendor-block rule is enforced in code.** A purchase order to a vendor with an open invoice can't be executed until that invoice is paid.
- **Nothing reaches customers automatically.** Messages are saved as drafts (`sent: false`); there's no email or phone tool. Customer Service is told not to share internal matters (vendor debts, cash, rent), not to quote a discount as final before the Boss approves it, and not to promise arrival dates for a blocked vendor.
- **No invented facts.**
  - Tools return `found: false` rather than guessing.
  - Reports must list the source tool for every fact.
  - The validator rejects any request or draft id that no tool returned.
- **Each role gets only the tools it needs.** Only Accounting can file money requests, and only Boss changes ticket status. The code, not the model, fills in the "requested by" and "drafted by" fields, so the board shows who really acted.
- **Data safety.**
  - The original database is read-only, and the server refuses to open it.
  - Read tools open SQLite in read-only mode.
  - The MCP server process doesn't receive the Portkey key.
  - Approval codes are redacted from the audit trail.
- **Accountability.** The append-only audit trail and the `ticket_updates` log record every step, every tool call, every model that answered, and the name of the person who approved each payment.

**Limits that keep token use in check** (`backend/config.py`). One budget is shared by every agent working a ticket:
- **Model requests:** 40 per ticket run (`UsageLimits.request_limit`).
- **Tool calls:** 70 per run.
- **Total tokens:** 400,000 per run. Going over stops the run, and the stop is logged.
- **Delegations:** at most 2 levels deep and 8 per ticket. No calling yourself and no loops back up the chain.
- **Reasoning effort:** medium for Boss and Accounting, low for Inventory, Facilities and Customer Service.
- **Per-agent tool-call targets** in each prompt: about 4 to 12.
- **Short audit records:** tool results are cut to 600 characters, and the MCP tools return compact JSON.
- **One owner per sub-task**, so two teammates don't fetch the same facts.
- **Model guard:** any response not served by `gpt-6-luna` stops the run immediately, so no other model can run up the bill.

## Problem 6: Backend API (`backend/main.py`)

Start it from `backend/` with `uvicorn main:app --reload --port 8000`. It serves http://localhost:8000, and CORS lets the dashboard at http://localhost:5173 call it.

Every shop read and every approval goes through the MCP server: the app keeps one stdio connection to it open the whole time it runs. `main.py` has no SQL of its own.

### Routes

| Method & URL | What it does |
| --- | --- |
| `GET /api/tickets` | **(1)** Lists every ticket with `is_open` (open vs resolved), its board status, and how many pending approvals and drafts it has |
| `GET /api/tickets/{id}` | Shows one ticket with its linked lease or invoice and its board history (notes, approval requests, drafts) |
| `POST /api/tickets/{id}/note` `{"author": "Name", "note": "..."}` | A person pins a decision on a ticket (MCP `add_ticket_note`, author "Name (manager)"). The Boss follows it on the next run. Used to settle blocked ticket 103. It never moves money |
| `POST /api/tickets/{id}/run` | **(2)** Starts the agent team (the Boss, plus whoever it delegates to) on that ticket in the background. Returns `202` with a `run_id`; returns `409` if a run is already going |
| `GET /api/runs/{run_id}` · `GET /api/runs` | Shows a run's status (`running` / `completed` / `failed`) and the Boss's final decision · lists all runs since the server started |
| `GET /api/events?limit=&ticket_id=&run_id=&after=` | **(3)** Recent agent events from the audit trail: agent, depth and chain, what the agent said, which tool it called with which arguments, the result, the serving model, and tokens. Pass `after=<latest>` to fetch only new events while the board refreshes |
| `GET /api/approvals?status=pending` | Lists the payment and purchase-order requests the agents prepared |
| `POST /api/approvals/{id}/approve` `{"approved_by": "Name"}` | **(4)** A person clicked Approve, so pay the request. **The only route that changes cash.** It: records a `payments` row with `approved_by`; lowers `cash_accounts`; marks the invoice paid, moves the lease's next due date, or records the purchase order; and moves the ticket back to `in_progress` once nothing else is pending. Returns `409` when the server refuses (not enough cash, vendor still blocked, already paid) |
| `POST /api/approvals/{id}/reject` `{"rejected_by": "Name", "reason": "..."}` | Declines a request; no money moves |
| `GET /api/cash` | **(5)** The current checking balance from `cash_accounts`, plus pending totals, open invoices, and payments made |
| `POST /api/reset` | **(6)** Copies the original `campus_customs.db` over the working copy (rule 7) and clears the board tables. The audit trail is kept. Returns `409` during a run |
| `GET /api/health` | Liveness check, plus the model and database names |

**Tested against the running server:**
1. Reset, then the tickets list showed 101–103 all `open`, and cash was $3,400.
2. `POST /api/tickets/102/run` came back in under a second; the run finished in about 55 seconds. Path: Boss → Facilities → Accounting, 14 model requests, all served by `gpt-6-luna-global`. A second run, or a reset, during that time got `409`.
3. Approving request 1 (rent $2,400, approved by "Justin"):
   - checking went from $3,400 to $1,000;
   - `payments` gained a row with `approved_by` set;
   - `leases.next_due` moved to 2026-10-02.

   Approving it again got `409`; leaving the name blank got `422`.
4. The database was reset afterwards.

## Problem 7: The dashboard (`frontend/`)

- **Stack:** React + Vite + TypeScript. Start it from `frontend/` with `npm run dev` and open http://localhost:5173.
- **Backend calls:** `src/api.ts` talks directly to `http://localhost:8000`. `backend/main.py` allows the `http://localhost:5173` and `http://127.0.0.1:5173` origins through CORS.
- **Polling:**
  - tickets, cash and approvals every 6 seconds, or every 2.5 seconds during a run;
  - `/api/events?run_id=…&after=<latest>` every 1.2 seconds while a run is live;
  - `/api/runs/{id}` every 1.5 seconds until the run finishes.
- **Payee names:** `GET /api/approvals` now adds `payee` and `what` to each request, so cheques can say who gets paid. The names come from MCP `list_vendors` and `get_cash_position`.
- **When a ticket resolves:** when the last pending approval on a `waiting_on_approval` ticket is paid, `execute_approved_request` sets the ticket to `resolved`. A rejection sends it back to `in_progress`. The Boss prompt explains this.

**Tested in the browser:**
1. Clicked *Send to the team* on ticket 101. The feed and floor map updated live; the run took 21 model calls, 28 tool calls, 3 hand-offs and 83k tokens.
2. The path matched the Problem 5 plan: Boss → Inventory → Accounting and Boss → Customer Service, with Facilities on the bench. The tray then showed two cheques: invoice 501 for $840, and a purchase order for $8 flagged "vendor still has unpaid invoice 501".
3. Approving the purchase order first was refused under rule 3, and the reason appeared on the cheque.
4. Approving the invoice, then the purchase order: checking counted down from $3,400 to $2,552, both payments appeared on the register tape signed "Justin", and ticket 101 was stamped RESOLVED.
5. *Reset shop* restored $3,400 and three open tickets.

## Problem 8: Full run and final reference

### The full run (reset, then 101 → 102 → 103)

1. **Reset first (rule 7).** Done with the board's *Reset shop* button. The working copy's md5 then matched the original (`46effb90…`). Starting checking balance: **$3,400.00**.
2. **The board drove everything.** Each ticket was started with *Send to the team*, and each payment was signed in the approval tray by "Justin". 103 also needed a manager note.

| Ticket | Team runs (path) | Human step | Cash | Final |
| --- | --- | --- | --- | --- |
| 101 | Boss → Inventory → Accounting; Boss → Customer Service. 21 model calls, 27 tool calls | Approved request 1 (invoice 501, $840), then request 2 (purchase order, $8) | −$848.00 → $2,552.00 | resolved |
| 102 | Boss → Facilities → Accounting. 16 model calls, 17 tool calls | Approved request 3 (rent, $2,400) | −$2,400.00 → $152.00 | resolved |
| 103 | Run 1: Boss → Inventory, Accounting, Customer Service ×2 → `blocked` ($264 restock > $152). Run 2: Boss → Customer Service | Manager note: no restock, offer the 8 on hand at $52.20, close | $0.00 → $152.00 | resolved |

- **Totals:** 4 team runs, 69 model calls, 81 tool calls, 10 delegations, about 244k input and 14k output tokens. Every response was served by `gpt-6-luna-global`.
- **Ending checking balance: $152.00.** That equals `cash_accounts.balance` in the working database: 3,400 − 840 − 8 − 2,400 = 152, and `SUM(payments.amount)` = 3,248.
- **The full comparison** of plan vs actual, and the itemized cash, are in `output/desk_tickets.html`. Per-ticket outcomes are in `output/resolved_tickets.json`. Board screenshots are in `output/resolved_board.html`. The audit trail kept every step: earlier test runs plus this run's 285 new entries.

**What the run taught (and what changed because of it)**
- **Blocked tickets need a channel for a human decision.** I added `POST /api/tickets/{id}/note`, the manager-note box on the board, and a Boss rule to follow "(manager)" notes.
- **Parallel hand-offs can run ahead of the facts.** The Boss sent Customer Service at the same time as Inventory on 101, and before Accounting's price on 103. That gave vaguer drafts and an extra draft on 103. A further prompt tweak should tell the Boss to ask Customer Service *after* the facts are in.
- **Human approvals now carry their `ticket_id`** in the audit trail, so a reviewer can follow ticket → request → payment → approver.

### Final safety rules (all in force)

| Rule | Enforced where |
| --- | --- |
| Only `gpt-6-luna` through Portkey | `config.MODEL_NAME` is fixed, and the served model name is checked on every response; any other name stops the run |
| "Today" is `desk.date_today` | Every date calculation in the MCP tools, and every prompt |
| Lead times come from `vendors.lead_days` | `estimate_restock`, `request_purchase_order`, and `execute_approved_request` (purchase-order ETA) |
| A vendor with an open invoice won't ship | `execute_approved_request` refuses the purchase order; `estimate_restock` and `list_vendors` flag it. Tested live on 101 |
| Payments need a person | Agents only file requests; pay and reject are left off every agent's toolset, a wrapper blocks them, and the server requires an HMAC approval code plus an approver's name. `payments.approved_by` records the name |
| Paying updates the related tables | `payments`, `cash_accounts`, plus `invoices.status`, `leases.next_due` or `purchase_orders`, plus `approval_requests` and the ticket status, all in one transaction |
| No negative balances | `execute_approved_request` refuses overdrafts; request tools refuse anything bigger than the balance |
| Cash only goes out | No tool adds money; prompts tell agents not to count on revenue |
| Reset before a full run | `POST /api/reset`, the board button, or `backend/reset_db.py`; refused while a run is going |
| No outreach | There's no send tool. Drafts are saved with `status='draft'`, and the board labels them "DRAFT · NOT SENT" |
| No invented data | Tools return `found: false` rather than guessing; reports must cite the tool for each fact; the validator rejects request or draft ids that no tool returned |
| Token limits | Per ticket run: 40 model requests, 70 tool calls, 400k tokens; at most 2 levels and 8 delegations; no loops |
| Manager notes are instructions, not money | The note route only writes `ticket_updates`; payments still go through approve |
| Accountability | Append-only `audit_trail.json`, the `ticket_updates` log, and an approver name on every payment |

### How to run everything

```bash
cd backend && ../.venv/bin/python -m uvicorn main:app --reload --port 8000
cd frontend && npm run dev                                   # http://localhost:5173
cd backend && ../.venv/bin/python -m pytest -q               # 8 offline rule tests
cd backend && ../.venv/bin/python run_team.py --all --reset  # command-line alternative to the board
```
