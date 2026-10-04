import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { API_BASE, api } from './api'
import type { AgentEvent, Approval, BossDecision, Cash, RunInfo, Ticket, TicketDetail } from './api'
import { STATUS_LABEL, TYPE_LABEL } from './agents'
import { TicketRail } from './components/TicketRail'
import { FloorMap } from './components/FloorMap'
import { Feed } from './components/Feed'
import { ShiftReport } from './components/ShiftReport'
import { ApprovalTray } from './components/ApprovalTray'
import { CashRegister } from './components/CashRegister'
import { BoardPanel } from './components/BoardPanel'

type Tab = 'floor' | 'report' | 'board'
interface Toast { id: number; text: string; tone: 'pink' | 'good' | 'bad' }

const prettyDate = (iso?: string) =>
  iso ? new Date(`${iso}T12:00:00`).toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' }) : '—'

export default function App() {
  const [tickets, setTickets] = useState<Ticket[]>([])
  const [cash, setCash] = useState<Cash | null>(null)
  const [approvals, setApprovals] = useState<Approval[]>([])
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [detail, setDetail] = useState<TicketDetail | null>(null)
  const [activeRun, setActiveRun] = useState<RunInfo | null>(null)
  const [viewRunId, setViewRunId] = useState<string | null>(null)
  const [runIds, setRunIds] = useState<string[]>([])
  const [events, setEvents] = useState<AgentEvent[]>([])
  const [decisions, setDecisions] = useState<Record<string, BossDecision>>({})
  const [tab, setTab] = useState<Tab>('floor')
  const [toasts, setToasts] = useState<Toast[]>([])
  const [offline, setOffline] = useState(false)
  const [justResolved, setJustResolved] = useState<number | null>(null)
  const [confirmReset, setConfirmReset] = useState(false)
  const [managerNote, setManagerNote] = useState('')
  const [signer, setSigner] = useState(() => localStorage.getItem('cc-signer') ?? '')
  const latest = useRef<string | null>(null)
  const prevStatus = useRef<Record<number, string>>({})

  const toast = useCallback((text: string, tone: Toast['tone'] = 'pink') => {
    const id = Date.now() + Math.random()
    setToasts((t) => [...t, { id, text, tone }])
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 5200)
  }, [])

  // ---------------------------------------------------------------- shop state (tickets, cash, approvals)
  const refreshShop = useCallback(async () => {
    try {
      const [t, c, a] = await Promise.all([api.tickets(), api.cash(), api.approvals()])
      for (const tk of t.tickets) {
        const before = prevStatus.current[tk.id]
        if (before && before !== 'resolved' && tk.status === 'resolved') {
          setJustResolved(tk.id)
          toast(`Ticket ${tk.id} resolved`, 'good')
        }
        prevStatus.current[tk.id] = tk.status
      }
      setTickets(t.tickets); setCash(c); setApprovals(a.requests); setOffline(false)
      setSelectedId((cur) => cur ?? t.tickets.find((x) => x.is_open)?.id ?? t.tickets[0]?.id ?? null)
    } catch {
      setOffline(true)
    }
  }, [toast])

  useEffect(() => {
    refreshShop()
    api.runs().then((runs) => {
      const r = runs.find((x) => x.status === 'running')
      if (r) { setActiveRun(r); setSelectedId(r.ticket_id); setViewRunId(r.run_id) }
      const d: Record<string, BossDecision> = {}
      runs.forEach((x) => x.decision && (d[x.run_id] = x.decision))
      setDecisions(d)
    }).catch(() => setOffline(true))
  }, [refreshShop])

  useEffect(() => {
    const id = setInterval(refreshShop, activeRun ? 2500 : 6000)
    return () => clearInterval(id)
  }, [refreshShop, activeRun])

  // ---------------------------------------------------------------- selected ticket: detail + which run we're watching
  const loadDetail = useCallback(async (id: number) => {
    try { setDetail(await api.ticket(id)) } catch { /* shown as offline by refreshShop */ }
  }, [])

  useEffect(() => {
    if (selectedId === null) return
    loadDetail(selectedId)
    if (activeRun?.ticket_id === selectedId) { setViewRunId(activeRun.run_id); return }
    api.events({ ticket_id: selectedId, limit: 1000 }).then((r) => {
      const ids = [...new Set(r.events.filter((e) => e.event === 'run_started').map((e) => e.run_id))] // team runs only, not human notes
      setRunIds(ids)
      setViewRunId(ids[ids.length - 1] ?? null)
    })
  }, [selectedId, activeRun, loadDetail])

  // ---------------------------------------------------------------- events for the run on screen (live while it runs)
  useEffect(() => {
    setEvents([]); latest.current = null
    if (!viewRunId) return
    let stop = false
    const live = activeRun?.run_id === viewRunId
    const pull = async () => {
      try {
        const r = await api.events({ run_id: viewRunId, after: latest.current ?? undefined, limit: 1000 })
        if (stop || !r.events.length) return
        latest.current = r.latest
        setEvents((prev) => [...prev, ...r.events])
      } catch { /* keep polling */ }
    }
    pull()
    const id = live ? setInterval(pull, 1200) : undefined
    return () => { stop = true; if (id) clearInterval(id) }
  }, [viewRunId, activeRun])

  // ---------------------------------------------------------------- watch the active run until it finishes
  useEffect(() => {
    if (!activeRun || activeRun.status !== 'running') return
    const id = setInterval(async () => {
      try {
        const r = await api.runInfo(activeRun.run_id)
        if (r.status === 'running') return
        clearInterval(id)
        // one last pull so the feed has the final steps
        const tail = await api.events({ run_id: r.run_id, after: latest.current ?? undefined, limit: 1000 })
        if (tail.events.length) { latest.current = tail.latest; setEvents((p) => [...p, ...tail.events]) }
        if (r.decision) setDecisions((d) => ({ ...d, [r.run_id]: r.decision! }))
        setActiveRun(null)
        setRunIds((ids) => (ids.includes(r.run_id) ? ids : [...ids, r.run_id]))
        await refreshShop(); loadDetail(r.ticket_id)
        if (r.status === 'completed' && r.decision) {
          const st = r.decision.final_status
          if (st === 'resolved') setJustResolved(r.ticket_id)
          toast(`Ticket ${r.ticket_id}: ${STATUS_LABEL[st]}${st === 'waiting_on_approval' ? '. Sign the tray to close it.' : ''}`, st === 'resolved' ? 'good' : 'pink')
        } else {
          toast(`Run on ticket ${r.ticket_id} stopped: ${r.error ?? 'unknown error'}`, 'bad')
        }
        setTab('report')
      } catch { /* backend blip; try again */ }
    }, 1500)
    return () => clearInterval(id)
  }, [activeRun, refreshShop, loadDetail, toast])

  // ---------------------------------------------------------------- actions
  async function startRun() {
    if (selectedId === null) return
    try {
      const r = await api.run(selectedId)
      setActiveRun(r); setViewRunId(r.run_id); setTab('floor')
      toast(`Ticket ${selectedId} handed to the Boss`)
    } catch (e) { toast((e as Error).message, 'bad') }
  }

  async function doReset() {
    setConfirmReset(false)
    try {
      await api.reset()
      prevStatus.current = {}
      setDecisions({}); setJustResolved(null)
      await refreshShop()
      if (selectedId !== null) loadDetail(selectedId)
      setViewRunId(null); setRunIds([])
      toast('Database reset to the original shop. Fresh start.', 'good')
    } catch (e) { toast((e as Error).message, 'bad') }
  }

  async function sendWithNote() {
    if (selectedId === null || !managerNote.trim() || !signer.trim()) return
    try {
      await api.note(selectedId, signer, managerNote.trim())
      setManagerNote('')
      toast(`Your note is pinned to ticket ${selectedId}`)
      await startRun()
    } catch (e) { toast((e as Error).message, 'bad') }
  }

  function onSigner(name: string) {
    setSigner(name); localStorage.setItem('cc-signer', name)
  }

  const selected = tickets.find((t) => t.id === selectedId) ?? null
  const running = !!activeRun && activeRun.run_id === viewRunId
  const decision = viewRunId ? decisions[viewRunId] ?? null : null
  const runIndex = viewRunId ? runIds.indexOf(viewRunId) : -1
  const ticketApprovals = useMemo(() => approvals.filter((a) => a.ticket_id === selectedId).length, [approvals, selectedId])

  return (
    <div className="desk">
      <div className="glow" aria-hidden />
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden>CC</span>
          <div>
            <h1>Campus Customs <span>Back Office</span></h1>
            <p className="brand-sub">57 Broadway · the after-hours desk</p>
          </div>
        </div>
        <div className="shop-date">
          <span className="label">Shop date</span>
          <span className="value">{prettyDate(cash?.today)}</span>
        </div>
        <div className="top-actions">
          <span className={`conn ${offline ? 'off' : 'on'}`} title={API_BASE}>{offline ? 'Backend offline' : 'Live · gpt-6-luna'}</span>
          {confirmReset ? (
            <span className="confirm">Wipe the board and restore the original shop?
              <button className="btn-danger" onClick={doReset}>Reset</button>
              <button className="btn-ghost" onClick={() => setConfirmReset(false)}>Cancel</button></span>
          ) : (
            <button className="btn-ghost" disabled={!!activeRun} onClick={() => setConfirmReset(true)}>Reset shop</button>
          )}
        </div>
      </header>

      {offline && <div className="offline">Can't reach the backend at {API_BASE}. Start it from backend/ with <code>uvicorn main:app --reload --port 8000</code>.</div>}

      <main className="layout">
        <TicketRail tickets={tickets} selectedId={selectedId} runningTicketId={activeRun?.ticket_id ?? null}
                    justResolved={justResolved} onSelect={(id) => { setSelectedId(id); setTab(activeRun?.ticket_id === id ? 'floor' : tab) }} />

        <section className="stage">
          {selected ? (
            <>
              <div className={`stage-head status-${selected.status}`}>
                <div>
                  <span className="kicker">Ticket {selected.id} · {TYPE_LABEL[selected.type] ?? selected.type}</span>
                  <h2>{selected.subject} <span className="from">from {selected.requester}</span></h2>
                  <p className="stage-note">{selected.notes}</p>
                </div>
                <div className="stage-cta">
                  <span className={`status-chip big chip-${selected.status}`}>{STATUS_LABEL[selected.status]}</span>
                  <button className="btn-run" onClick={startRun} disabled={!!activeRun || selected.status === 'resolved'}>
                    {activeRun?.ticket_id === selected.id ? <><span className="spinner" /> Team is working…</>
                      : activeRun ? `Busy on ticket ${activeRun.ticket_id}`
                      : selected.status === 'resolved' ? 'Resolved' : runIds.length ? 'Send to the team again' : 'Send to the team'}
                  </button>
                </div>
                {selected.status === 'resolved' && <span className="stamp big stamp-in">Resolved</span>}
              </div>

              {!activeRun && (selected.status === 'blocked' || (selected.status === 'in_progress' && runIds.length > 0)) && (
                <div className="manager-note">
                  <div className="mn-head">
                    <span className="kicker">The team is waiting on your call</span>
                    <span className="muted">Pinned to the ticket as a note from {signer.trim() || 'you'} (manager); the Boss reads it on the next run.</span>
                  </div>
                  <textarea value={managerNote} onChange={(e) => setManagerNote(e.target.value)} maxLength={1000} rows={3}
                            placeholder="e.g. No restock: offer the club what's on hand and close the ticket." />
                  <div className="mn-actions">
                    {!signer.trim() && <span className="hint">Type your name in the approval tray first.</span>}
                    <button className="btn-run small" disabled={!managerNote.trim() || !signer.trim()} onClick={sendWithNote}>Pin note & send back to the team</button>
                  </div>
                </div>
              )}

              <FloorMap events={events} running={running} />

              <nav className="tabs" role="tablist">
                {(['floor', 'report', 'board'] as Tab[]).map((t) => (
                  <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? 'on' : ''} onClick={() => setTab(t)}>
                    {t === 'floor' ? (running ? '● Live floor' : 'Floor talk') : t === 'report' ? 'Shift report' : `Board${ticketApprovals ? ` · ${ticketApprovals} to sign` : ''}`}
                  </button>
                ))}
                {runIds.length > 1 && !running && (
                  <select value={viewRunId ?? ''} onChange={(e) => setViewRunId(e.target.value)} className="run-pick">
                    {runIds.map((id, i) => <option key={id} value={id}>Run {i + 1} of {runIds.length}</option>)}
                  </select>
                )}
                {runIndex >= 0 && runIds.length === 1 && !running && <span className="run-pick quiet">Run 1</span>}
              </nav>

              <div className="tab-body">
                {tab === 'floor' && <Feed events={events} running={running} />}
                {tab === 'report' && <ShiftReport events={events} decision={decision} ticketStatus={selected.status} />}
                {tab === 'board' && <BoardPanel detail={detail} />}
              </div>
            </>
          ) : <div className="feed empty"><p className="empty-big">Pick a ticket from the spike.</p></div>}
        </section>

        <aside className="side">
          <CashRegister cash={cash} />
          <ApprovalTray approvals={approvals} balance={cash?.balance ?? null} signer={signer} onSigner={onSigner}
                        onSelectTicket={(id) => { setSelectedId(id); setTab('board') }}
                        onDecided={async (msg, paid) => {
                          toast(msg, paid ? 'good' : 'pink')
                          await refreshShop()
                          if (selectedId !== null) loadDetail(selectedId)
                        }} />
        </aside>
      </main>

      <div className="toasts" aria-live="polite">
        {toasts.map((t) => <div key={t.id} className={`toast ${t.tone}`}>{t.text}</div>)}
      </div>
    </div>
  )
}
