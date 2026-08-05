/**
 * The one place the console says anything about whether the system is live.
 *
 * Every word of it comes off `GET /cycle` — including "not running", which is
 * what the suite's own configuration (`GLASSBOX_CYCLE_SECONDS=0`) produces and
 * what a developer with the interval deliberately set will see. There is no
 * fallback value anywhere in this file: if the payload is missing, the strip
 * says it could not ask.
 */
import { useCycle } from '../cycle'
import { whenShort } from '../format'

export function SystemStrip() {
  const { state, unavailable } = useCycle()

  if (!state) {
    return (
      <div className="strip">
        <span className="dot unknown" />
        <span>engine status unknown — {unavailable}</span>
      </div>
    )
  }

  const running = state.scheduler_running
  const last = state.recent_ticks?.[state.recent_ticks.length - 1]

  return (
    <div className="strip">
      <span className={`dot ${running ? 'live' : 'idle'}`} />
      <span>
        {running
          ? <>scheduler <b>running</b>{state.interval_seconds
              ? <> every <b>{state.interval_seconds}s</b></> : null}</>
          : <>scheduler <b>not running</b>
              {state.interval_seconds === 0
                ? <span className="dim"> — interval is 0, which disables it</span>
                : null}</>}
      </span>
      <span className="sep">│</span>
      <span>watermark <b>{state.frontier ? whenShort(state.frontier) : '—'}</b>
        <span className="dim"> event time</span></span>
      {last && (
        <>
          <span className="sep">│</span>
          <span>
            last tick{' '}
            {last.ran
              ? <><b>{last.decisions ?? 0}</b> decisions
                  {last.duration_ms != null && <span className="dim"> in {Math.round(Number(last.duration_ms))} ms</span>}</>
              : <span className="dim">did nothing — {last.reason ?? 'no reason given'}</span>}
          </span>
        </>
      )}
      {state.started_at && (
        <>
          <span className="sep">│</span>
          <span className="dim">up since {whenShort(state.started_at)}</span>
        </>
      )}
    </div>
  )
}
