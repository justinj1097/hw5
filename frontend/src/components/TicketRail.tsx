import type { Ticket } from '../api'
import { STATUS_LABEL, TYPE_LABEL } from '../agents'

interface Props {
  tickets: Ticket[]
  selectedId: number | null
  runningTicketId: number | null
  justResolved: number | null
  onSelect: (id: number) => void
}

function detailLine(t: Ticket) {
  if (t.sku) return `${t.qty ?? '?'} × ${t.sku} · ${t.size}`
  if (t.lease_id) return `Lease #${t.lease_id}`
  if (t.invoice_id) return `Invoice #${t.invoice_id}`
  return t.notes ?? ''
}

export function TicketRail({ tickets, selectedId, runningTicketId, justResolved, onSelect }: Props) {
  const open = tickets.filter((t) => t.is_open).length
  return (
    <aside className="rail">
      <div className="rail-head">
        <h2>Ticket spike</h2>
        <span className="rail-count">{open} open · {tickets.length - open} resolved</span>
      </div>
      <ol className="ticket-list">
        {tickets.map((t, i) => (
          <li key={t.id} style={{ ['--tilt' as string]: `${[-0.6, 0.5, -0.3, 0.4][i % 4]}deg` }}>
            <button
              className={`ticket status-${t.status} ${selectedId === t.id ? 'selected' : ''} ${runningTicketId === t.id ? 'running' : ''}`}
              onClick={() => onSelect(t.id)}
              aria-pressed={selectedId === t.id}
            >
              <span className="ticket-stub">
                <span className="stub-label">No.</span>
                <span className="stub-num">{t.id}</span>
              </span>
              <span className="ticket-body">
                <span className="ticket-type">{TYPE_LABEL[t.type] ?? t.type}</span>
                <span className="ticket-subject">{t.subject}</span>
                <span className="ticket-from">{t.requester}</span>
                <span className="ticket-detail">{detailLine(t)}</span>
                <span className="ticket-foot">
                  <span className={`status-chip chip-${t.status}`}>
                    {runningTicketId === t.id ? 'Team working…' : STATUS_LABEL[t.status] ?? t.status}
                  </span>
                  {t.pending_approvals > 0 && <span className="mini-badge">{t.pending_approvals} to sign</span>}
                  {t.drafts > 0 && <span className="mini-badge soft">{t.drafts} draft{t.drafts > 1 ? 's' : ''}</span>}
                </span>
              </span>
              {t.status === 'resolved' && <span className={`stamp ${justResolved === t.id ? 'stamp-in' : ''}`}>Resolved</span>}
            </button>
          </li>
        ))}
      </ol>
    </aside>
  )
}
