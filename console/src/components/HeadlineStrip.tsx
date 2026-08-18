/**
 * Four numbers, above the fold, answering "is anything on fire".
 *
 * WHY THIS IS NOT A TENTH TILE. It restates numbers that are already on the
 * measurement tab, and a restated number is normally exactly what this console
 * refuses. What keeps it honest is that it restates nothing: the label is
 * `tile.label` off the wire, the value goes through the same `format` the tile
 * uses, the delta is the tile's own `Delta` component — so a strip figure and
 * its tile cannot disagree, because they are the same code reading the same
 * payload field. Nothing here is computed.
 *
 * WHICH TILES. Not a fixed three. `PREFERRED` is a preference order and the
 * strip takes the first three tiles in it that have a value — because
 * `action_rates` publishes `value: null` and carries three rates in `parts`,
 * and a strip cell reading "—" is worse than a strip cell showing a different
 * number that exists. A tile the payload did not send is simply skipped.
 *
 * The synthetic mark travels with the number. A headline figure is the one most
 * likely to be read off a slide, so the one place a "⚠ synthetic" must not be
 * dropped for space is here.
 */
import type { KpiSet, KpiTile, QueueEntry } from '../api/types'
import { Delta, format } from './Tile'

/** Preference order, first three with a non-null value win. */
const PREFERRED = [
  'false_positive_rate',
  'median_triage_time',
  'alert_volume',
  'false_negative_rate',
  'rule_precision',
  'score_distribution',
]

export function HeadlineStrip({ queue, set, onOpenMeasurement }: {
  queue: QueueEntry[] | null
  set: KpiSet | null
  onOpenMeasurement: () => void
}) {
  const byKey = new Map((set?.tiles ?? []).map((t) => [t.key, t]))
  const headline: KpiTile[] = []
  for (const key of PREFERRED) {
    if (headline.length === 3) break
    const t = byKey.get(key)
    if (t && t.value !== null && t.value !== undefined) headline.push(t)
  }

  // Off the queue payload, not off a tile: "waiting" means no person has
  // written a verdict, which is the queue's own definition of worked. Counted
  // this way so the number does not change meaning when the analyst ticks
  // "include cases a person has worked".
  const waiting = queue === null ? null : queue.filter((e) => !e.worked_by_analyst).length

  return (
    <div className="headline" role="group" aria-label="headline figures">
      <div className="headline-cell">
        <span className="headline-label">Cases waiting</span>
        <span className="headline-value">
          {waiting === null ? <span className="dim">—</span> : waiting}
        </span>
        <span className="tiny dim">no verdict from a person yet</span>
      </div>

      {/* Spans throughout, not divs: a <button> may contain only phrasing
          content, and the stylesheet is what makes these lay out as blocks. */}
      {headline.map((t) => (
        <button key={t.key} className="headline-cell as-button"
                onClick={onOpenMeasurement}
                title="open the measurement tab">
          <span className="headline-label">{t.label}</span>
          <span className="headline-value">{format(String(t.value), t.unit)}</span>
          <span className="tiny dim">
            <Delta tile={t} />
            {t.synthetic && <span className="tile-caveat"> ⚠ synthetic</span>}
          </span>
        </button>
      ))}
    </div>
  )
}
