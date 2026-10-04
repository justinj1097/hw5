import { useEffect, useRef, useState } from 'react'
import type { Cash } from '../api'
import { money } from '../agents'

/** Tweens a number toward its new value so a payment visibly "counts down". */
function useTween(target: number | null, ms = 1200) {
  const [shown, setShown] = useState(target)
  const from = useRef(target)
  useEffect(() => {
    if (target === null) return
    const start = from.current ?? target
    if (start === target) { setShown(target); return }
    const t0 = performance.now()
    let raf = 0
    const tick = (t: number) => {
      const p = Math.min(1, (t - t0) / ms)
      const eased = 1 - Math.pow(1 - p, 3)
      setShown(start + (target - start) * eased)
      if (p < 1) raf = requestAnimationFrame(tick)
      else from.current = target
    }
    raf = requestAnimationFrame(tick)
    return () => { cancelAnimationFrame(raf); from.current = target }
  }, [target, ms])
  return shown
}

export function CashRegister({ cash }: { cash: Cash | null }) {
  const balance = cash?.balance ?? null
  const shown = useTween(balance)
  const prev = useRef(balance)
  const [dropped, setDropped] = useState(false)

  useEffect(() => {
    if (balance !== null && prev.current !== null && balance < prev.current) {
      setDropped(true)
      const t = setTimeout(() => setDropped(false), 1600)
      prev.current = balance
      return () => clearTimeout(t)
    }
    prev.current = balance
  }, [balance])

  if (!cash) return <section className="register"><p className="muted">Connecting to the till…</p></section>
  const start = cash.balance + cash.total_paid // cash only goes out, so this is where the shop started
  const usedPct = start ? (cash.total_paid / start) * 100 : 0
  const pendingPct = start ? (Math.min(cash.pending_approval_total, cash.balance) / start) * 100 : 0
  const after = cash.checking_after_all_pending

  return (
    <section className={`register ${dropped ? 'dropped' : ''}`}>
      <div className="panel-head">
        <h2>Checking</h2>
        <span className="as-of">as of {cash.as_of}</span>
      </div>
      <div className="balance" aria-live="polite">{money(shown)}</div>
      <div className="meter" title="Paid out · pending approval · free">
        <span className="meter-paid" style={{ width: `${usedPct}%` }} />
        <span className="meter-pending" style={{ width: `${pendingPct}%` }} />
      </div>
      <dl className="register-lines">
        <div><dt>Paid out</dt><dd>{money(cash.total_paid)}</dd></div>
        <div><dt>Waiting on approval</dt><dd>{money(cash.pending_approval_total)}</dd></div>
        <div className={after < 0 ? 'neg' : ''}><dt>Left if all approved</dt><dd>{money(after)}</dd></div>
      </dl>
      {after < 0 && <p className="register-warn">Pending requests add up to more than checking holds. You'll have to choose; the desk refuses overdrafts.</p>}
      <div className="tape">
        <div className="tape-title">Register tape</div>
        {cash.payments.length === 0 && <div className="tape-line muted">No money has left the shop.</div>}
        {[...cash.payments].reverse().map((p) => (
          <div key={p.id} className="tape-line">
            <span>#{p.id} {p.kind.replace('_', ' ')}</span>
            <span className="tape-amt">−{money(p.amount)}</span>
            <span className="tape-sub">{p.paid_at} · signed {p.approved_by}</span>
          </div>
        ))}
        <div className="tape-line total"><span>Opening</span><span className="tape-amt">{money(start)}</span></div>
      </div>
    </section>
  )
}
