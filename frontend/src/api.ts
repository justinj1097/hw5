// Every call goes to the FastAPI backend (backend/main.py). Override with VITE_API_URL if needed.
export const API_BASE = (import.meta.env.VITE_API_URL as string | undefined) ?? 'http://localhost:8000'

export type AgentName = 'boss' | 'inventory' | 'accounting' | 'facilities' | 'customer_service'
export type TicketStatus = 'open' | 'in_progress' | 'waiting_on_approval' | 'blocked' | 'resolved'

export interface Ticket {
  id: number
  type: string
  requester: string
  subject: string
  sku: string | null
  size: string | null
  qty: number | null
  lease_id: number | null
  invoice_id: number | null
  status: TicketStatus
  notes: string | null
  created_at: string
  is_open: boolean
  pending_approvals: number
  drafts: number
}

export interface BoardUpdate { id: number; ticket_id: number; author: string; status: string | null; note: string; created_at: string }
export interface Draft { id: number; ticket_id: number; recipient: string; subject: string; body: string; drafted_by: string; status: string; created_at: string }

export interface Approval {
  id: number
  ticket_id: number | null
  kind: 'invoice' | 'rent' | 'purchase_order'
  ref_id: number | null
  vendor_id: number | null
  sku: string | null
  size: string | null
  qty: number | null
  amount: number
  reason: string
  requested_by: string
  status: 'pending' | 'executed' | 'rejected' | 'failed'
  created_at: string
  decided_by: string | null
  payee?: string | null
  what?: string
  vendor_can_ship?: boolean
  vendor_open_invoice_ids?: number[]
}

export interface TicketDetail {
  found: boolean
  ticket: Ticket
  linked_lease?: Record<string, unknown>
  linked_invoice?: Record<string, unknown>
  board_updates: BoardUpdate[]
  approval_requests: Approval[]
  drafts: Draft[]
}

export interface Payment { id: number; kind: string; ref_id: number; amount: number; account: string; paid_at: string; approved_by: string }

export interface Cash {
  account: string
  balance: number
  as_of: string
  today: string
  pending_approval_total: number
  checking_after_all_pending: number
  open_invoices: { id: number; vendor_name: string; amount: number; due_date: string; days_overdue: number }[]
  payments: Payment[]
  total_paid: number
}

export interface Fact { label: string; value: string; source_tool: string }
export interface BossDecision {
  ticket_id: number
  final_status: TicketStatus
  decision: string
  delegated_to: AgentName[]
  facts: Fact[]
  approvals_awaiting_human: number[]
  customer_draft_ids: number[]
  blockers: string[]
  next_steps_for_human: string[]
}

export interface RunInfo {
  run_id: string
  ticket_id: number
  status: 'running' | 'completed' | 'failed'
  started_at: string
  finished_at: string | null
  decision: BossDecision | null
  error: string | null
}

export interface AgentEvent {
  timestamp: string
  run_id: string
  ticket_id: number | null
  step: number
  agent: AgentName | 'team' | 'human'
  depth: number
  chain?: string[]
  event: string
  said?: string
  tool?: string
  tool_args?: Record<string, string>
  result?: string
  model?: string
  stop_reason?: string
  tokens?: number
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: init?.body ? { 'Content-Type': 'application/json' } : undefined,
  })
  const body = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(typeof body.detail === 'string' ? body.detail : `HTTP ${res.status}`)
  return body as T
}

export const api = {
  tickets: () => call<{ count: number; open: number; tickets: Ticket[] }>('/api/tickets'),
  ticket: (id: number) => call<TicketDetail>(`/api/tickets/${id}`),
  run: (id: number) => call<RunInfo>(`/api/tickets/${id}/run`, { method: 'POST' }),
  runs: () => call<RunInfo[]>('/api/runs'),
  runInfo: (runId: string) => call<RunInfo>(`/api/runs/${runId}`),
  events: (q: { ticket_id?: number; run_id?: string; after?: string; limit?: number }) => {
    const params = new URLSearchParams()
    Object.entries(q).forEach(([k, v]) => v !== undefined && params.set(k, String(v)))
    return call<{ count: number; latest: string | null; running: string[]; events: AgentEvent[] }>(`/api/events?${params}`)
  },
  approvals: (status = 'pending') => call<{ count: number; requests: Approval[] }>(`/api/approvals?status=${status}`),
  approve: (id: number, approvedBy: string) =>
    call<Record<string, unknown>>(`/api/approvals/${id}/approve`, { method: 'POST', body: JSON.stringify({ approved_by: approvedBy }) }),
  reject: (id: number, rejectedBy: string, reason: string) =>
    call<Record<string, unknown>>(`/api/approvals/${id}/reject`, { method: 'POST', body: JSON.stringify({ rejected_by: rejectedBy, reason }) }),
  cash: () => call<Cash>('/api/cash'),
  note: (id: number, author: string, note: string) =>
    call<{ ok: boolean }>(`/api/tickets/${id}/note`, { method: 'POST', body: JSON.stringify({ author, note }) }),
  reset: () => call<{ ok: boolean; md5: string }>('/api/reset', { method: 'POST' }),
  health: () => call<{ ok: boolean; model: string; database: string }>('/api/health'),
}
