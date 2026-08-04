"""The scheduler — §15's topology, completed.

`architecture.md` §15 describes the prototype as "one service, one database,
**a scheduler**", and for four weeks the last third of that sentence was a
promise. `run_cycle.py` was run by hand, so nothing in this system ever reacted
to anything; §18's open decision 6 (the async cycle period) could not be settled
because nothing had a period to set.

**The period, answered.** §2.2 seeds 15 minutes as a placeholder and says
plainly what it costs: it "sets the floor on detection latency for every network
pattern". That is a production number, chosen against graph-rebuild cost on real
volume. Here the graph is four accounts and the whole cycle takes under a
second, so fifteen minutes would buy nothing and cost the only thing this
prototype actually needs to show — that the system reacts. **Default 30
seconds**, `GLASSBOX_CYCLE_SECONDS`, and `0` turns it off entirely.

Both halves of that are deliberate. A demo wants to watch an alert appear; a
test suite wants nothing running behind its back, and `conftest.py` builds a
database that every test then mutates inside a rolled-back transaction — a
background thread committing into the middle of that would be the least
debuggable failure this project could have. So the scheduler is **off unless
asked**, and `glassbox serve` is what asks.

**A thread, not asyncio.** Every database call in this codebase is synchronous
psycopg and every engine entry point takes a `psycopg.Connection`. Making the
cycle async would mean either a second database driver or `run_in_executor`
around the whole thing, which is a thread with more ceremony. A daemon thread
with its own connection is the honest shape, and it is what the cycle's advisory
lock already assumes.

**Failure is logged and the loop survives it.** A tick that raises rolls back
its own transaction, advances no watermark, and the next tick re-reads the same
window. The alternative — a scheduler that dies on the first bad row — would be
a scheduler nobody could trust to be running, which is worse than not having one.
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .db import connect
from .ingest.cycle import CycleResult, run_cycle

log = logging.getLogger("glassbox.scheduler")

DEFAULT_INTERVAL_SECONDS = 30.0
ENV_INTERVAL = "GLASSBOX_CYCLE_SECONDS"


def interval_seconds() -> float:
    """0 disables the scheduler. Anything else is seconds between ticks."""
    raw = os.environ.get(ENV_INTERVAL)
    if raw is None:
        return DEFAULT_INTERVAL_SECONDS
    try:
        return max(0.0, float(raw))
    except ValueError:
        log.warning("%s=%r is not a number; falling back to %ss",
                    ENV_INTERVAL, raw, DEFAULT_INTERVAL_SECONDS)
        return DEFAULT_INTERVAL_SECONDS


@dataclass
class Tick:
    """One turn, kept for the operator surface. Bounded — see `Scheduler`."""
    at: datetime
    ran: bool
    reason: str | None = None
    alerts: int = 0
    decisions: int = 0
    feature_values: int = 0
    duration_ms: float = 0.0
    error: str | None = None

    @classmethod
    def of(cls, result: CycleResult) -> "Tick":
        lanes = result.lanes.values()
        return cls(
            at=datetime.now(timezone.utc), ran=result.ran, reason=result.reason,
            alerts=sum(t.get("alerts", 0) for t in lanes),
            decisions=sum(t.get("decisions", 0) for t in lanes),
            feature_values=result.feature_values,
            duration_ms=result.duration_ms)


class Scheduler:
    """A daemon thread that turns the engine over on an interval.

    Stopping is co-operative and immediate: the sleep is an `Event.wait`, so
    shutdown does not have to outlast a full interval. That matters more than it
    sounds — a 30-second sleep in a `time.sleep` makes `Ctrl-C` feel broken, and
    a scheduler people kill with `-9` is a scheduler that never gets to finish a
    transaction cleanly.
    """

    def __init__(self, interval: float | None = None, history: int = 20):
        self.interval = interval if interval is not None else interval_seconds()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._history_size = history
        self.ticks: list[Tick] = []
        self.started_at: datetime | None = None

    # ------------------------------------------------------------- lifecycle
    @property
    def enabled(self) -> bool:
        return self.interval > 0

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        if not self.enabled:
            log.info("scheduler disabled (%s=0)", ENV_INTERVAL)
            return False
        if self.running:                                  # pragma: no cover
            return True
        self._stop.clear()
        self.started_at = datetime.now(timezone.utc)
        self._thread = threading.Thread(target=self._loop, name="glassbox-cycle",
                                        daemon=True)
        self._thread.start()
        log.info("scheduler started; every %.1fs", self.interval)
        return True

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    # ------------------------------------------------------------------ work
    def _loop(self) -> None:
        # One tick immediately, then on the interval. A service that starts and
        # then does nothing for thirty seconds looks broken during exactly the
        # thirty seconds somebody is watching it start.
        while not self._stop.is_set():
            self._record(self.tick())
            if self._stop.wait(self.interval):
                break

    def tick(self) -> Tick:
        """One cycle, in its own connection and its own transaction.

        Commits only on success. A tick that raises leaves the watermark where
        it was, so the next one re-reads the same window rather than skipping it.
        """
        try:
            with connect() as conn:
                result = run_cycle(conn)
                if result.ran:
                    conn.commit()
                else:
                    conn.rollback()
                return Tick.of(result)
        except Exception as exc:                          # noqa: BLE001
            log.exception("cycle failed")
            return Tick(at=datetime.now(timezone.utc), ran=False,
                        reason="cycle raised", error=f"{type(exc).__name__}: {exc}")

    def _record(self, tick: Tick) -> None:
        self.ticks.append(tick)
        del self.ticks[:-self._history_size]


# One per process, created lazily by the API's lifespan hook. Module-level
# because the operator endpoint has to be able to ask the same object the
# service started, and threading one through FastAPI's dependency graph would be
# ceremony around a singleton that is a singleton by nature.
_scheduler: Scheduler | None = None


def get() -> Scheduler | None:
    return _scheduler


def start() -> Scheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = Scheduler()
    _scheduler.start()
    return _scheduler


def stop() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.stop()
        _scheduler = None
