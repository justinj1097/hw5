"""Campus Customs MCP server (FastMCP).

The one shared tool box for every agent on the team (Boss, Inventory, Accounting,
Facilities, Customer Service) and for the human approval path. It only ever opens
the working copy data/campus_customs_new.db - never the original campus_customs.db.

Rules the tools enforce (not just the prompts):
- Every number comes from the database. Missing rows come back as
  {"found": False, "error": ...}; nothing is guessed or filled in.
- "Today" is desk.date_today, never the computer clock (shop rule 1).
- Vendor lead times come from vendors.lead_days (rule 2).
- A vendor with an open unpaid invoice will not ship: purchase orders to it
  cannot be executed (rule 3).
- Agents can only *request* payments. Money moves only through
  execute_approved_request, which needs a human approver name plus an approval
  code that only the backend can compute (HMAC with CC_APPROVAL_SECRET, which
  agents never see). After paying it updates payments, cash_accounts, and the
  invoice / lease / purchase order it paid for (rule 4).
- The pay tool refuses anything that would make a balance negative (rule 5).
  Cash only goes out; no tool adds money (rule 6).
- Messages to customers or landlords are saved as drafts on the board and are
  never sent (rule 8).

Run (stdio):  .venv/bin/python mcp_server/server.py
"""

from __future__ import annotations

import calendar
import hashlib
import hmac
import os
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from fastmcp import FastMCP

HW5_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("CAMPUS_CUSTOMS_DB", HW5_ROOT / "data" / "campus_customs_new.db")).resolve()

if DB_PATH.name == "campus_customs.db":
    raise SystemExit("Refusing to use the original campus_customs.db - point at campus_customs_new.db.")

mcp = FastMCP("campus-customs")

TICKET_STATUSES = ("open", "in_progress", "waiting_on_approval", "blocked", "resolved")
DRAFT_RECIPIENT_MAX = 120
DRAFT_BODY_MAX = 4000

# Board tables this server adds to the working copy. They vanish on reset (the original
# file doesn't have them) and are recreated on the next connection.
_BOARD_SCHEMA = """
CREATE TABLE IF NOT EXISTS approval_requests (
    id INTEGER PRIMARY KEY,
    ticket_id INTEGER,
    kind TEXT NOT NULL CHECK (kind IN ('invoice', 'rent', 'purchase_order')),
    ref_id INTEGER,
    vendor_id INTEGER,
    sku TEXT,
    size TEXT,
    qty INTEGER,
    amount REAL NOT NULL,
    reason TEXT NOT NULL,
    requested_by TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'executed', 'rejected', 'failed')),
    shop_date TEXT NOT NULL,
    created_at TEXT NOT NULL,
    decided_by TEXT,
    decided_at TEXT,
    payment_id INTEGER,
    decision_note TEXT
);
CREATE TABLE IF NOT EXISTS purchase_orders (
    id INTEGER PRIMARY KEY,
    request_id INTEGER NOT NULL,
    vendor_id INTEGER NOT NULL,
    sku TEXT NOT NULL,
    size TEXT NOT NULL,
    qty INTEGER NOT NULL,
    unit_cost REAL NOT NULL,
    total REAL NOT NULL,
    ordered_on TEXT NOT NULL,
    eta TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ordered'
);
CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY,
    ticket_id INTEGER,
    recipient TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    drafted_by TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ticket_updates (
    id INTEGER PRIMARY KEY,
    ticket_id INTEGER NOT NULL,
    author TEXT NOT NULL,
    status TEXT,
    note TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


# ---------------------------------------------------------------- helpers

def _connect(readonly: bool = True) -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Working database not found: {DB_PATH}")
    if readonly:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(DB_PATH, isolation_level=None)  # explicit BEGIN IMMEDIATE below
        conn.executescript(_BOARD_SCHEMA)
    conn.row_factory = sqlite3.Row
    return conn


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _today(conn: sqlite3.Connection) -> date:
    row = conn.execute("SELECT date_today FROM desk LIMIT 1").fetchone()
    if row is None or not row["date_today"]:
        raise ValueError("desk.date_today is empty - cannot tell what is overdue.")
    return date.fromisoformat(row["date_today"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _add_month(d: date) -> date:
    year, month = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def _money(x: float) -> float:
    return round(float(x), 2)


def _open_invoices(conn: sqlite3.Connection, vendor_id: int, today: date) -> list[dict]:
    rows = conn.execute(
        "SELECT id, amount, due_date, description FROM invoices WHERE vendor_id = ? AND status = 'open' ORDER BY due_date",
        (vendor_id,),
    ).fetchall()
    out = []
    for r in rows:
        overdue = (today - date.fromisoformat(r["due_date"])).days
        out.append({"invoice_id": r["id"], "amount": r["amount"], "due_date": r["due_date"],
                    "description": r["description"], "days_overdue": max(0, overdue), "is_overdue": overdue > 0})
    return out


def _checking(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute("SELECT name, balance, date FROM cash_accounts WHERE name = 'checking'").fetchone()


def _pending_total(conn: sqlite3.Connection) -> float:
    if not _has_table(conn, "approval_requests"):
        return 0.0
    return conn.execute("SELECT COALESCE(SUM(amount), 0) FROM approval_requests WHERE status = 'pending'").fetchone()[0]


def _ticket_exists(conn: sqlite3.Connection, ticket_id: int | None) -> bool:
    return ticket_id is None or conn.execute("SELECT 1 FROM tickets WHERE id = ?", (ticket_id,)).fetchone() is not None


def _no_pending(conn: sqlite3.Connection, ticket_id: int | None) -> bool:
    return ticket_id is not None and conn.execute(
        "SELECT COUNT(*) FROM approval_requests WHERE ticket_id = ? AND status = 'pending'", (ticket_id,)).fetchone()[0] == 0


def _log(conn: sqlite3.Connection, ticket_id: int | None, author: str, note: str, status: str | None = None) -> None:
    if ticket_id is not None:
        conn.execute("INSERT INTO ticket_updates (ticket_id, author, status, note, created_at) VALUES (?, ?, ?, ?, ?)",
                     (ticket_id, author, status, note, _now()))


def _clean_name(name: str) -> str:
    return " ".join(name.split())[:60]


# ---------------------------------------------------------------- desk and tickets

@mcp.tool()
def get_shop_today() -> dict:
    """The shop's official 'today' (desk.date_today) and desk notes. Use this date - never the
    computer clock - for anything overdue, due soon, or arriving later."""
    with _connect() as conn:
        row = conn.execute("SELECT date_today, notes FROM desk LIMIT 1").fetchone()
    if row is None:
        return {"found": False, "error": "desk table is empty."}
    return {"found": True, "date_today": row["date_today"], "notes": row["notes"]}


@mcp.tool()
def list_open_tickets() -> dict:
    """Every ticket that is not resolved, oldest first: id, type, requester, subject, item, linked lease/invoice, status."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, type, requester, subject, sku, size, qty, lease_id, invoice_id, status, created_at "
            "FROM tickets WHERE status <> 'resolved' ORDER BY created_at, id"
        ).fetchall()
    return {"count": len(rows), "tickets": [dict(r) for r in rows]}


@mcp.tool()
def list_tickets() -> dict:
    """Every ticket (open or resolved) with its board status and how many approval requests / drafts it has."""
    with _connect() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, type, requester, subject, sku, size, qty, lease_id, invoice_id, status, notes, created_at FROM tickets ORDER BY id")]
        for t in rows:
            t["is_open"] = t["status"] != "resolved"
            if _has_table(conn, "approval_requests"):
                t["pending_approvals"] = conn.execute(
                    "SELECT COUNT(*) FROM approval_requests WHERE ticket_id = ? AND status = 'pending'", (t["id"],)).fetchone()[0]
                t["drafts"] = conn.execute("SELECT COUNT(*) FROM drafts WHERE ticket_id = ?", (t["id"],)).fetchone()[0]
            else:
                t["pending_approvals"] = t["drafts"] = 0
    return {"count": len(rows), "open": sum(t["is_open"] for t in rows), "tickets": rows}


@mcp.tool()
def get_ticket(ticket_id: int) -> dict:
    """One ticket in full, with the lease or invoice (and its vendor) it links to, plus everything
    already on the board for it: status updates, approval requests, and saved drafts."""
    with _connect() as conn:
        t = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        if t is None:
            return {"found": False, "error": f"No ticket with id {ticket_id}."}
        result: dict = {"found": True, "ticket": dict(t)}
        if t["lease_id"] is not None:
            lease = conn.execute("SELECT * FROM leases WHERE id = ?", (t["lease_id"],)).fetchone()
            result["linked_lease"] = dict(lease) if lease else {"found": False, "error": f"Lease {t['lease_id']} missing."}
        if t["invoice_id"] is not None:
            inv = conn.execute(
                "SELECT i.*, v.name AS vendor_name, v.specialty, v.lead_days FROM invoices i "
                "LEFT JOIN vendors v ON v.id = i.vendor_id WHERE i.id = ?", (t["invoice_id"],)).fetchone()
            result["linked_invoice"] = dict(inv) if inv else {"found": False, "error": f"Invoice {t['invoice_id']} missing."}
        for table, key in (("ticket_updates", "board_updates"), ("approval_requests", "approval_requests"), ("drafts", "drafts")):
            result[key] = ([dict(r) for r in conn.execute(f"SELECT * FROM {table} WHERE ticket_id = ? ORDER BY id", (ticket_id,))]
                           if _has_table(conn, table) else [])
    return result


@mcp.tool()
def update_ticket_status(ticket_id: int, status: Literal["open", "in_progress", "waiting_on_approval", "blocked", "resolved"],
                         note: str, updated_by: str) -> dict:
    """Set a ticket's board status and log why. Only mark 'resolved' when nothing is left waiting on
    a human approval for that ticket.

    Args:
        ticket_id: tickets.id.
        status: open | in_progress | waiting_on_approval | blocked | resolved.
        note: One or two sentences on why (shown on the board).
        updated_by: The agent making the change, e.g. "boss".
    """
    if status not in TICKET_STATUSES:
        return {"ok": False, "error": f"status must be one of {TICKET_STATUSES}."}
    conn = _connect(readonly=False)
    try:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT 1 FROM tickets WHERE id = ?", (ticket_id,)).fetchone() is None:
            conn.execute("ROLLBACK")
            return {"ok": False, "error": f"No ticket with id {ticket_id}."}
        pending = conn.execute("SELECT id FROM approval_requests WHERE ticket_id = ? AND status = 'pending'",
                               (ticket_id,)).fetchall()
        if status == "resolved" and pending:
            conn.execute("ROLLBACK")
            return {"ok": False, "error": "Ticket still has approval requests waiting on a human: "
                                          f"{[r['id'] for r in pending]}. Use 'waiting_on_approval'."}
        conn.execute("UPDATE tickets SET status = ? WHERE id = ?", (status, ticket_id))
        _log(conn, ticket_id, _clean_name(updated_by), note.strip()[:1000], status)
        conn.execute("COMMIT")
    finally:
        conn.close()
    return {"ok": True, "ticket_id": ticket_id, "status": status}


@mcp.tool()
def add_ticket_note(ticket_id: int, note: str, author: str) -> dict:
    """Add a finding or progress note to a ticket's board log without changing its status."""
    conn = _connect(readonly=False)
    try:
        if not _ticket_exists(conn, ticket_id):
            return {"ok": False, "error": f"No ticket with id {ticket_id}."}
        _log(conn, ticket_id, _clean_name(author), note.strip()[:1000])
    finally:
        conn.close()
    return {"ok": True, "ticket_id": ticket_id}


# ---------------------------------------------------------------- inventory and vendors

@mcp.tool()
def check_stock(sku: str, size: str, qty_needed: int | None = None) -> dict:
    """Stock on hand for one SKU in one size, plus its unit cost and list price.

    Use for a customer order or bulk request: tells you whether the shelf can
    fill it, the shortfall if not, what other sizes are on hand, and the cost
    floor (unit_cost) any discount must stay above.

    Args:
        sku: Product SKU exactly as in the ticket, e.g. "CC-HOOD-NAVY".
        size: Size code as in the ticket, e.g. "S", "M", "XL", "OS".
        qty_needed: Units the ticket asks for (optional). Enables can_fill/shortfall.
    """
    sku, size = sku.strip().upper(), size.strip().upper()
    with _connect() as conn:
        row = conn.execute(
            "SELECT sku, name, size, qty, location FROM inventory WHERE sku = ? AND size = ?", (sku, size)
        ).fetchone()
        if row is None:
            sizes = [r["size"] for r in conn.execute("SELECT size FROM inventory WHERE sku = ?", (sku,))]
            return {"found": False, "error": f"No inventory row for {sku} size {size}.", "sizes_listed_for_sku": sizes}
        others = conn.execute(
            "SELECT size, qty FROM inventory WHERE sku = ? AND size <> ? ORDER BY size", (sku, size)
        ).fetchall()
        price = conn.execute("SELECT unit_cost, list_price FROM pricing WHERE sku = ?", (sku,)).fetchone()

    result = {
        "found": True, "sku": row["sku"], "name": row["name"], "size": row["size"], "on_hand": row["qty"],
        "location": row["location"], "other_sizes_on_hand": {r["size"]: r["qty"] for r in others},
        "unit_cost": price["unit_cost"] if price else None, "list_price": price["list_price"] if price else None,
    }
    if price is None:
        result["pricing_note"] = f"No pricing row for {sku}."
    if qty_needed is not None:
        result.update(qty_needed=qty_needed, can_fill=row["qty"] >= qty_needed, shortfall=max(0, qty_needed - row["qty"]))
    return result


@mcp.tool()
def list_vendors() -> dict:
    """All vendors with specialty, lead_days, open invoice total, and whether each can ship right now.
    Pick the vendor whose specialty matches the item; the database has no SKU-to-vendor table."""
    with _connect() as conn:
        today = _today(conn)
        vendors = conn.execute("SELECT id, name, specialty, lead_days FROM vendors ORDER BY id").fetchall()
        out = []
        for v in vendors:
            open_inv = _open_invoices(conn, v["id"], today)
            out.append({**dict(v), "open_invoice_ids": [i["invoice_id"] for i in open_inv],
                        "open_amount": _money(sum(i["amount"] for i in open_inv)), "can_ship": not open_inv})
    return {"today": today.isoformat(), "vendors": out}


@mcp.tool()
def check_vendor_invoices(vendor_id: int | None = None, invoice_id: int | None = None) -> dict:
    """Whether a vendor will ship: its lead time and every open (unpaid) invoice.

    Shop rule 3: a vendor will not ship new product while it has an open unpaid
    invoice. Overdue is measured against desk.date_today. Pass either the
    vendor_id, or an invoice_id from a ticket to look up that invoice's vendor.

    Args:
        vendor_id: vendors.id, e.g. 1 for the apparel printer.
        invoice_id: invoices.id linked from a ticket, e.g. 501.
    """
    if vendor_id is None and invoice_id is None:
        return {"found": False, "error": "Pass vendor_id or invoice_id."}
    with _connect() as conn:
        today = _today(conn)
        if vendor_id is None:
            inv = conn.execute("SELECT vendor_id FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
            if inv is None:
                return {"found": False, "error": f"No invoice with id {invoice_id}."}
            vendor_id = inv["vendor_id"]
        vendor = conn.execute("SELECT id, name, specialty, lead_days FROM vendors WHERE id = ?", (vendor_id,)).fetchone()
        if vendor is None:
            return {"found": False, "error": f"No vendor with id {vendor_id}."}
        open_invoices = _open_invoices(conn, vendor_id, today)

    can_ship = not open_invoices
    return {
        "found": True, "today": today.isoformat(), "vendor_id": vendor["id"], "vendor_name": vendor["name"],
        "specialty": vendor["specialty"], "lead_days": vendor["lead_days"], "open_invoices": open_invoices,
        "total_open_amount": _money(sum(i["amount"] for i in open_invoices)), "can_ship": can_ship,
        "blocked_reason": None if can_ship else
            f"{vendor['name']} has {len(open_invoices)} open unpaid invoice(s); it will not ship new product until they are paid.",
    }


@mcp.tool()
def estimate_restock(vendor_id: int, sku: str, size: str, qty: int) -> dict:
    """Cost and arrival date for restocking qty units of one SKU/size from one vendor, before ordering.

    Cost = pricing.unit_cost x qty. Earliest arrival = desk.date_today + vendors.lead_days, and only
    once the vendor has no open invoices (rule 3). Also says whether checking could cover it.
    """
    sku, size = sku.strip().upper(), size.strip().upper()
    if qty <= 0:
        return {"found": False, "error": "qty must be positive."}
    with _connect() as conn:
        today = _today(conn)
        vendor = conn.execute("SELECT id, name, specialty, lead_days FROM vendors WHERE id = ?", (vendor_id,)).fetchone()
        if vendor is None:
            return {"found": False, "error": f"No vendor with id {vendor_id}."}
        item = conn.execute("SELECT name FROM inventory WHERE sku = ? AND size = ?", (sku, size)).fetchone()
        if item is None:
            return {"found": False, "error": f"No inventory row for {sku} size {size}."}
        price = conn.execute("SELECT unit_cost FROM pricing WHERE sku = ?", (sku,)).fetchone()
        if price is None:
            return {"found": False, "error": f"No pricing row for {sku}; cannot cost a restock."}
        open_inv = _open_invoices(conn, vendor_id, today)
        cash = _checking(conn)
        pending = _pending_total(conn)

    cost = _money(price["unit_cost"] * qty)
    balance = cash["balance"] if cash else None
    return {
        "found": True, "today": today.isoformat(), "vendor_id": vendor["id"], "vendor_name": vendor["name"],
        "vendor_specialty": vendor["specialty"], "sku": sku, "size": size, "item_name": item["name"], "qty": qty,
        "unit_cost": price["unit_cost"], "total_cost": cost, "lead_days": vendor["lead_days"],
        "vendor_can_ship_now": not open_inv, "blocking_invoice_ids": [i["invoice_id"] for i in open_inv],
        "earliest_arrival_if_ordered_today": (today + timedelta(days=vendor["lead_days"])).isoformat() if not open_inv else None,
        "arrival_note": None if not open_inv else
            f"Blocked: pay invoice(s) {[i['invoice_id'] for i in open_inv]} first; arrival is then {vendor['lead_days']} days after the order.",
        "checking_balance": balance, "pending_approval_total": _money(pending),
        "affordable_after_pending": None if balance is None else balance - pending - cost >= 0,
    }


# ---------------------------------------------------------------- money: read

@mcp.tool()
def get_rent_due(lease_id: int) -> dict:
    """Rent owed on a lease, how many days until it is due, and whether checking can cover it.

    Reads the lease, desk.date_today and the checking balance. Does NOT pay -
    any payment needs human approval first.

    Args:
        lease_id: leases.id from a rent ticket, e.g. 1.
    """
    with _connect() as conn:
        today = _today(conn)
        lease = conn.execute(
            "SELECT id, space_name, landlord, monthly_rent, next_due, notes FROM leases WHERE id = ?", (lease_id,)
        ).fetchone()
        if lease is None:
            return {"found": False, "error": f"No lease with id {lease_id}."}
        cash = _checking(conn)

    days_until_due = (date.fromisoformat(lease["next_due"]) - today).days
    result = {
        "found": True, "today": today.isoformat(), "lease_id": lease["id"], "space_name": lease["space_name"],
        "landlord": lease["landlord"], "monthly_rent": lease["monthly_rent"], "next_due": lease["next_due"],
        "days_until_due": days_until_due, "is_overdue": days_until_due < 0, "notes": lease["notes"],
    }
    if cash is None:
        result["cash_note"] = "No checking account row found."
    else:
        after = _money(cash["balance"] - lease["monthly_rent"])
        result.update(checking_balance=cash["balance"], balance_after_rent=after, can_afford=after >= 0)
    return result


@mcp.tool()
def get_cash_position() -> dict:
    """The whole money picture as of desk.date_today: cash balances, every open vendor invoice (with
    days overdue), upcoming rent, approval requests still waiting on a human, and what would be left
    if every pending request were paid. Cash only goes out in this shop - there is no revenue."""
    with _connect() as conn:
        today = _today(conn)
        accounts = [dict(r) for r in conn.execute("SELECT name, balance, date FROM cash_accounts ORDER BY name")]
        invoices = []
        for r in conn.execute("SELECT i.id, i.vendor_id, v.name AS vendor_name, i.amount, i.due_date, i.description "
                              "FROM invoices i LEFT JOIN vendors v ON v.id = i.vendor_id WHERE i.status = 'open' ORDER BY i.due_date"):
            overdue = (today - date.fromisoformat(r["due_date"])).days
            invoices.append({**dict(r), "days_overdue": max(0, overdue), "is_overdue": overdue > 0})
        leases = []
        for r in conn.execute("SELECT id, space_name, landlord, monthly_rent, next_due FROM leases ORDER BY next_due"):
            leases.append({**dict(r), "days_until_due": (date.fromisoformat(r["next_due"]) - today).days})
        pending = ([dict(r) for r in conn.execute(
            "SELECT id, ticket_id, kind, ref_id, amount, requested_by, reason FROM approval_requests WHERE status = 'pending' ORDER BY id")]
            if _has_table(conn, "approval_requests") else [])

    checking = next((a["balance"] for a in accounts if a["name"] == "checking"), None)
    pending_total = _money(sum(p["amount"] for p in pending))
    return {
        "today": today.isoformat(), "accounts": accounts, "open_invoices": invoices,
        "open_invoice_total": _money(sum(i["amount"] for i in invoices)), "leases": leases,
        "pending_approval_requests": pending, "pending_approval_total": pending_total,
        "checking_after_all_pending": None if checking is None else _money(checking - pending_total),
        "note": "No money comes in. Payments only happen after a human approves them.",
    }


@mcp.tool()
def check_discount(sku: str, qty: int, unit_price: float | None = None, discount_pct: float | None = None) -> dict:
    """Margin check for a proposed price (price-override / bulk-discount tickets).

    Give either a unit_price or a discount_pct off list. Returns the price, total, profit per unit and
    margin vs pricing.unit_cost, and whether it would sell at or below cost. The shop has no written
    discount policy in the database; never go at or below cost.
    """
    sku = sku.strip().upper()
    if qty <= 0:
        return {"found": False, "error": "qty must be positive."}
    if (unit_price is None) == (discount_pct is None):
        return {"found": False, "error": "Pass exactly one of unit_price or discount_pct."}
    with _connect() as conn:
        price = conn.execute("SELECT unit_cost, list_price FROM pricing WHERE sku = ?", (sku,)).fetchone()
    if price is None:
        return {"found": False, "error": f"No pricing row for {sku}."}
    cost, list_price = price["unit_cost"], price["list_price"]
    if discount_pct is not None:
        if not 0 <= discount_pct < 100:
            return {"found": False, "error": "discount_pct must be between 0 and 100."}
        unit_price = _money(list_price * (1 - discount_pct / 100))
    profit = _money(unit_price - cost)
    return {
        "found": True, "sku": sku, "qty": qty, "unit_cost": cost, "list_price": list_price,
        "proposed_unit_price": unit_price, "discount_pct_off_list": round((1 - unit_price / list_price) * 100, 2),
        "profit_per_unit": profit, "margin_pct": round(profit / unit_price * 100, 2) if unit_price else None,
        "order_total": _money(unit_price * qty), "order_profit": _money(profit * qty),
        "at_or_below_cost": unit_price <= cost, "list_price_margin_pct": round((list_price - cost) / list_price * 100, 2),
    }


@mcp.tool()
def list_payments() -> dict:
    """Every payment already made (who approved it, what it paid, when)."""
    with _connect() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM payments ORDER BY id")]
    return {"count": len(rows), "payments": rows, "total_paid": _money(sum(r["amount"] for r in rows))}


@mcp.tool()
def list_approval_requests(status: Literal["pending", "executed", "rejected", "failed", "all"] = "pending") -> dict:
    """Payment and purchase-order requests on the board, filtered by status (default: waiting on a human)."""
    with _connect() as conn:
        if not _has_table(conn, "approval_requests"):
            return {"count": 0, "requests": []}
        sql, args = "SELECT * FROM approval_requests", ()
        if status != "all":
            sql, args = sql + " WHERE status = ?", (status,)
        rows = [dict(r) for r in conn.execute(sql + " ORDER BY id", args)]
    return {"count": len(rows), "requests": rows}


# ---------------------------------------------------------------- money: request (agents)

@mcp.tool()
def request_payment_approval(kind: Literal["invoice", "rent"], ref_id: int, reason: str, requested_by: str,
                             ticket_id: int | None = None) -> dict:
    """Put a payment on the board for a HUMAN to approve. This does not pay anything.

    The amount is read from the database (the invoice amount, or the lease's monthly_rent) - you
    cannot set it. Refused if the invoice is already paid, if the same payment is already waiting,
    or if checking could never cover it. Warns when pending requests together exceed checking.

    Args:
        kind: "invoice" (vendor bill) or "rent" (lease).
        ref_id: invoices.id or leases.id.
        reason: Why this should be paid now (shown to the human approver).
        requested_by: The agent asking, e.g. "accounting".
        ticket_id: The ticket this unblocks, if any.
    """
    conn = _connect(readonly=False)
    try:
        conn.execute("BEGIN IMMEDIATE")
        today = _today(conn)
        if not _ticket_exists(conn, ticket_id):
            conn.execute("ROLLBACK")
            return {"ok": False, "error": f"No ticket with id {ticket_id}."}
        vendor_id = None
        if kind == "invoice":
            inv = conn.execute("SELECT id, vendor_id, amount, status, due_date FROM invoices WHERE id = ?", (ref_id,)).fetchone()
            if inv is None:
                conn.execute("ROLLBACK")
                return {"ok": False, "error": f"No invoice with id {ref_id}."}
            if inv["status"] != "open":
                conn.execute("ROLLBACK")
                return {"ok": False, "error": f"Invoice {ref_id} is '{inv['status']}', not open - nothing to pay."}
            amount, vendor_id = inv["amount"], inv["vendor_id"]
            detail = f"invoice {ref_id} due {inv['due_date']}"
        elif kind == "rent":
            lease = conn.execute("SELECT id, monthly_rent, next_due, landlord FROM leases WHERE id = ?", (ref_id,)).fetchone()
            if lease is None:
                conn.execute("ROLLBACK")
                return {"ok": False, "error": f"No lease with id {ref_id}."}
            amount = lease["monthly_rent"]
            detail = f"rent for lease {ref_id} ({lease['landlord']}) due {lease['next_due']}"
        else:
            conn.execute("ROLLBACK")
            return {"ok": False, "error": "kind must be 'invoice' or 'rent'."}

        dup = conn.execute("SELECT id FROM approval_requests WHERE kind = ? AND ref_id = ? AND status = 'pending'",
                           (kind, ref_id)).fetchone()
        if dup:
            conn.execute("ROLLBACK")
            return {"ok": False, "error": f"Already waiting on a human: approval request {dup['id']}.", "request_id": dup["id"]}
        cash = _checking(conn)
        balance = cash["balance"] if cash else 0.0
        if amount > balance:
            conn.execute("ROLLBACK")
            return {"ok": False, "error": f"Checking has ${balance:,.2f}; ${amount:,.2f} can never be paid (no negative balances)."}
        pending_before = _pending_total(conn)
        cur = conn.execute(
            "INSERT INTO approval_requests (ticket_id, kind, ref_id, vendor_id, amount, reason, requested_by, shop_date, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ticket_id, kind, ref_id, vendor_id, amount, reason.strip()[:1000], _clean_name(requested_by), today.isoformat(), _now()))
        _log(conn, ticket_id, _clean_name(requested_by), f"Requested human approval to pay {detail}: ${amount:,.2f} (request {cur.lastrowid}).")
        conn.execute("COMMIT")
    finally:
        conn.close()
    left = _money(balance - pending_before - amount)
    return {
        "ok": True, "request_id": cur.lastrowid, "status": "pending", "kind": kind, "ref_id": ref_id, "amount": amount,
        "checking_balance": balance, "checking_after_all_pending": left,
        "warning": None if left >= 0 else "Pending requests together exceed checking; a human will have to choose - the pay tool refuses overdrafts.",
        "note": "Nothing has been paid. A human must approve this request on the dashboard.",
    }


@mcp.tool()
def request_purchase_order(vendor_id: int, sku: str, size: str, qty: int, reason: str, requested_by: str,
                           ticket_id: int | None = None) -> dict:
    """Put a restock purchase order on the board for a HUMAN to approve. This does not order or pay.

    Cost = pricing.unit_cost x qty (from the database). If the vendor has an open unpaid invoice the
    request is still saved but flagged: it cannot be executed until that invoice is paid (rule 3).
    """
    sku, size = sku.strip().upper(), size.strip().upper()
    if qty <= 0:
        return {"ok": False, "error": "qty must be positive."}
    conn = _connect(readonly=False)
    try:
        conn.execute("BEGIN IMMEDIATE")
        today = _today(conn)
        if not _ticket_exists(conn, ticket_id):
            conn.execute("ROLLBACK")
            return {"ok": False, "error": f"No ticket with id {ticket_id}."}
        vendor = conn.execute("SELECT id, name, lead_days FROM vendors WHERE id = ?", (vendor_id,)).fetchone()
        item = conn.execute("SELECT 1 FROM inventory WHERE sku = ? AND size = ?", (sku, size)).fetchone()
        price = conn.execute("SELECT unit_cost FROM pricing WHERE sku = ?", (sku,)).fetchone()
        if vendor is None or item is None or price is None:
            conn.execute("ROLLBACK")
            missing = "vendor" if vendor is None else "inventory row" if item is None else "pricing row"
            return {"ok": False, "error": f"No {missing} for that request; nothing saved."}
        amount = _money(price["unit_cost"] * qty)
        dup = conn.execute("SELECT id FROM approval_requests WHERE kind = 'purchase_order' AND vendor_id = ? AND sku = ? "
                           "AND size = ? AND status = 'pending'", (vendor_id, sku, size)).fetchone()
        if dup:
            conn.execute("ROLLBACK")
            return {"ok": False, "error": f"A purchase order for {sku} {size} from this vendor is already waiting: request {dup['id']}.",
                    "request_id": dup["id"]}
        cash = _checking(conn)
        balance = cash["balance"] if cash else 0.0
        if amount > balance:
            conn.execute("ROLLBACK")
            return {"ok": False, "error": f"Checking has ${balance:,.2f}; a ${amount:,.2f} order can never be paid (no negative balances)."}
        pending_before = _pending_total(conn)
        open_inv = _open_invoices(conn, vendor_id, today)
        cur = conn.execute(
            "INSERT INTO approval_requests (ticket_id, kind, vendor_id, sku, size, qty, amount, reason, requested_by, shop_date, created_at) "
            "VALUES (?, 'purchase_order', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ticket_id, vendor_id, sku, size, qty, amount, reason.strip()[:1000], _clean_name(requested_by), today.isoformat(), _now()))
        _log(conn, ticket_id, _clean_name(requested_by),
             f"Requested human approval for PO: {qty} x {sku} {size} from {vendor['name']}, ${amount:,.2f} (request {cur.lastrowid}).")
        conn.execute("COMMIT")
    finally:
        conn.close()
    left = _money(balance - pending_before - amount)
    return {
        "ok": True, "request_id": cur.lastrowid, "status": "pending", "vendor_name": vendor["name"], "sku": sku, "size": size,
        "qty": qty, "amount": amount, "lead_days": vendor["lead_days"],
        "blocked_by_open_invoices": [i["invoice_id"] for i in open_inv],
        "earliest_arrival_if_approved_today": (today + timedelta(days=vendor["lead_days"])).isoformat(),
        "checking_after_all_pending": left,
        "warning": "; ".join(w for w in (
            f"{vendor['name']} will not ship until invoice(s) {[i['invoice_id'] for i in open_inv]} are paid." if open_inv else "",
            "Pending requests together exceed checking." if left < 0 else "") if w) or None,
        "note": "Nothing has been ordered or paid. A human must approve this request on the dashboard.",
    }


# ---------------------------------------------------------------- drafts (never sent)

@mcp.tool()
def save_draft_message(recipient: str, subject: str, body: str, drafted_by: str, ticket_id: int | None = None) -> dict:
    """Save a message to a customer, student org, landlord, or vendor as a DRAFT on the board.
    Nothing is emailed, texted, or called - a human reads drafts and decides whether to send them."""
    if not body.strip() or not subject.strip() or not recipient.strip():
        return {"ok": False, "error": "recipient, subject and body are all required."}
    if len(body) > DRAFT_BODY_MAX:
        return {"ok": False, "error": f"Draft body is over {DRAFT_BODY_MAX} characters; shorten it."}
    conn = _connect(readonly=False)
    try:
        if not _ticket_exists(conn, ticket_id):
            return {"ok": False, "error": f"No ticket with id {ticket_id}."}
        cur = conn.execute("INSERT INTO drafts (ticket_id, recipient, subject, body, drafted_by, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                           (ticket_id, recipient.strip()[:DRAFT_RECIPIENT_MAX], subject.strip()[:200], body.strip(),
                            _clean_name(drafted_by), _now()))
        _log(conn, ticket_id, _clean_name(drafted_by), f"Saved draft {cur.lastrowid} to {recipient.strip()[:DRAFT_RECIPIENT_MAX]} (not sent).")
    finally:
        conn.close()
    return {"ok": True, "draft_id": cur.lastrowid, "status": "draft", "sent": False}


# ---------------------------------------------------------------- human-only (hidden from agents)

def _approval_secret() -> str | None:
    return os.environ.get("CC_APPROVAL_SECRET") or None


def _valid_code(action: str, request_id: int, person: str, code: str) -> bool:
    secret = _approval_secret()
    if not secret or not code:
        return False
    expected = hmac.new(secret.encode(), f"{action}:{request_id}:{person}".encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, code)


@mcp.tool()
def execute_approved_request(request_id: int, approved_by: str, approval_code: str) -> dict:
    """HUMAN APPROVAL ONLY. Pays an approval request after a person approves it on the dashboard.
    Agents must never call this. Requires the approval code the backend issues for that person."""
    approved_by = _clean_name(approved_by)
    if not approved_by:
        return {"ok": False, "error": "approved_by (a human's name) is required."}
    if not _valid_code("execute", request_id, approved_by, approval_code):
        return {"ok": False, "error": "Refused: no valid human approval for this request. Payments need a person to approve them on the dashboard."}

    conn = _connect(readonly=False)
    try:
        conn.execute("BEGIN IMMEDIATE")
        today = _today(conn)
        req = conn.execute("SELECT * FROM approval_requests WHERE id = ?", (request_id,)).fetchone()
        if req is None:
            conn.execute("ROLLBACK")
            return {"ok": False, "error": f"No approval request {request_id}."}
        if req["status"] != "pending":
            conn.execute("ROLLBACK")
            return {"ok": False, "error": f"Request {request_id} is already '{req['status']}'."}

        def refuse(msg: str) -> dict:
            conn.execute("ROLLBACK")
            return {"ok": False, "request_id": request_id, "error": msg, "status": "pending"}

        # Re-read the amount from the source table; never trust the stored copy blindly.
        kind = req["kind"]
        if kind == "invoice":
            inv = conn.execute("SELECT amount, status FROM invoices WHERE id = ?", (req["ref_id"],)).fetchone()
            if inv is None or inv["status"] != "open":
                return refuse(f"Invoice {req['ref_id']} is no longer open.")
            amount = inv["amount"]
        elif kind == "rent":
            lease = conn.execute("SELECT monthly_rent, next_due FROM leases WHERE id = ?", (req["ref_id"],)).fetchone()
            if lease is None:
                return refuse(f"Lease {req['ref_id']} not found.")
            amount = lease["monthly_rent"]
        else:
            vendor = conn.execute("SELECT name, lead_days FROM vendors WHERE id = ?", (req["vendor_id"],)).fetchone()
            price = conn.execute("SELECT unit_cost FROM pricing WHERE sku = ?", (req["sku"],)).fetchone()
            if vendor is None or price is None:
                return refuse("Vendor or pricing row for this purchase order no longer exists.")
            blocking = _open_invoices(conn, req["vendor_id"], today)
            if blocking:
                return refuse(f"{vendor['name']} still has open invoice(s) {[i['invoice_id'] for i in blocking]}; "
                              "it will not ship until they are paid (rule 3).")
            amount = _money(price["unit_cost"] * req["qty"])
        if abs(amount - req["amount"]) > 0.005:
            return refuse(f"Amount changed since the request (${req['amount']:,.2f} -> ${amount:,.2f}); ask for a new request.")

        cash = _checking(conn)
        if cash is None:
            return refuse("No checking account.")
        if cash["balance"] - amount < 0:
            return refuse(f"Not enough cash: checking has ${cash['balance']:,.2f}, payment is ${amount:,.2f}. No negative balances.")

        ref_id = req["ref_id"]
        effect: dict = {}
        if kind == "purchase_order":
            eta = (today + timedelta(days=vendor["lead_days"])).isoformat()
            po = conn.execute(
                "INSERT INTO purchase_orders (request_id, vendor_id, sku, size, qty, unit_cost, total, ordered_on, eta) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (request_id, req["vendor_id"], req["sku"], req["size"], req["qty"], price["unit_cost"], amount, today.isoformat(), eta))
            ref_id = po.lastrowid
            effect = {"purchase_order_id": ref_id, "eta": eta}
        pay = conn.execute("INSERT INTO payments (kind, ref_id, amount, account, paid_at, approved_by) VALUES (?, ?, ?, 'checking', ?, ?)",
                           (kind, ref_id, amount, today.isoformat(), approved_by))
        new_balance = _money(cash["balance"] - amount)
        conn.execute("UPDATE cash_accounts SET balance = ?, date = ? WHERE name = 'checking'", (new_balance, today.isoformat()))
        if kind == "invoice":
            conn.execute("UPDATE invoices SET status = 'paid' WHERE id = ?", (ref_id,))
            effect = {"invoice_id": ref_id, "invoice_status": "paid"}
        elif kind == "rent":
            next_due = _add_month(date.fromisoformat(lease["next_due"])).isoformat()
            conn.execute("UPDATE leases SET next_due = ? WHERE id = ?", (next_due, ref_id))
            effect = {"lease_id": ref_id, "next_due": next_due}
        conn.execute("UPDATE approval_requests SET status = 'executed', decided_by = ?, decided_at = ?, payment_id = ? WHERE id = ?",
                     (approved_by, _now(), pay.lastrowid, request_id))
        _log(conn, req["ticket_id"], approved_by,
             f"Approved and paid request {request_id}: {kind} ${amount:,.2f} (payment {pay.lastrowid}). Checking now ${new_balance:,.2f}.")
        # The Boss parked this ticket on 'waiting_on_approval' with nothing else left to do; the last approval closes it.
        if _no_pending(conn, req["ticket_id"]):
            moved = conn.execute("UPDATE tickets SET status = 'resolved' WHERE id = ? AND status = 'waiting_on_approval'",
                                 (req["ticket_id"],)).rowcount
            if moved:
                _log(conn, req["ticket_id"], approved_by, "Last approval paid; nothing left waiting, ticket resolved.", "resolved")
        conn.execute("COMMIT")
    finally:
        conn.close()
    return {"ok": True, "request_id": request_id, "payment_id": pay.lastrowid, "kind": kind, "amount": amount,
            "approved_by": approved_by, "checking_balance": new_balance, **effect}


@mcp.tool()
def reject_request(request_id: int, rejected_by: str, approval_code: str, reason: str) -> dict:
    """HUMAN APPROVAL ONLY. Declines an approval request. Agents must never call this."""
    rejected_by = _clean_name(rejected_by)
    if not _valid_code("reject", request_id, rejected_by, approval_code):
        return {"ok": False, "error": "Refused: no valid human decision for this request."}
    conn = _connect(readonly=False)
    try:
        conn.execute("BEGIN IMMEDIATE")
        req = conn.execute("SELECT ticket_id, status FROM approval_requests WHERE id = ?", (request_id,)).fetchone()
        if req is None or req["status"] != "pending":
            conn.execute("ROLLBACK")
            return {"ok": False, "error": f"Request {request_id} is not pending."}
        conn.execute("UPDATE approval_requests SET status = 'rejected', decided_by = ?, decided_at = ?, decision_note = ? WHERE id = ?",
                     (rejected_by, _now(), reason.strip()[:500], request_id))
        _log(conn, req["ticket_id"], rejected_by, f"Rejected request {request_id}: {reason.strip()[:300]}")
        # A rejection changes the plan: hand the ticket back to the team instead of leaving it parked.
        if _no_pending(conn, req["ticket_id"]):
            if conn.execute("UPDATE tickets SET status = 'in_progress' WHERE id = ? AND status = 'waiting_on_approval'",
                            (req["ticket_id"],)).rowcount:
                _log(conn, req["ticket_id"], rejected_by, "A request was rejected; back to the team to re-plan.", "in_progress")
        conn.execute("COMMIT")
    finally:
        conn.close()
    return {"ok": True, "request_id": request_id, "status": "rejected"}


if __name__ == "__main__":
    mcp.run()
