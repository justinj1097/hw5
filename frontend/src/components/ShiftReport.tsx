import type { AgentEvent, AgentName, BossDecision, TicketStatus } from '../api'
import { AGENTS, AGENT_ORDER, STATUS_LABEL, TOOL_LABELS, isAgent } from '../agents'

interface Props {
  events: AgentEvent[]
  decision: BossDecision | null
  ticketStatus: TicketStatus | null
}

interface AgentSummary {
  name: AgentName
  summaries: string[]
  tools: Map<string, number>
  handedTo: AgentName[]
  tokens: number
  stops: string[]
}

function summarize(events: AgentEvent[]): AgentSummary[] {
  const by = new Map<AgentName, AgentSummary>()
  const get = (a: AgentName) => {
    if (!by.has(a)) by.set(a, { name: a, summaries: [], tools: new Map(), handedTo: [], tokens: 0, stops: [] })
    return by.get(a)!
  }
  for (const e of events) {
    if (!isAgent(e.agent)) continue
    const s = get(e.agent)
    if (e.event === 'tool_call' && e.tool && e.tool !== 'final_result') s.tools.set(e.tool, (s.tools.get(e.tool) ?? 0) + 1)
    if (e.event === 'delegation' && e.tool_args?.to && isAgent(e.tool_args.to) && !e.result?.startsWith('refused')) s.handedTo.push(e.tool_args.to)
    if (e.event === 'model_response') s.tokens += e.tokens ?? 0
    if (e.event === 'agent_finished') {
      if (e.said) s.summaries.push(e.said)
      if (e.stop_reason) s.stops.push(e.stop_reason)
    }
  }
  return AGENT_ORDER.filter((a) => by.has(a)).map((a) => by.get(a)!)
}

export function ShiftReport({ events, decision, ticketStatus }: Props) {
  const rows = summarize(events)
  const finish = [...events].reverse().find((e) => e.event === 'run_finished')
  if (!rows.length) {
    return <div className="report empty"><p className="empty-big">No shift report yet.</p><p>Run the team on this ticket and each agent's work lands here.</p></div>
  }
  const stats = finish?.result?.match(/requests=(\d+) tool_calls=(\d+) delegations=(\d+)/)
  const benched = AGENT_ORDER.filter((a) => !rows.some((r) => r.name === a))

  return (
    <div className="report">
      {decision && (
        <section className="verdict">
          <div className="verdict-head">
            <span className="verdict-kicker">The Boss's call</span>
            <span className="verdict-chips">
              <span className={`status-chip chip-${decision.final_status}`}>{STATUS_LABEL[decision.final_status]}</span>
              {ticketStatus && ticketStatus !== decision.final_status && (
                <><span className="since">now</span><span className={`status-chip chip-${ticketStatus}`}>{STATUS_LABEL[ticketStatus]}</span></>
              )}
            </span>
          </div>
          <p className="verdict-text">{decision.decision}</p>
          {decision.next_steps_for_human.length > 0 && (
            <div className="verdict-steps">
              <h4>For you</h4>
              <ul>{decision.next_steps_for_human.map((s, i) => <li key={i}>{s}</li>)}</ul>
            </div>
          )}
          {decision.facts.length > 0 && (
            <details className="verdict-facts">
              <summary>{decision.facts.length} facts it relied on</summary>
              <table><tbody>{decision.facts.map((f, i) => (
                <tr key={i}><td>{f.label}</td><td className="mono">{f.value}</td><td className="src">{f.source_tool}</td></tr>
              ))}</tbody></table>
            </details>
          )}
        </section>
      )}

      {stats && (
        <div className="run-stats">
          <span><b>{stats[1]}</b> model calls</span>
          <span><b>{stats[2]}</b> tool calls</span>
          <span><b>{stats[3]}</b> hand-offs</span>
          {finish?.tokens ? <span><b>{(finish.tokens / 1000).toFixed(1)}k</b> tokens</span> : null}
          {benched.length > 0 && <span className="benched">Stayed on the bench: {benched.map((b) => AGENTS[b].label).join(', ')}</span>}
        </div>
      )}

      <div className="report-grid">
        {rows.map((r) => {
          const m = AGENTS[r.name]
          return (
            <article key={r.name} className="report-card" style={{ ['--agent' as string]: m.color }}>
              <header>
                <span className="avatar small"><span className="avatar-glyph">{m.glyph}</span></span>
                <div><strong>{m.label}</strong><span className="role">{m.role}</span></div>
              </header>
              {r.summaries.length ? r.summaries.map((s, i) => <p key={i} className="report-summary">{s}</p>)
                : <p className="report-summary muted">Still working…</p>}
              <div className="report-tools">
                {[...r.tools.entries()].map(([t, n]) => (
                  <span key={t} className="tool-pill" title={t}>{TOOL_LABELS[t] ?? t}{n > 1 ? ` ×${n}` : ''}</span>
                ))}
              </div>
              {r.handedTo.length > 0 && (
                <p className="report-handoff">Handed off to {r.handedTo.map((h) => AGENTS[h].label).join(', ')}</p>
              )}
            </article>
          )
        })}
      </div>
    </div>
  )
}
