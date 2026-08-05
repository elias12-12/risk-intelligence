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
 */
import { duration, when, whenShort } from '../format'
import type { KpiSet, KpiTile } from '../api/types'

export function TileView({ tile }: { tile: KpiTile }) {
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
          {tile.parts.map((p) => (
            <div className="tile-part" key={p.label}>
              <span>{p.label}</span>
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
          ))}
        </div>
      )}

      <div className="tile-foot">
        <div>
          {whenShort(tile.window_start)} → {whenShort(tile.window_end)}
        </div>
        <div>{tile.basis}</div>
        {tile.synthetic && (
          <div className="tile-caveat">
            ⚠ synthetic — {tile.caveat ?? 'this number was produced by the fixtures, not observed'}
          </div>
        )}
        {!tile.synthetic && tile.caveat && <div className="tile-caveat">⚠ {tile.caveat}</div>}
        <div className="dim">requires {tile.requires}</div>
      </div>
    </div>
  )
}

function Value({ tile }: { tile: KpiTile }) {
  if (tile.value === null || tile.value === undefined) {
    return <div className="tile-value absent">no value — nothing in this window</div>
  }
  return <div className="tile-value">{format(String(tile.value), tile.unit)}</div>
}

function format(value: string, unit: string): string {
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
function Delta({ tile }: { tile: KpiTile }) {
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
