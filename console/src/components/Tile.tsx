/**
 * A KPI tile, rendered entirely from its payload.
 *
 * **No tile copy is written in this file.** `label`, `basis`, `caveat`,
 * `requires`, the unit, the numerator and the denominator all come off
 * `kpis.v1`, which is the only place a window is defined (`contract/kpis.py`).
 * Writing the copy here would put a second description of what a number means
 * next to the number, and the two would agree until the day the view changed.
 *
 * §11 flags two console strings that outran the system. One of them lives here:
 * *deltas implying a measured prior period*. The rule this file follows is that
 * a delta renders only when `delta_pct` is non-null, and it renders WITH the
 * baseline window that produced it. When the dataset does not reach back a full
 * window, `KpiSet.baseline_available` is false and the payload carries
 * `baseline_absent_reason` — which is printed, because "−41%" against nothing is
 * the one kind of KPI that is worse than no KPI.
 *
 * WHAT IS FOLDED AWAY, AND WHAT IS NOT. Nine tiles each carrying a definition,
 * an arithmetic note and a window is a wall of prose, and a wall of prose is
 * read by nobody — which costs the transparency it was written for. So the
 * explanation and the window sit behind a `<details>`, closed by default.
 *
 * The CAVEAT does not. A caveat is the tile saying the number is not what it
 * looks like — "we planted this fraud", "a script wrote these verdicts" — and a
 * disclosure a reader has to open is a disclosure most readers never see.
 * Folding the definition away is a density decision; folding the caveat away
 * would be an honesty one. Same for the value, the delta and the denominator: a
 * rate is never shown without what it was computed over.
 */
import { duration, when, whenShort } from '../format'
import type { KpiSet, KpiTile, ReasonCodeValue } from '../api/types'
import { TILE_COPY, plainCaveat } from '../copy/tiles'

/** Reason code → its published gloss and direction, from `GET /reference`. */
export type ReasonCodes = Record<string, ReasonCodeValue>

export function TileView({ tile, reasonCodes }: {
  tile: KpiTile
  reasonCodes?: ReasonCodes
}) {
  return (
    <div className="tile">
      <div className="tile-label">{tile.label}</div>

      <div className="row" style={{ alignItems: 'baseline', gap: 10 }}>
        <Value tile={tile} />
        <Delta tile={tile} />
      </div>

      {tile.numerator !== null && tile.numerator !== undefined
        && tile.denominator !== null && tile.denominator !== undefined && (
        <div className="tile-denominator">
          {tile.numerator} / {tile.denominator}
        </div>
      )}

      {tile.parts && tile.parts.length > 0 && (
        <div className="tile-parts">
          {tile.parts.map((p) => {
            // The trends tile's parts ARE reason codes, so a part whose label
            // the vocabulary knows gets its gloss. Looked up rather than
            // switched on the tile key: any tile that starts emitting reason
            // codes as parts is explained for free, and one that never does
            // simply never matches.
            const code = reasonCodes?.[p.label]
            return (
              <div className="tile-part" key={p.label}>
                <span>
                  {p.label}
                  {code?.direction === 'mitigating' && (
                    <span className="chip mitigating" title="argues for the customer">
                      mitigating
                    </span>
                  )}
                  {code?.direction === 'veto' && (
                    <span className="chip mitigating" title="capped the action">veto</span>
                  )}
                  {code?.description && (
                    <div className="tiny dim">{code.description}</div>
                  )}
                </span>
                <b>
                  {p.value === null || p.value === undefined ? '—' : format(String(p.value), p.unit)}
                  {p.numerator !== null && p.numerator !== undefined
                    && p.denominator !== null && p.denominator !== undefined && (
                    <span className="dim" style={{ fontWeight: 400 }}>
                      {' '}({p.numerator}/{p.denominator})
                    </span>
                  )}
                </b>
              </div>
            )
          })}
        </div>
      )}

      <div className="tile-foot">
        {/* Out front, always. See the header: a caveat behind a disclosure is a
            caveat most readers never open. */}
        {tile.synthetic && (
          <div className="tile-caveat">
            ⚠ synthetic — {plainCaveat(tile.caveat)
              ?? 'this number was produced by the fixtures, not observed'}
          </div>
        )}
        {!tile.synthetic && tile.caveat && (
          <div className="tile-caveat">⚠ {plainCaveat(tile.caveat)}</div>
        )}

        {/* Folded, because it is the same shape on all nine and reads as a wall
            when nine of them are on screen at once. Still in the DOM, still
            findable, one click away. */}
        <details className="tile-detail">
          <summary>what this measures</summary>
          <div className="tile-detail-body">
            <Explanation tile={tile} />
            <div className="dim">
              {whenShort(tile.window_start)} → {whenShort(tile.window_end)}
            </div>
          </div>
        </details>
        {/* `requires` is deliberately not rendered. It names the internal
            milestone that made the tile computable — "§9 dedup + §10 population
            scoring; the denominator is decisions.alert_routing, which did not
            exist before 0023" — which is archaeology to everyone who has not
            read the plan. It stays ON THE WIRE: it is part of kpis.v1, and
            removing a published field to tidy a screen is a contract change. */}
      </div>
    </div>
  )
}

/**
 * What the tile means and how it was computed — two short lines where the
 * payload's `basis` was one long one.
 *
 * Falls back to `basis` for any tile `copy/tiles.ts` has not been taught, so a
 * tile added server-side arrives explaining itself rather than silent.
 */
function Explanation({ tile }: { tile: KpiTile }) {
  const copy = TILE_COPY[tile.key]
  if (!copy) return <div>{tile.basis}</div>
  return (
    <>
      <div className="tile-means">{copy.means}</div>
      <div className="dim">{copy.computed}</div>
    </>
  )
}

function Value({ tile }: { tile: KpiTile }) {
  if (tile.value === null || tile.value === undefined) {
    return <div className="tile-value absent">no value — nothing in this window</div>
  }
  return <div className="tile-value">{format(String(tile.value), tile.unit)}</div>
}

export function format(value: string, unit: string): string {
  const n = Number(value)
  if (unit === 'percent') return `${trim(n)}%`
  if (unit === 'seconds') return duration(n)
  return trim(n)
}

function trim(n: number): string {
  if (Number.isInteger(n)) return n.toLocaleString()
  return n.toFixed(2).replace(/\.?0+$/, '')
}

/**
 * A delta, or an explicit statement that there is no baseline to compare with.
 * Never a bare percentage.
 */
export function Delta({ tile }: { tile: KpiTile }) {
  if (tile.delta_pct === null || tile.delta_pct === undefined) {
    return <span className="tiny dim">no prior window</span>
  }
  const n = Number(tile.delta_pct)
  const dir = n > 0 ? 'up' : n < 0 ? 'down' : ''
  return (
    <span className={`tile-delta ${dir}`}
          title={`against ${whenShort(tile.baseline_start)} → ${whenShort(tile.baseline_end)}`}>
      {n > 0 ? '+' : ''}{trim(n)}%
      <span className="dim"> vs {whenShort(tile.baseline_start)}</span>
    </span>
  )
}

/** The window header, and — when there is no baseline — the payload's own reason. */
export function KpiWindow({ set }: { set: KpiSet }) {
  return (
    <div className="notice">
      <div>
        Window <b>{when(set.window_start)}</b> → <b>{when(set.window_end)}</b>,
        computed at {when(set.as_of)}.
      </div>
      <div style={{ marginTop: 6 }}>
        {set.baseline_available
          ? <>Deltas compare against <b>{when(set.baseline_start)}</b> → <b>{when(set.baseline_end)}</b>
              — the immediately preceding window of the same length, and nothing else.</>
          : <>No deltas on any tile: {set.baseline_absent_reason
              ?? 'the dataset does not reach back a full window before this one'}.</>}
      </div>
    </div>
  )
}
