/**
 * The nine tiles, in four sections. Every word of every tile comes off
 * `kpis.v1` — see `components/Tile.tsx` for why none of the copy is written
 * here, and `copy/tiles.ts` for the one fenced exception the sections belong to.
 *
 * The sections are a reading order, not a filter: `groupTiles` returns every
 * tile the payload sent, exactly once, and a tile this console has not been
 * taught to file lands in "not yet categorised" rather than disappearing.
 */
import type { KpiSet, ReferenceVocabulary } from '../api/types'
import { ErrorNotice, Loading } from '../components/bits'
import { KpiWindow, TileView } from '../components/Tile'
import type { ReasonCodes } from '../components/Tile'
import { groupTiles } from '../copy/tiles'

/** Offered rather than free-typed. 30 and 90 are on the list on purpose: the
 *  dataset spans thirty days, so both of them make the baseline unavailable and
 *  every delta correctly null — which is a thing worth being able to show. */
const WINDOWS = [1, 7, 14, 30, 90]

/**
 * `2026-01-15T15:00:00Z` → `2026-01-15T15:00`, which is what a datetime-local
 * input wants. Sliced rather than passed through `Date`, deliberately: the
 * payload's instants are UTC and the API reads this value back as UTC, so
 * routing it through the browser's local zone would shift the window by the
 * viewer's offset and nothing on screen would say why.
 */
export function toInput(iso: string | null | undefined): string {
  return iso ? iso.replace(/Z$/, '').slice(0, 16) : ''
}

/** ...and back. The `Z` is the whole point — see above. */
export function toParam(local: string): string {
  return `${local}${local.length === 16 ? ':00' : ''}Z`
}

export interface KpiWindowChoice {
  /** `null` means "unset", and unset is not the same as "the default value".
   *  Neither parameter is sent while it is null, so an untouched screen
   *  produces the byte-identical payload it produced before these controls
   *  existed. */
  windowDays: number | null
  asOf: string | null
}

export function MeasurementPanel({ set, error, loading, choice, onChoice, reference }: {
  set: KpiSet | null
  error: Error | null
  loading: boolean
  choice: KpiWindowChoice
  onChoice: (next: KpiWindowChoice) => void
  reference: ReferenceVocabulary | null
}) {
  // The reason-code vocabulary, so the trends tile can say what a code MEANS and
  // which ones argue for the customer. Served from `ref_reason_code` and derived
  // from what prices each code — the console holds no list of its own, because a
  // list here would disagree with the rules the first time one was repriced.
  // Allowed to be absent: a tile without glosses is the tile that shipped
  // yesterday, and it should not take the screen down.
  const reasonCodes: ReasonCodes = Object.fromEntries(
    (reference?.reason_codes ?? []).map((c) => [c.value, c]))

  const touched = choice.windowDays !== null || choice.asOf !== null

  return (
    <div className="stack">
      <section className="panel" aria-labelledby="window-heading">
        <div className="panel-head">
          <h2 id="window-heading" className="grow">Window</h2>
          <label className="inline" htmlFor="as-of">ending</label>
          <input id="as-of" type="datetime-local" className="compact"
                 value={choice.asOf ?? toInput(set?.as_of)}
                 onChange={(e) => onChoice({ ...choice, asOf: e.target.value || null })} />
          <span className="tiny dim">UTC</span>
          <label className="inline" htmlFor="window-days">spanning</label>
          <select id="window-days" className="compact"
                  value={choice.windowDays ?? 7}
                  onChange={(e) => onChoice({ ...choice, windowDays: Number(e.target.value) })}>
            {WINDOWS.map((d) => (
              <option key={d} value={d}>{d} {d === 1 ? 'day' : 'days'}</option>
            ))}
          </select>
          {touched && (
            <button className="btn" onClick={() => onChoice({ windowDays: null, asOf: null })}>
              Back to defaults
            </button>
          )}
        </div>

        {/* The two behaviours these controls make visible. Both are correct and
            both read as breakage when they are met without warning, which is the
            argument for putting the controls on the screen at all. */}
        <details className="note-detail">
          <summary>two things this window does that look like bugs</summary>
          <ul className="tiny dim">
            <li>
              The window is <b>half-open</b> — after the start, up to and including
              the end. A case dated one minute after the end is outside it. That is
              why an alert raised on the 16th does not appear in a window ending on
              the 15th: not caching, and not a stale view.
            </li>
            <li>
              Above about fifteen days the deltas <b>correctly disappear</b>. The
              data spans thirty, so a longer window has no preceding window of equal
              length to compare against, and a delta computed anyway would be
              arithmetic on a partial period. The header below says so whenever it
              happens.
            </li>
          </ul>
        </details>
      </section>

      <ErrorNotice error={error} />
      {loading && <Loading what="the tiles" />}

      {set && (
        <>
          <KpiWindow set={set} />
          {groupTiles(set.tiles).map(({ group, tiles }) => (
            <section key={group.key} className="tile-group"
                     aria-labelledby={`group-${group.key}`}>
              <div className="tile-group-head">
                <h3 id={`group-${group.key}`}>{group.label}</h3>
                <p className="tiny dim">{group.answers}</p>
              </div>
              <div className="grid tiles">
                {tiles.map((t) => (
                  <TileView key={t.key} tile={t} reasonCodes={reasonCodes} />
                ))}
              </div>
            </section>
          ))}
        </>
      )}
    </div>
  )
}
