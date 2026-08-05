/**
 * THE score bar. One implementation, three payloads.
 *
 * `AlertDetail`, `SimulatedDecision` and `AuthorizationOutcome` all carry
 * `Signal[]` from alert.v1's `$defs` closure, unmodified and deliberately
 * (decision 8), so this component renders all three. A second bar is the failure
 * mode decision 13 exists to prevent — `persist.ranked_signals` was made public
 * so a simulated bar and a stored bar could not be ordered differently — and
 * writing one here would reintroduce it one level up, where the server's tests
 * cannot see it.
 *
 * Two things it will not do:
 *
 *   - It never computes a score. The score is a field; this adds the signals up
 *     only to CHECK them, exactly, and says so on screen. If the two disagree
 *     the bar says the payload does not add up rather than quietly rendering
 *     whichever number is bigger. That should be unreachable — `AlertDetail`'s
 *     validator raises and the API returns 500 — and it is rendered anyway,
 *     because the one place an invariant is worth restating is the surface whose
 *     entire proposition is that it holds.
 *   - It never hides a mitigator. §13's constraint 3 is enforced on the server
 *     by `CopilotAnswer` refusing to be built; the bar is where a reader would
 *     notice it being broken, and a bar that sorted by magnitude or truncated to
 *     the top three would break it silently. Rank order, all of them.
 */
import { decAbs, decEq, decNum, decSum, signed, when, DIRECTION_CLASS } from '../format'
import type { Signal } from '../api/types'

export function ScoreBar({ signals, score }: { signals: Signal[]; score: string }) {
  const contributions = signals.map((s) => String(s.contribution))
  const total = decSum(contributions)
  const adds = decEq(total, score)

  // Widths from magnitudes: a −9 is nine points of evidence like a +9 is, and
  // the bar shows how much of the argument each signal carries.
  const magnitudes = contributions.map((c) => decNum(decAbs(c)))
  const span = magnitudes.reduce((a, b) => a + b, 0)

  return (
    <div>
      {signals.length === 0 ? (
        <>
          <div className="bar-empty" aria-label="empty score bar" />
          <div className="bar-sum">
            no signals — nothing to show on the bar
          </div>
        </>
      ) : (
        <>
          <div className="bar" role="img"
               aria-label={`score bar, ${signals.length} signals summing to ${total}`}>
            {signals.map((s, i) => (
              <div
                key={`${s.rank}-${s.feature_key ?? i}`}
                className={`bar-seg ${DIRECTION_CLASS[s.direction] ?? ''}`}
                style={{ width: span ? `${(magnitudes[i] / span) * 100}%` : 0 }}
                title={`${signed(String(s.contribution))} ${s.feature_key ?? s.direction}`}
              />
            ))}
          </div>
          <div className={adds ? 'bar-sum' : 'notice bad'} data-testid="bar-sum">
            {adds
              ? `${contributions.map(signed).join(' ')} = ${total}`
              : `signals sum to ${total} but the score is ${score} — this payload does not add up`}
          </div>
        </>
      )}

      <div className="signals">
        {signals.map((s, i) => (
          <div key={`${s.rank}-${s.feature_key ?? i}`}
               className={`signal ${DIRECTION_CLASS[s.direction] ?? ''}`}>
            <div className="pts">{signed(String(s.contribution))}</div>
            <div className="text">
              <div>{s.human_text}</div>
              <div className="meta">
                {s.feature_key && <span>{s.feature_key}</span>}
                {s.feature_value !== null && s.feature_value !== undefined && (
                  <span>= {formatValue(s.feature_value)}</span>
                )}
                {s.value_as_of && <span>as of {when(s.value_as_of)}</span>}
                {s.asserted_by_rules && s.asserted_by_rules.length > 0 && (
                  <span>asserted by {s.asserted_by_rules.join(', ')}</span>
                )}
              </div>
            </div>
            <div>
              <span className={`chip ${DIRECTION_CLASS[s.direction] ?? ''}`}>{s.direction}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

function formatValue(value: unknown): string {
  if (typeof value === 'number') {
    return Number.isInteger(value) ? String(value) : value.toFixed(2)
  }
  if (typeof value === 'boolean') return value ? 'true' : 'false'
  return String(value)
}

/**
 * The empty pool, named rather than left as a blank space.
 *
 * `TXN-48251` scores 0 with no signals: `12 − 9 − 6 − 4 = −7`, and consolidation
 * drops a pool that does not net positive rather than clamping the score while
 * still showing the bar, because clamping would break `sum(signals) == score`.
 * A console that rendered that as an empty box would lose the most interesting
 * decision in the dataset.
 */
export function EmptyPoolNote() {
  return (
    <div className="notice">
      Nothing on the bar, and that is the answer. A mitigator is a deduction from
      an accusation; when the deductions consume the accusation there is no
      accusation left to publish, so consolidation drops the pool whole — score 0,
      empty signal set. Clamping the score to zero while still listing the signals
      would break <code>sum(signals) == score</code>.
    </div>
  )
}
