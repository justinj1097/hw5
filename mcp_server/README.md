# Campus Customs MCP server

The shared tool box for the Campus Customs agent team. The Boss, Inventory, Accounting, Facilities and Customer Service agents all reach shop data through these tools, and nothing else. The dashboard's human-approval path uses them too. The server is built with [FastMCP](https://gofastmcp.com) and talks over stdio.

**Database:** `data/campus_customs_new.db`, the working copy. The server never opens the original `data/campus_customs.db`, which stays untouched for resets (`backend/reset_db.py`). Set `CAMPUS_CUSTOMS_DB` to use a different path. On first write, the server adds four board tables to the working copy: `approval_requests`, `purchase_orders`, `drafts` and `ticket_updates`.

**Ground rules the code enforces:**
- every value comes from the database, and a missing row returns `{"found": false, ...}`;
- "today" is `desk.date_today`;
- lead times come from `vendors.lead_days`;
- a vendor with an open invoice can't be sent a purchase order;
- money only moves through the human-approval tool, which refuses overdrafts;
- messages are saved as drafts and never sent.

## Tools

### Reading
| Tool | Reads | Returns |
| --- | --- | --- |
| `get_shop_today()` | desk | The shop's "today" and the desk notes |
| `list_open_tickets()` | tickets | Every ticket that isn't resolved |
| `list_tickets()` | tickets, approval_requests, drafts | Every ticket with `is_open` and counts of pending approvals and drafts (used by the dashboard API) |
| `get_ticket(ticket_id)` | tickets, leases, invoices, vendors, board tables | One ticket, its linked lease or invoice and vendor, and its notes, requests and drafts |
| `check_stock(sku, size, qty_needed?)` | inventory, pricing | On hand, other sizes, `can_fill` / `shortfall`, unit cost and list price |
| `list_vendors()` | vendors, invoices | Specialty, `lead_days`, open invoices, and `can_ship` for each vendor |
| `check_vendor_invoices(vendor_id? \| invoice_id?)` | vendors, invoices, desk | One vendor's open invoices with days overdue, and `can_ship` |
| `estimate_restock(vendor_id, sku, size, qty)` | vendors, inventory, pricing, invoices, cash_accounts | Restock cost, arrival date, whether the vendor is blocked, and whether cash covers it |
| `get_rent_due(lease_id)` | leases, cash_accounts, desk | Rent, `next_due`, days until due, and whether checking covers it |
| `get_cash_position()` | cash_accounts, invoices, leases, approval_requests | Balances, open invoices, upcoming rent, pending requests, and what's left after them |
| `check_discount(sku, qty, unit_price? \| discount_pct?)` | pricing | Price, total, margin, and whether it's at or below cost |
| `list_payments()` | payments | Payments already made, with the approver for each |
| `list_approval_requests(status)` | approval_requests | Requests by status (pending by default) |

### Writing to the board (agents)
| Tool | Writes | Notes |
| --- | --- | --- |
| `update_ticket_status(ticket_id, status, note, updated_by)` | tickets, ticket_updates | Refuses `resolved` while approvals are pending |
| `add_ticket_note(ticket_id, note, author)` | ticket_updates | |
| `request_payment_approval(kind, ref_id, reason, requested_by, ticket_id?)` | approval_requests | Invoice or rent. The amount comes from the database. **Doesn't pay.** |
| `request_purchase_order(vendor_id, sku, size, qty, reason, requested_by, ticket_id?)` | approval_requests | Cost is unit cost × quantity. Flags a vendor blocked by an open invoice. **Doesn't order.** |
| `save_draft_message(recipient, subject, body, drafted_by, ticket_id?)` | drafts | Saved as a draft, **never sent** |

### Human only (hidden from every agent)
| Tool | Writes | Notes |
| --- | --- | --- |
| `execute_approved_request(request_id, approved_by, approval_code)` | payments, cash_accounts, invoices / leases / purchase_orders, approval_requests, tickets | Needs an HMAC approval code that only the backend can make (`CC_APPROVAL_SECRET`). Refuses overdrafts, changed amounts, and blocked vendors. Moves the ticket back to `in_progress` once its last approval is done. |
| `reject_request(request_id, rejected_by, approval_code, reason)` | approval_requests | Same approval-code check |

## Run

From the `HW5/` folder:

```bash
.venv/bin/pip install -r mcp_server/requirements.txt
.venv/bin/python mcp_server/server.py
```

Offline tests of the rules above (they use a temporary copy of the database):

```bash
cd backend && ../.venv/bin/python -m pytest -q
```
