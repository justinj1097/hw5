import { useState } from 'react'
import { api } from '../api'
import type { Approval } from '../api'
import { AGENTS, isAgent, money } from '../agents'

interface Props {
  approvals: Approval[]
  balance: number | null
  signer: string
  onSigner: (name: string) => void
  onDecided: (msg: string, paid: boolean) => void
  onSelectTicket: (id: number) => void
}

const KIND_LABEL = { invoice: 'Vendor invoice', rent: 'Rent', purchase_order: 'Purchase order' } as const

function Cheque({ a, balance, signer, onDecided, onSelectTicket }: { a: Approval } & Omit<Props, 'approvals' | 'onSigner'>) {
  const [busy, setBusy] = useState<'approve' | 'reject' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [stamp, setStamp] = useState<'paid' | 'void' | null>(null)
  const short = balance !== null && a.amount > balance
  const blocked = a.kind === 'purchase_order' && a.vendor_can_ship === false
  const requester = isAgent(a.requested_by) ? AGENTS[a.requested_by] : null

  async function decide(approve: boolean) {
    setBusy(approve ? 'approve' : 'reject'); setError(null)
    try {
      if (approve) await api.approve(a.id, signer)
      else await api.reject(a.id, signer, 'Rejected on the desk.')
      setStamp(approve ? 'paid' : 'void')
      setTimeout(() => onDecided(approve ? `Paid ${money(a.amount)} to ${a.payee ?? 'payee'}` : `Rejected request #${a.id}`, approve), 1100)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(null)
    }
  }

  return (
    <article className={`cheque ${stamp ? 'stamped' : ''}`}>
      <header className="cheque-top">
        <span className="cheque-bank">CAMPUS CUSTOMS · CHECKING</span>
        <span className="cheque-no">No. {String(a.id).padStart(4, '0')}</span>
      </header>
      <div className="cheque-kind">{KIND_LABEL[a.kind]}{a.ticket_id && (
        <button className="link" onClick={() => onSelectTicket(a.ticket_id!)}> · ticket {a.ticket_id}</button>)}</div>
      <div className="cheque-pay">
        <span className="cheque-label">Pay to the order of</span>
        <span className="cheque-payee">{a.payee ?? '—'}</span>
        <span className="cheque-amount">{money(a.amount)}</span>
      </div>
      <div className="cheque-memo"><span className="cheque-label">Memo</span>{a.what} · {a.reason}</div>
      <div className="cheque-foot">
        <span className="cheque-prep" style={requester ? { ['--agent' as string]: requester.color } : undefined}>
          Prepared by {requester ? <><span className="who-glyph">{requester.glyph}</span>{requester.label}</> : a.requested_by}
        </span>
        <span className="cheque-sign">{stamp === 'paid' ? <span className="signature">{signer}</span> : 'signature'}</span>
      </div>
      {(short || blocked) && !stamp && (
        <p className="cheque-warn">
          {short && <>Checking only has {money(balance)}. The desk will refuse this. </>}
          {blocked && <>Vendor still has unpaid invoice {a.vendor_open_invoice_ids?.join(', ')}; approve that first.</>}
        </p>
      )}
      {error && <p className="cheque-error">{error}</p>}
      {!stamp && (
        <div className="cheque-actions">
          <button className="btn-approve" disabled={!signer.trim() || !!busy} onClick={() => decide(true)}>
            {busy === 'approve' ? 'Paying…' : 'Approve & pay'}
          </button>
          <button className="btn-reject" disabled={!signer.trim() || !!busy} onClick={() => decide(false)}>
            {busy === 'reject' ? '…' : 'Reject'}
          </button>
        </div>
      )}
      {stamp && <span className={`cheque-stamp ${stamp}`}>{stamp === 'paid' ? 'Paid' : 'Void'}</span>}
    </article>
  )
}

export function ApprovalTray({ approvals, balance, signer, onSigner, onDecided, onSelectTicket }: Props) {
  return (
    <section className="tray">
      <div className="panel-head">
        <h2>Approval tray</h2>
        <span className="tray-count">{approvals.length ? `${approvals.length} waiting` : 'empty'}</span>
      </div>
      <label className="signer">
        <span>Signing as</span>
        <input value={signer} onChange={(e) => onSigner(e.target.value)} placeholder="Your name" maxLength={60} />
      </label>
      {!signer.trim() && approvals.length > 0 && <p className="hint">Type your name to sign. Every payment records who approved it.</p>}
      <div className="cheques">
        {approvals.length === 0 && (
          <p className="tray-empty">Nothing to sign. Agents can only <em>prepare</em> payments; they land here for a person to approve.</p>
        )}
        {approvals.map((a) => (
          <Cheque key={a.id} a={a} balance={balance} signer={signer} onDecided={onDecided} onSelectTicket={onSelectTicket} />
        ))}
      </div>
    </section>
  )
}
