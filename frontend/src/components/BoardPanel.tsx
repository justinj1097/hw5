import type { TicketDetail } from '../api'
import { AGENTS, STATUS_LABEL, isAgent, money } from '../agents'

export function BoardPanel({ detail }: { detail: TicketDetail | null }) {
  if (!detail) return null
  const { ticket, drafts, board_updates, approval_requests } = detail
  return (
    <div className="board">
      <section>
        <h3>The ticket</h3>
        <blockquote className="ticket-note">“{ticket.notes}”<cite>{ticket.requester}</cite></blockquote>
      </section>

      <section>
        <h3>Drafts on the board <span className="muted">never sent; a person decides</span></h3>
        {drafts.length === 0 && <p className="muted">No drafts yet.</p>}
        <div className="drafts">
          {drafts.map((d) => {
            const by = isAgent(d.drafted_by) ? AGENTS[d.drafted_by] : null
            return (
              <article key={d.id} className="envelope" style={by ? { ['--agent' as string]: by.color } : undefined}>
                <div className="env-flap" />
                <div className="env-head"><span>To: <b>{d.recipient}</b></span><span className="env-unsent">DRAFT · NOT SENT</span></div>
                <div className="env-subject">{d.subject}</div>
                <p className="env-body">{d.body}</p>
                <div className="env-by">Drafted by {by ? `${by.glyph} ${by.label}` : d.drafted_by}</div>
              </article>
            )
          })}
        </div>
      </section>

      {approval_requests.length > 0 && (
        <section>
          <h3>Money requests for this ticket</h3>
          <ul className="req-list">
            {approval_requests.map((r) => (
              <li key={r.id}><span className={`req-status req-${r.status}`}>{r.status}</span> #{r.id} {r.kind.replace('_', ' ')} · {money(r.amount)}
                {r.decided_by && <span className="muted"> · {r.status === 'executed' ? 'paid' : 'decided'} by {r.decided_by}</span>}</li>
            ))}
          </ul>
        </section>
      )}

      <section>
        <h3>Board log</h3>
        {board_updates.length === 0 && <p className="muted">Nothing logged yet.</p>}
        <ol className="log">
          {board_updates.map((u) => {
            const by = isAgent(u.author) ? AGENTS[u.author] : null
            return (
              <li key={u.id} style={{ ['--agent' as string]: by?.color ?? '#f3eef2' }}>
                <span className="log-who">{by ? `${by.glyph} ${by.label}` : `✍ ${u.author}`}</span>
                {u.status && <span className={`status-chip chip-${u.status}`}>{STATUS_LABEL[u.status] ?? u.status}</span>}
                <p>{u.note}</p>
              </li>
            )
          })}
        </ol>
      </section>
    </div>
  )
}
