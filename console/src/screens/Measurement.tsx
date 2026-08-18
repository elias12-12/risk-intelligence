/** The nine tiles. Every word of every tile comes off `kpis.v1` — see
 *  `components/Tile.tsx` for why none of the copy is written here. */
import { useState } from 'react'

import { api } from '../api/client'
import type { KpiSet, ReferenceVocabulary } from '../api/types'
import { ErrorNotice, Loading } from '../components/bits'
import { KpiWindow, TileView } from '../components/Tile'
import type { ReasonCodes } from '../components/Tile'
import { useAsync } from '../useAsync'

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
function toInput(iso: string | null | undefined): string {
  return iso ? iso.replace(/Z$/, '').slice(0, 16) : ''
}

/** ...and back. The `Z` is the whole point — see above. */
function toParam(local: string): string {
  return `${local}${local.length === 16 ? ':00' : ''}Z`
}

export function KpiScreen() {
  // `null` means "unset", and unset is not the same as "the default value".
  // Neither parameter is sent while it is null, so an untouched screen produces
  // the byte-identical payload it produced before these controls existed.
  const [windowDays, setWindowDays] = useState<number | null>(null)
  const [asOf, setAsOf] = useState<string | null>(null)

  const { data, error, loading } = useAsync<KpiSet>(
    () => api.kpis({
      windowDays: windowDays ?? undefined,
      asOf: asOf ? toParam(asOf) : undefined,
    }),
    [windowDays, asOf],
  )

  // The reason-code vocabulary, so the trends tile can say what a code MEANS
  // and which ones argue for the customer. Served from `ref_reason_code` and
  // derived from what prices each code — the console holds no list of its own,
  // because a list here would disagree with the rules the first time one was
  // repriced. Fetched separately and allowed to fail: a tile without glosses is
  // the tile that shipped yesterday, and it should not take the screen down.
  const reference = useAsync<ReferenceVocabulary>(() => api.reference(), [])
  const reasonCodes: ReasonCodes = Object.fromEntries(
    (reference.data?.reason_codes ?? []).map((c) => [c.value, c]))

  const touched = windowDays !== null || asOf !== null

  return (
    <div className="page">
      <div className="page-head">
        <div className="grow">
          <h1>Measurement</h1>
          <p>
            Nine tiles, each computed from stored rows and each naming its window,
            its denominator and what it cannot tell you. Two of them are exact
            against planted ground truth and meaningless beyond it; they say so
            here because they say so on the wire.
          </p>
        </div>
      </div>

      <div className="panel">
        <div className="panel-head">
          <h2 className="grow">Window</h2>
          <label className="tiny dim">
            ending{' '}
            <input type="datetime-local" aria-label="as of"
                   value={asOf ?? toInput(data?.as_of)}
                   onChange={(e) => setAsOf(e.target.value || null)} />
            {' '}UTC
          </label>
          <label className="tiny dim">
            spanning{' '}
            <select aria-label="window days"
                    value={windowDays ?? 7}
                    onChange={(e) => setWindowDays(Number(e.target.value))}>
              {WINDOWS.map((d) => (
                <option key={d} value={d}>{d} {d === 1 ? 'day' : 'days'}</option>
              ))}
            </select>
          </label>
          {touched && (
            <button className="btn" onClick={() => { setWindowDays(null); setAsOf(null) }}>
              Back to defaults
            </button>
          )}
        </div>

        {/* The two behaviours these controls make visible. Both are correct and
            both read as breakage when they are met without warning, which is the
            argument for putting the controls on the screen at all. */}
        <ul className="tiny dim" style={{ margin: '6px 0 0 18px' }}>
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
      </div>

      <ErrorNotice error={error} />
      {loading && <Loading what="the tiles" />}

      {data && (
        <div className="stack">
          <KpiWindow set={data} />
          <div className="grid tiles">
            {data.tiles.map((t) => (
              <TileView key={t.key} tile={t} reasonCodes={reasonCodes} />
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
