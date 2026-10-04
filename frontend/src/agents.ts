import type { AgentEvent, AgentName } from './api'

export interface AgentMeta {
  name: AgentName
  label: string
  role: string
  glyph: string
  color: string
  // where the agent sits on the floor map (percent of the map box)
  x: number
  y: number
}

export const AGENTS: Record<AgentName, AgentMeta> = {
  boss: { name: 'boss', label: 'Boss', role: 'Reads tickets · makes the call', glyph: '♛', color: '#ff3d9a', x: 50, y: 18 },
  inventory: { name: 'inventory', label: 'Inventory', role: 'Stock · vendors · restock dates', glyph: '▦', color: '#ff9fd2', x: 12, y: 76 },
  accounting: { name: 'accounting', label: 'Accounting', role: 'Cash · invoices · approvals', glyph: '$', color: '#ffd36e', x: 37, y: 82 },
  facilities: { name: 'facilities', label: 'Facilities', role: 'Lease · rent · landlord', glyph: '⌂', color: '#c39bff', x: 63, y: 82 },
  customer_service: { name: 'customer_service', label: 'Customer Service', role: 'Drafts to customers', glyph: '✉', color: '#7fe3c8', x: 88, y: 76 },
}
export const AGENT_ORDER: AgentName[] = ['boss', 'inventory', 'accounting', 'facilities', 'customer_service']

export const isAgent = (a: string): a is AgentName => a in AGENTS

export const TOOL_LABELS: Record<string, string> = {
  get_ticket: 'read the ticket',
  get_shop_today: 'checked the shop date',
  list_open_tickets: 'scanned open tickets',
  check_stock: 'checked stock',
  list_vendors: 'looked up vendors',
  check_vendor_invoices: 'checked vendor invoices',
  estimate_restock: 'priced a restock',
  get_rent_due: 'checked rent due',
  get_cash_position: 'checked the cash position',
  check_discount: 'ran a margin check',
  list_payments: 'reviewed payments',
  list_approval_requests: 'checked the approval tray',
  request_payment_approval: 'filed a payment for approval',
  request_purchase_order: 'filed a purchase order for approval',
  save_draft_message: 'saved a draft',
  update_ticket_status: 'updated the ticket',
  add_ticket_note: 'pinned a note',
}

export type LiveState = { mode: 'idle' | 'thinking' | 'tool' | 'handoff' | 'done' | 'error'; text: string }

/** What each agent is doing right now, from the newest events of one run. */
export function liveStates(events: AgentEvent[]): Record<AgentName, LiveState> {
  const out = Object.fromEntries(AGENT_ORDER.map((a) => [a, { mode: 'idle', text: 'Off the clock' }])) as Record<AgentName, LiveState>
  for (const e of events) {
    if (!isAgent(e.agent)) continue
    const a = e.agent
    switch (e.event) {
      case 'agent_started': out[a] = { mode: 'thinking', text: 'Reading the brief…' }; break
      case 'model_response': out[a] = { mode: 'thinking', text: e.said ? truncate(e.said, 70) : 'Thinking…' }; break
      case 'tool_call':
        if (e.tool && e.tool !== 'final_result') out[a] = { mode: 'tool', text: cap(TOOL_LABELS[e.tool] ?? e.tool) }
        if (e.tool === 'final_result') out[a] = { mode: 'thinking', text: 'Writing up the report…' }
        break
      case 'tool_result': if (e.tool !== 'delegate') out[a] = { mode: 'thinking', text: 'Reading the result…' }; break
      case 'delegation': {
        const to = e.tool_args?.to
        out[a] = e.result?.startsWith('refused')
          ? { mode: 'thinking', text: 'Hand-off refused, carrying on' }
          : { mode: 'handoff', text: `Waiting on ${to && isAgent(to) ? AGENTS[to].label : to}` }
        break
      }
      case 'agent_finished': out[a] = e.stop_reason === 'completed' ? { mode: 'done', text: 'Done' } : { mode: 'error', text: `Stopped: ${e.stop_reason}` }; break
    }
  }
  return out
}

/** Edges that carried a hand-off in this run (from -> to). */
export function handoffs(events: AgentEvent[]): { from: AgentName; to: AgentName; live: boolean }[] {
  const finished = new Set<string>()
  const edges: { from: AgentName; to: AgentName; key: string }[] = []
  for (const e of events) {
    if (e.event === 'delegation' && isAgent(e.agent) && e.tool_args?.to && isAgent(e.tool_args.to) && !e.result?.startsWith('refused')) {
      edges.push({ from: e.agent, to: e.tool_args.to, key: `${e.tool_args.to}@${e.depth + 1}` })
    }
    if (e.event === 'agent_finished' && isAgent(e.agent)) finished.add(`${e.agent}@${e.depth}`)
  }
  return edges.map(({ from, to, key }) => ({ from, to, live: !finished.has(key) }))
}

export const money = (n: number | null | undefined) =>
  n === null || n === undefined ? '—' : n.toLocaleString('en-US', { style: 'currency', currency: 'USD' })

export const truncate = (s: string, n: number) => (s.length > n ? s.slice(0, n - 1) + '…' : s)
export const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1)

export const STATUS_LABEL: Record<string, string> = {
  open: 'Open', in_progress: 'In progress', waiting_on_approval: 'Waiting on you', blocked: 'Blocked', resolved: 'Resolved',
}

export const TYPE_LABEL: Record<string, string> = {
  customer_order: 'Customer order', rent_notice: 'Rent notice', price_override: 'Price override', unpaid_bill: 'Unpaid bill',
}
