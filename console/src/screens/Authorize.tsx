/**
 * The live authorization screen. This is the demo, and it commits.
 *
 * **O6: it posts to the real `POST /authorize`.** A simulate-only version would
 * have been safe and would only ever have been able to say a charge *would be*
 * declined — which is the exact gap session 4b was built to close, reopened in
 * the surface most people will judge the system by. What makes the real one
 * defensible is that nothing about it is disguised: the response carries
 * `persisted: true`, the committed row carries `source = 'authorized'`, and the
 * case it raises is in the queue a click away.
 *
 * **It must never be used as a what-if.** `/simulate/transaction` is the
 * endpoint that answers a hypothetical, and the two are deliberately separate —
 * one typo away from each other is exactly what the split exists to prevent. So
 * this screen says what it is going to do before it does it, and points at the
 * simulator for the other question.
 *
 * `scripts/demo_burst.py --http` is the working specification and the charge
 * below is the same one, for the same reasons: five card-not-present charges on
 * `CARD-4417` twenty seconds apart so all five sit inside `card_cnp_count`'s
 * ninety-second window, from a device nobody has seen so
 * `device_first_seen_min` is measured from the first of them, dated at the
 * fixtures' reference instant so the card has a history to be measured against.
 */
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { api } from '../api/client'
import type { AuthorizationOutcome, KpiSet } from '../api/types'
import { ErrorNotice } from '../components/bits'
import { ActionRow, DecisionFrame, EvidenceBlock } from '../components/DecisionFrame'
import { ScoreBar } from '../components/ScoreBar'
import { when } from '../format'
import { useSession } from '../session'

const GAP_SECONDS = 20
const BURST = 5

/** ~1,412 km from CUST-OKAFOR's home in New York, which is what clears R-114's
 *  1,400 km line on `session_geo_jump_km`. */
const AWAY = { lat: '40.71', lon: '-90.78' }

interface Charge {
  txn_id: string
  occurred_at: string
  amount: string
  card_id: string
  account_id: string
  customer_id: string
  merchant_id: string
  mcc: string
  channel: string
  entry_mode: string
  txn_country: string
  txn_lat: string
  txn_lon: string
  ip_address: string
  device_id: string
  billing_country: string
}

function charge(prefix: string, n: number, startIso: string, device: string): Charge {
  const start = new Date(startIso)
  const at = new Date(start.getTime() + n * GAP_SECONDS * 1000)
  return {
    txn_id: `${prefix}-${n + 1}`,
    occurred_at: at.toISOString(),
    amount: '312.00',
    card_id: 'CARD-4417',
    account_id: 'ACC-4417',
    customer_id: 'CUST-OKAFOR',
    merchant_id: 'MER-GIFT',
    mcc: '5815',
    channel: 'cnp',
    entry_mode: 'ecom',
    txn_country: 'US',
    txn_lat: AWAY.lat,
    txn_lon: AWAY.lon,
    ip_address: '45.83.12.9',
    device_id: device,
    billing_country: 'US',
  }
}

export function AuthorizeScreen() {
  const { can, principal } = useSession()

  // A prefix per page load, so a second demo does not collide with the first on
  // `transactions.txn_id`. Raw capture is append-only; nothing here overwrites.
  const [prefix] = useState(() => `UI${Date.now().toString(36).slice(-5).toUpperCase()}`)
  const [device] = useState(() => `DEV-UI-${Date.now().toString(36).slice(-5).toUpperCase()}`)

  const [startIso, setStartIso] = useState<string | null>(null)
  const [sent, setSent] = useState<AuthorizationOutcome[]>([])
  const [error, setError] = useState<Error | null>(null)
  const [busy, setBusy] = useState(false)

  // The fixtures' reference instant, learned from `/kpis` rather than assumed.
  // Every window feature (90s, 24h, 30d) is measured against history pinned to
  // 2026-01-15, so a charge dated at wall clock would see a card with no past at
  // all and the burst would score zero for a reason that has nothing to do with
  // the engine.
  useEffect(() => {
    void api.kpis().then(
      (k: KpiSet) => setStartIso((prev) => prev ?? String(k.as_of)),
      () => setStartIso((prev) => prev ?? new Date().toISOString()))
  }, [])

  if (!can('admin')) {
    return (
      <div className="page">
        <div className="page-head"><div className="grow"><h1>Send a charge</h1></div></div>
        <div className="notice bad">
          <b>This needs the admin role.</b> It commits a row to raw capture and
          can decline a real charge.{' '}
          {principal
            ? <>You are signed in as <b>{principal.actor}</b> ({principal.role}).</>
            : 'Sign in with an admin token.'}
        </div>
        <div className="notice" style={{ marginTop: 12 }}>
          To ask what the engine <i>would</i> say about a charge nobody made, use{' '}
          <Link to="/simulate">the simulator</Link>. That is a different endpoint
          on purpose.
        </div>
      </div>
    )
  }

  const send = async (n: number) => {
    if (!startIso) return
    setBusy(true)
    try {
      const outcome = await api.authorize(
        charge(prefix, n, startIso, device) as unknown as Record<string, unknown>)
      setSent((s) => [...s, outcome])
      setError(null)
    } catch (err) { setError(err as Error) } finally { setBusy(false) }
  }

  const sendBurst = async () => {
    setBusy(true)
    setError(null)
    try {
      const collected: AuthorizationOutcome[] = []
      for (let n = sent.length; n < BURST; n += 1) {
        // Sequential on purpose: each charge must see the ones before it. Five
        // parallel requests would race on `card_cnp_count` and the fifth of five
        // could read four.
        const outcome = await api.authorize(
          charge(prefix, n, startIso!, device) as unknown as Record<string, unknown>)
        collected.push(outcome)
        setSent((s) => [...s, outcome])
      }
      if (collected.length === 0) setError(new Error('All five have already been sent.'))
    } catch (err) { setError(err as Error) } finally { setBusy(false) }
  }

  const stopped = sent.find((o) => o.authorization !== 'approved')

  return (
    <div className="page">
      <div className="page-head">
        <div className="grow">
          <h1>Send a charge</h1>
          <p>
            Five card-not-present charges on <span className="id">CARD-4417</span>,
            twenty seconds apart, from a device nobody has seen. Each is
            authorized on its own and the engine decides it <b>before the row is
            committed</b> — so a declined charge is never an approved transaction.
          </p>
        </div>
      </div>

      <div className="notice warn" style={{ marginBottom: 14 }}>
        <b>This writes.</b> Every charge below is committed to raw capture with{' '}
        <code>source = 'authorized'</code>, and a preventive decision commits the
        row as <code>declined</code>. To ask a hypothetical instead, use{' '}
        <Link to="/simulate">the simulator</Link> — it rolls everything back and
        publishes <code>persisted: false</code>.
      </div>

      <div className="panel">
        <div className="spread">
          <div className="row">
            <button className="primary" onClick={() => void sendBurst()}
                    disabled={busy || !startIso || sent.length >= BURST}>
              {busy ? 'authorizing…' : `Send the burst (${BURST} charges)`}
            </button>
            <button onClick={() => void send(sent.length)}
                    disabled={busy || !startIso || sent.length >= BURST}>
              Send one ({sent.length + 1} of {BURST})
            </button>
            {sent.length > 0 && (
              <button className="ghost" onClick={() => { setSent([]); setError(null) }}>
                clear this list
              </button>
            )}
          </div>
          <div className="tiny dim">
            first charge at {startIso ? when(startIso) : '…'} · device{' '}
            <span className="mono">{device}</span>
          </div>
        </div>
        <div className="tiny dim" style={{ marginTop: 10 }}>
          "Clear this list" empties the screen and nothing else. The charges are
          committed; <code>scripts/demo_burst.py --clean</code> is what removes
          them.
        </div>
      </div>

      <ErrorNotice error={error} />

      {sent.length > 0 && (
        <div className="panel" style={{ padding: 0 }}>
          <div className="scroll-x">
            <table>
              <thead>
                <tr>
                  <th>Charge</th><th>Authorization</th><th>Reason</th>
                  <th className="num">Score</th><th>Action</th><th>Case</th>
                  <th>Issued</th><th className="num">ms</th>
                </tr>
              </thead>
              <tbody>
                {sent.map((o) => (
                  <tr key={o.txn_id}>
                    <td className="id">{o.txn_id}</td>
                    <td>
                      <span className={`chip ${o.authorization === 'approved' ? 'allowed' : 'stopped'}`}>
                        {o.authorization}
                      </span>
                    </td>
                    <td className="tiny mono dim">{o.decline_reason ?? '—'}</td>
                    <td className="num"><b>{o.score}</b></td>
                    <td className="tiny">{o.action.taken}</td>
                    <td className="tiny">
                      {o.alert_id
                        ? <Link to={`/alerts/${o.alert_id}`}>#{o.alert_id}</Link>
                        : <span className="dim">—</span>}
                      {o.alert_routing && <div className="tiny dim">{o.alert_routing}</div>}
                    </td>
                    <td className="tiny">
                      {(o.executions ?? []).length === 0 ? <span className="dim">—</span>
                        : (o.executions ?? []).map((e) => (
                            <div key={e.execution_id}>{e.action}/{e.channel}</div>
                          ))}
                    </td>
                    <td className="num tiny">{o.latency_ms ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {stopped && (
        <div style={{ marginTop: 14 }}>
          <DecisionFrame persisted stopped>
            <div className="score-head">
              <div className="score-value">{stopped.score}</div>
              <span className={`chip band-${stopped.band}`}>{stopped.band}</span>
              <div className="small muted">
                <span className="id">{stopped.txn_id}</span> — committed as{' '}
                <b>{stopped.authorization}</b>
                {stopped.decline_reason && <> with <code>{stopped.decline_reason}</code></>}
              </div>
            </div>

            <div style={{ margin: '14px 0' }}>
              <ActionRow action={stopped.action} />
            </div>

            <ScoreBar signals={stopped.signals ?? []} score={String(stopped.score)} />

            <div style={{ marginTop: 16 }}>
              <EvidenceBlock evidence={stopped.evidence} />
            </div>

            <div className="notice" style={{ marginTop: 14 }}>
              {stopped.basis}
            </div>

            {stopped.alert_id && (
              <div className="notice good" style={{ marginTop: 12 }}>
                This raised case{' '}
                <Link to={`/alerts/${stopped.alert_id}`}><b>#{stopped.alert_id}</b></Link>,
                and it is in <Link to="/">the queue</Link> now.
              </div>
            )}
          </DecisionFrame>
        </div>
      )}

      <div className="panel" style={{ marginTop: 14 }}>
        <h4 style={{ marginBottom: 8 }}>Why the numbers land where they do</h4>
        <ul className="small muted" style={{ margin: 0, paddingLeft: 18 }}>
          <li>
            The charges are twenty seconds apart, so all five sit inside{' '}
            <code>card_cnp_count</code>'s ninety-second window. The fifth is the fifth.
          </li>
          <li>
            The device is one nobody has seen. A device is <i>observed</i> rather
            than opened, so the first charge registers it — and{' '}
            <code>device_first_seen_min</code> is measured from that instant.
          </li>
          <li>
            They are dated at the fixtures' reference instant rather than at wall
            clock, because every window feature is measured against history pinned
            there. A charge dated today would see a card with no past at all.
          </li>
          <li>
            A <code>challenge</code> commits as <code>declined</code> with{' '}
            <code>step_up_required</code>: a step-up nobody has answered has not
            been passed, and that is how 3DS behaves.
          </li>
        </ul>
      </div>
    </div>
  )
}
