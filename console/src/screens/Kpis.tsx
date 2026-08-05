/** The nine tiles. Every word of every tile comes off `kpis.v1` — see
 *  `components/Tile.tsx` for why none of the copy is written here. */
import { api } from '../api/client'
import type { KpiSet } from '../api/types'
import { ErrorNotice, Loading } from '../components/bits'
import { KpiWindow, TileView } from '../components/Tile'
import { useAsync } from '../useAsync'

export function KpiScreen() {
  const { data, error, loading } = useAsync<KpiSet>(() => api.kpis(), [])

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

      <ErrorNotice error={error} />
      {loading && <Loading what="the tiles" />}

      {data && (
        <div className="stack">
          <KpiWindow set={data} />
          <div className="grid tiles">
            {data.tiles.map((t) => <TileView key={t.key} tile={t} />)}
          </div>
        </div>
      )}
    </div>
  )
}
