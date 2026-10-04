import type { AgentEvent, AgentName } from '../api'
import { AGENTS, AGENT_ORDER, handoffs, liveStates } from '../agents'

interface Props {
  events: AgentEvent[]
  running: boolean
}

// The org map: Boss up top, the four specialists along the bottom, every pair connectable (full connectivity).
export function FloorMap({ events, running }: Props) {
  const states = liveStates(events)
  const edges = handoffs(events)
  const pairs: [AgentName, AgentName][] = []
  AGENT_ORDER.forEach((a, i) => AGENT_ORDER.slice(i + 1).forEach((b) => pairs.push([a, b])))
  const used = (a: AgentName, b: AgentName) => edges.find((e) => (e.from === a && e.to === b) || (e.from === b && e.to === a))

  return (
    <div className="floor">
      <svg className="floor-lines" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden>
        {pairs.map(([a, b]) => {
          const e = used(a, b)
          const A = AGENTS[a], B = AGENTS[b]
          return (
            <line key={`${a}-${b}`} x1={A.x} y1={A.y} x2={B.x} y2={B.y}
                  className={e ? (e.live && running ? 'edge live' : 'edge used') : 'edge'}
                  style={e ? { stroke: AGENTS[e.from].color } : undefined} />
          )
        })}
      </svg>
      {AGENT_ORDER.map((name) => {
        const meta = AGENTS[name]
        const st = states[name]
        return (
          <div key={name} className={`agent-node mode-${st.mode}`} style={{ left: `${meta.x}%`, top: `${meta.y}%`, ['--agent' as string]: meta.color }}>
            <div className="avatar" aria-hidden>
              <span className="avatar-glyph">{meta.glyph}</span>
              {st.mode !== 'idle' && st.mode !== 'done' && <span className="avatar-pulse" />}
              {st.mode === 'done' && <span className="avatar-check">✓</span>}
            </div>
            <div className="agent-tag">
              <strong>{meta.label}</strong>
              <span className="agent-now">{st.text}</span>
            </div>
          </div>
        )
      })}
    </div>
  )
}
