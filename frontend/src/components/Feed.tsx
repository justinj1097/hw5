import { useEffect, useRef } from 'react'
import type { AgentEvent } from '../api'
import { AGENTS, TOOL_LABELS, isAgent } from '../agents'

interface Props {
  events: AgentEvent[]
  running: boolean
}

type Item =
  | { kind: 'divider'; e: AgentEvent; text: string }
  | { kind: 'brief'; e: AgentEvent }
  | { kind: 'say'; e: AgentEvent }
  | { kind: 'tool'; e: AgentEvent; result?: AgentEvent }
  | { kind: 'handoff'; e: AgentEvent }
  | { kind: 'retry'; e: AgentEvent }
  | { kind: 'done'; e: AgentEvent }

function build(events: AgentEvent[]): Item[] {
  const items: Item[] = []
  const open: { item: Extract<Item, { kind: 'tool' }>; agent: string; depth: number }[] = []
  for (const e of events) {
    switch (e.event) {
      case 'run_started': items.push({ kind: 'divider', e, text: 'Ticket handed to the team' }); break
      case 'run_finished': items.push({ kind: 'divider', e, text: `Run ${e.stop_reason === 'completed' ? 'finished' : 'stopped: ' + e.stop_reason}` }); break
      case 'agent_started': if (e.depth === 0) items.push({ kind: 'brief', e }); break // hand-offs already show the brief
      case 'model_response': if (e.said) items.push({ kind: 'say', e }); break
      case 'tool_call':
        if (e.tool && e.tool !== 'final_result') {
          const item = { kind: 'tool' as const, e }
          items.push(item)
          open.push({ item, agent: e.agent, depth: e.depth })
        }
        break
      case 'tool_result': {
        const i = open.findIndex((o) => o.agent === e.agent && o.depth === e.depth && o.item.e.tool === e.tool)
        if (i >= 0) { open[i].item.result = e; open.splice(i, 1) }
        break
      }
      case 'delegation': items.push({ kind: 'handoff', e }); break
      case 'validation_retry': items.push({ kind: 'retry', e }); break
      case 'agent_finished': items.push({ kind: 'done', e }); break
    }
  }
  return items
}

const time = (ts: string) => new Date(ts).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', second: '2-digit' })

function argText(args?: Record<string, string>) {
  if (!args) return ''
  return Object.entries(args)
    .filter(([k]) => !['requested_by', 'drafted_by', 'updated_by', 'author', 'body', 'note', 'reason'].includes(k))
    .map(([k, v]) => `${k}=${v}`).join(', ')
}

function pretty(raw?: string) {
  if (!raw) return ''
  try { return JSON.stringify(JSON.parse(raw), null, 2) } catch { return raw }
}

function Who({ e }: { e: AgentEvent }) {
  if (!isAgent(e.agent)) return null
  const m = AGENTS[e.agent]
  return (
    <span className="who" style={{ ['--agent' as string]: m.color }}>
      <span className="who-glyph">{m.glyph}</span>{m.label}
      {e.depth > 0 && <span className="who-depth">via {e.chain?.slice(0, -1).map((c) => (isAgent(c) ? AGENTS[c].label : c)).join(' → ')}</span>}
    </span>
  )
}

export function Feed({ events, running }: Props) {
  const items = build(events)
  const end = useRef<HTMLDivElement>(null)
  const box = useRef<HTMLDivElement>(null)
  const stick = useRef(true)

  useEffect(() => {
    if (stick.current) end.current?.scrollIntoView({ block: 'end', behavior: 'smooth' })
  }, [items.length])

  if (!items.length) {
    return (
      <div className="feed empty">
        <p className="empty-big">The floor is quiet.</p>
        <p>Send this ticket to the team and you'll see every hand-off, tool call, and word they say, live.</p>
      </div>
    )
  }

  return (
    <div className="feed" ref={box} onScroll={(ev) => {
      const el = ev.currentTarget
      stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80
    }}>
      {items.map((it, i) => {
        const indent = { ['--depth' as string]: it.e.depth, ['--agent' as string]: isAgent(it.e.agent) ? AGENTS[it.e.agent].color : '#ff4fa3' }
        switch (it.kind) {
          case 'divider':
            return <div key={i} className="feed-divider"><span>{it.text} · {time(it.e.timestamp)}</span></div>
          case 'brief':
            return (
              <div key={i} className="feed-row brief" style={indent}>
                <Who e={it.e} /><span className="feed-time">{time(it.e.timestamp)}</span>
                <p className="brief-text">{it.e.said}</p>
              </div>
            )
          case 'say':
            return (
              <div key={i} className="feed-row say" style={indent}>
                <Who e={it.e} /><span className="feed-time">{time(it.e.timestamp)}</span>
                <p className="bubble">{it.e.said}</p>
              </div>
            )
          case 'tool':
            return (
              <div key={i} className="feed-row tool" style={indent}>
                <details className="tool-chip">
                  <summary>
                    <span className="tool-dot" />
                    <span className="tool-verb">{TOOL_LABELS[it.e.tool!] ?? it.e.tool}</span>
                    <code>{it.e.tool}({argText(it.e.tool_args)})</code>
                    {!it.result && running && <span className="tool-wait">…</span>}
                  </summary>
                  {it.result && <pre className="tool-result">{pretty(it.result.result)}</pre>}
                </details>
              </div>
            )
          case 'handoff': {
            const to = it.e.tool_args?.to
            const refused = it.e.result?.startsWith('refused')
            return (
              <div key={i} className={`feed-row handoff ${refused ? 'refused' : ''}`} style={indent}>
                <Who e={it.e} />
                <span className="handoff-arrow">⟶</span>
                {to && isAgent(to) ? (
                  <span className="who" style={{ ['--agent' as string]: AGENTS[to].color }}><span className="who-glyph">{AGENTS[to].glyph}</span>{AGENTS[to].label}</span>
                ) : <span>{to}</span>}
                <span className="feed-time">{time(it.e.timestamp)}</span>
                <p className="handoff-task">{refused ? it.e.result : it.e.tool_args?.task}</p>
              </div>
            )
          }
          case 'retry':
            return <div key={i} className="feed-row retry" style={indent}><Who e={it.e} /> <span>was asked to fix its answer: {it.e.result}</span></div>
          case 'done':
            return (
              <div key={i} className="feed-row done" style={indent}>
                <Who e={it.e} /><span className="done-tag">{it.e.stop_reason === 'completed' ? 'wrapped up' : `stopped (${it.e.stop_reason})`}</span>
                <span className="feed-time">{time(it.e.timestamp)}</span>
                {it.e.said && <p className="bubble final">{it.e.said}</p>}
              </div>
            )
        }
      })}
      {running && <div className="typing"><span /><span /><span /></div>}
      <div ref={end} />
    </div>
  )
}
