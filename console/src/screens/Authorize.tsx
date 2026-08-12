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
import { EmptyPoolNote, ScoreBar } from '../components/ScoreBar'
import { when } from '../format'
import { useSession } from '../session'

const GAP_SECONDS = 20

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
  txn_lat?: string
  txn_lon?: string
  ip_address?: string
  device_id: string
  billing_country: string
}

interface Scenario {
  key: string
  title: string
  shows: string
  /** What the engine returns, checked against a fresh build rather than hoped for. */
  expect: string
  /** Why it lands there — the argument that it is not staged. */
  why: React.ReactNode
  count: number
  charge: (prefix: string, n: number, startIso: string, device: string) => Charge
}

/**
 * THREE SCENARIOS, because one shows only that the system can say no.
 *
 * A demo that only ever declines is a demo of a system that would decline
 * everything; the veto and the quiet case are what make the decline mean
 * something. All three are `inline_sync` transaction subjects, which is not a
 * simplification — `POST /authorize` judges a charge the way a terminal does,
 * and no other subject type can be judged that way.
 *
 * Every expectation below was run against a freshly bootstrapped database.
 */
const SCENARIOS: Scenario[] = [
  {
    key: 'burst',
    title: 'Card-testing burst',
    shows: 'prevention — the system stopping something',
    expect: 'four approved, the fifth scores 87 and is DECLINED with step_up_required',
    count: 5,
    charge: (prefix, n, startIso, device) => ({
      txn_id: `${prefix}-${n + 1}`,
      occurred_at: new Date(new Date(startIso).getTime() + n * GAP_SECONDS * 1000).toISOString(),
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
    }),
    why: (
      <ul className="small muted" style={{ margin: 0, paddingLeft: 18 }}>
        <li>
          Twenty seconds apart, so all five sit inside <code>card_cnp_count</code>'s
          ninety-second window. The fifth is the fifth.
        </li>
        <li>
          A device nobody has seen. A device is <i>observed</i> rather than opened,
          so the first charge registers it and <code>device_first_seen_min</code> is
          measured from that instant — which is what makes it fire on the fifth.
        </li>
        <li>
          Dated at the sample data's reference instant, not at wall clock. Every
          window feature is measured against history pinned there, and a charge
          dated today would look at a card with no history at all.
        </li>
        <li>
          A <code>challenge</code> commits as <code>declined</code> with{' '}
          <code>step_up_required</code>: a step-up nobody has answered has not been
          passed, and that is how 3DS behaves.
        </li>
      </ul>
    ),
  },
  {
    key: 'veto',
    title: 'The veto',
    shows: 'the system arguing FOR the customer',
    expect: 'score 0, allow, approved — T-021 establishes the exonerating evidence',
    count: 1,
    charge: (prefix, _n, startIso) => ({
      txn_id: `${prefix}-V1`,
      occurred_at: startIso,
      amount: '86.20',
      card_id: 'CARD-9954',
      account_id: 'ACC-9954',
      customer_id: 'CUST-MENSAH',
      merchant_id: 'MER-REST',
      mcc: '5812',
      channel: 'pos',
      entry_mode: 'chip_pin',
      txn_country: 'PT',
      device_id: 'DEV-501',
      billing_country: 'GB',
    }),
    why: (
      <ul className="small muted" style={{ margin: 0, paddingLeft: 18 }}>
        <li>
          A chip-and-PIN charge in Portugal on a customer whose 222 previous
          transactions are all in Great Britain. On its own that is a new country,
          and new countries cost points.
        </li>
        <li>
          But this customer has a travel booking on file, so{' '}
          <code>recent_travel_purchase</code> is true — and{' '}
          <b>T-021, "Foreign POS after flight purchase"</b>, is a <i>veto</i> rule.
          It establishes that the location is explained.
        </li>
        <li>
          The result is a score of <b>0</b> and an <code>allow</code>. The bar is
          empty because a net-negative pool is dropped whole rather than shown as a
          negative score — the evidence for the customer consumed the evidence
          against them.
        </li>
        <li>
          This is the half of the system that never gets demonstrated. A model that
          only accumulates suspicion cannot do it.
        </li>
      </ul>
    ),
  },
  {
    key: 'quiet',
    title: 'Ordinary traffic',
    shows: 'the control case — the system saying nothing at all',
    expect: 'score 0, no signals, approved, no case raised',
    count: 1,
    charge: (prefix, _n, startIso) => ({
      txn_id: `${prefix}-Q1`,
      occurred_at: startIso,
      amount: '42.10',
      card_id: 'CARD-9954',
      account_id: 'ACC-9954',
      customer_id: 'CUST-MENSAH',
      merchant_id: 'MER-214',
      mcc: '5732',
      channel: 'pos',
      entry_mode: 'chip_pin',
      txn_country: 'GB',
      device_id: 'DEV-501',
      billing_country: 'GB',
    }),
    why: (
      <ul className="small muted" style={{ margin: 0, paddingLeft: 18 }}>
        <li>
          Home country, a merchant this customer uses, a known device, an
          unremarkable amount, chip-and-PIN. Nothing here is unusual and nothing
          fires.
        </li>
        <li>
          <b>Score 0, no signals, no case.</b> Not "low risk" — no evidence was
          found at all, which is a different statement and the screen makes it.
        </li>
        <li>
          This is the case that makes the other two mean something. A detector that
          alerts on everything catches all the fraud too.
        </li>
      </ul>
    ),
  },
]

export function AuthorizeScreen() {
  const { can, principal } = useSession()

  // A prefix per page load, so a second demo does not collide with the first on
  // `transactions.txn_id`. Raw capture is append-only; nothing here overwrites.
  const [prefix] = useState(() => `UI${Date.now().toString(36).slice(-5).toUpperCase()}`)
  const [device] = useState(() => `DEV-UI-${Date.now().toString(36).slice(-5).toUpperCase()}`)

  const [startIso, setStartIso] = useState<string | null>(null)
  const [scenario, setScenario] = useState<Scenario>(SCENARIOS[0])
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

  // The charges this scenario will send, built before anything is sent — which
  // is the change §13 is about. The audience saw a button and then a verdict;
  // the details ARE the argument, and an unseen charge makes a decline look
  // arbitrary.
  const planned: Charge[] = startIso
    ? Array.from({ length: scenario.count }, (_, n) =>
        scenario.charge(prefix, n, startIso, device))
    : []

  /**
   * The one call site of `POST /authorize` in this console, and a test pins it
   * there. Three scenarios share it rather than each owning one: the endpoint
   * commits and can decline a charge, and a second caller is the thing that
   * split is meant to prevent.
   *
   * Sequential, always. Each charge must see the ones before it — five parallel
   * requests would race on `card_cnp_count` and the fifth of five could read
   * four.
   */
  const sendFrom = async (first: number, last: number) => {
    if (!startIso) return
    setBusy(true)
    setError(null)
    try {
      for (let n = first; n < last; n += 1) {
        const outcome = await api.authorize(
          scenario.charge(prefix, n, startIso, device) as unknown as Record<string, unknown>)
        setSent((s) => [...s, outcome])
      }
    } catch (err) { setError(err as Error) } finally { setBusy(false) }
  }

  const pick = (s: Scenario) => { setScenario(s); setSent([]); setError(null) }
  const done = sent.length >= scenario.count
  const stopped = sent.find((o) => o.authorization !== 'approved')
  const shown = stopped ?? sent[sent.length - 1]
  const byId = new Map(sent.map((o) => [o.txn_id, o]))

  return (
    <div className="page">
      <div className="page-head">
        <div className="grow">
          <h1>Send a charge</h1>
          <p>
            Real charges, authorized one at a time. The engine decides each one{' '}
            <b>before the row is committed</b>, so a declined charge is never an
            approved transaction — which is the difference between preventing
            fraud and noticing it.
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
        <div className="row" style={{ flexWrap: 'wrap', gap: 8 }}>
          {SCENARIOS.map((s) => (
            <button key={s.key}
                    className={s.key === scenario.key ? 'primary' : 'btn'}
                    onClick={() => pick(s)} disabled={busy}>
              {s.title}
            </button>
          ))}
        </div>
        <div className="tiny dim" style={{ marginTop: 8 }}>
          <b>{scenario.title}</b> — shows {scenario.shows}.{' '}
          <span className="dim">Expect: {scenario.expect}.</span>
        </div>
      </div>

      {/* The charges themselves, before a single one is sent. */}
      {planned.length > 0 && (
        <div className="panel" style={{ padding: 0, marginTop: 14 }}>
          <div className="panel-head" style={{ padding: '12px 14px 0' }}>
            <h4 className="grow">
              {planned.length === 1 ? 'The charge' : `The ${planned.length} charges`}
            </h4>
            <span className="tiny dim">
              {planned.length > 1
                ? 'only the clock changes — everything else is identical'
                : 'one charge, judged on its own'}
            </span>
          </div>
          <div className="scroll-x">
            <table>
              <thead>
                <tr>
                  <th className="num">#</th><th>Time</th><th className="num">Amount</th>
                  <th>Card</th><th>Merchant</th><th>MCC</th><th>Channel</th>
                  <th>Entry</th><th>Country</th><th>Device</th><th>IP</th>
                  <th>Result</th>
                </tr>
              </thead>
              <tbody>
                {planned.map((c, i) => {
                  const o = byId.get(c.txn_id)
                  return (
                    <tr key={c.txn_id}>
                      <td className="num">{i + 1}</td>
                      {/* The clock is the one column that moves in a burst, so it
                          is the one column marked. */}
                      <td className="tiny mono changing">{c.occurred_at.slice(11, 19)}</td>
                      <td className="num">{c.amount}</td>
                      <td className="tiny id">{c.card_id}</td>
                      <td className="tiny">{c.merchant_id}</td>
                      <td className="tiny mono">{c.mcc}</td>
                      <td className="tiny">{c.channel}</td>
                      <td className="tiny">{c.entry_mode}</td>
                      <td className="tiny">{c.txn_country}</td>
                      <td className="tiny mono">{c.device_id}</td>
                      <td className="tiny mono dim">{c.ip_address ?? '—'}</td>
                      <td className="tiny">
                        {!o ? <span className="dim">not sent</span> : (
                          <>
                            <b>{o.score}</b>{' '}
                            <span className={`chip ${o.authorization === 'approved' ? 'allowed' : 'stopped'}`}>
                              {o.authorization}
                            </span>
                            <div className="tiny dim">
                              {o.action.taken}
                              {o.alert_id && (
                                <> · <Link to={`/alerts/${o.alert_id}`}>#{o.alert_id}</Link></>
                              )}
                            </div>
                          </>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div className="panel" style={{ marginTop: 14 }}>
        <div className="spread">
          <div className="row">
            <button className="primary" onClick={() => void sendFrom(sent.length, scenario.count)}
                    disabled={busy || !startIso || done}>
              {busy ? 'authorizing…'
                : scenario.count === 1 ? 'Send it' : `Send all ${scenario.count}`}
            </button>
            {scenario.count > 1 && (
              <button onClick={() => void sendFrom(sent.length, sent.length + 1)}
                      disabled={busy || !startIso || done}>
                Send one ({Math.min(sent.length + 1, scenario.count)} of {scenario.count})
              </button>
            )}
            {sent.length > 0 && (
              <button className="ghost" onClick={() => { setSent([]); setError(null) }}>
                clear this list
              </button>
            )}
          </div>
          <div className="tiny dim">
            dated {startIso ? when(startIso) : '…'} · device{' '}
            <span className="mono">{device}</span>
          </div>
        </div>
        <div className="tiny dim" style={{ marginTop: 10 }}>
          "Clear this list" empties the screen and nothing else — the charges are
          committed, and each run uses a fresh id prefix so a second run does not
          collide with the first. <code>scripts/demo_burst.py --clean</code> is
          what removes them. There is deliberately no delete button here: this
          console has no endpoint that erases raw capture, and adding one to tidy
          a demo would be the first thing in the system that can make a committed
          charge stop having happened.
        </div>
      </div>

      <ErrorNotice error={error} />

      {/* The full decision frame for whatever the scenario produced. A stopped
          charge if there is one — that is the moment — and otherwise the last
          approved charge, because "score 0, nothing fired" is the entire point
          of two of the three scenarios and hiding it would leave them with no
          visible answer at all. */}
      {shown && (
        <div style={{ marginTop: 14 }}>
          <DecisionFrame persisted stopped={shown.authorization !== 'approved'}>
            <div className="score-head">
              <div className={`score-value ${String(shown.score) === '0' ? 'zero' : ''}`}>
                {shown.score}
              </div>
              <span className={`chip band-${shown.band}`}>{shown.band}</span>
              <div className="small muted">
                <span className="id">{shown.txn_id}</span> — committed as{' '}
                <b>{shown.authorization}</b>
                {shown.decline_reason && <> with <code>{shown.decline_reason}</code></>}
              </div>
            </div>

            <div style={{ margin: '14px 0' }}>
              <ActionRow action={shown.action} />
            </div>

            <ScoreBar signals={shown.signals ?? []} score={String(shown.score)} />
            {(shown.signals ?? []).length === 0 && String(shown.score) === '0' && (
              <div style={{ marginTop: 12 }}><EmptyPoolNote /></div>
            )}

            <div style={{ marginTop: 16 }}>
              <EvidenceBlock evidence={shown.evidence} />
            </div>

            <div className="notice" style={{ marginTop: 14 }}>
              {shown.basis}
            </div>

            {shown.alert_id ? (
              <div className="notice good" style={{ marginTop: 12 }}>
                This raised case{' '}
                <Link to={`/alerts/${shown.alert_id}`}><b>#{shown.alert_id}</b></Link>,
                and it is in <Link to="/">the queue</Link> now.
              </div>
            ) : (
              <div className="notice" style={{ marginTop: 12 }}>
                <b>No case was raised.</b> Nothing scored enough to be worth an
                analyst's time, and the queue is unchanged — which is the outcome
                most traffic should have.
              </div>
            )}
          </DecisionFrame>
        </div>
      )}

      <div className="panel" style={{ marginTop: 14 }}>
        <h4 style={{ marginBottom: 8 }}>Why the numbers land where they do</h4>
        {scenario.why}
      </div>

    </div>
  )
}
