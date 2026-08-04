"""One turn of the engine, incremental, resumable, and safe to run twice.

`architecture.md` §15's topology is "one service, one database, **a scheduler**".
The scheduler was the last piece of that sentence that did not exist:
`run_cycle.py` has always been run by hand, which is why §18's decision 6 — the
async cycle period — has stayed open since Week 2. A period you have to pick is
a period you can only pick once something actually ticks.

    graph      →  clusters and members, from entity_links
    features   →  run_population(as_of, since) — the watermark IS the `since`
    inline     →  transactions that did not arrive through /authorize
    async      →  the lane with an evaluation_lag: rings, takeovers

**A tick evaluates what arrived, not the population.** This is the difference
between a scheduler and a batch job, and it is not an optimisation:
`plan_evaluations` plans every subject of every type a lane's rules name, so a
naive tick would re-score 9,844 transactions to notice one new charge — forty
seconds of work, every thirty seconds, forever. `affected_subjects` narrows it
to the entities the arriving rows actually touch: the transactions themselves,
the accounts and cards behind them, and the clusters those accounts belong to.

The narrowing is by SUBJECT, never by rule or by feature. A subject that is
re-evaluated is re-evaluated in full, against its whole history, so an
incremental tick and a full pass produce the same decision for the same subject
— which is the property that makes it safe to be incremental at all.

**Why the inline lane runs here.** A charge arriving through `/authorize` is
decided synchronously and never needs this. One arriving through
`/ingest/transactions` — a settled row, a planted decline burst — has had no
inline decision made about it, and leaving it undecided would put a hole in the
population exactly where a demo puts its evidence.

**Why `as_of` comes from the data.** `watermark.frontier()` is the newest event
on record. A wall clock would place every tick seven months after the fixtures,
which are pinned to `GLASSBOX_NOW`, so the first tick would consume everything
and every tick after it would consume nothing.

**Two ticks cannot overlap.** A Postgres advisory lock, taken with `try` and
never waited on: a tick that finds the lock held returns "already running"
rather than queueing, because a queue of identical cycles is the backlog §9
spends its whole existence preventing at the alert level.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Sequence

import psycopg

from ..db import fetch_all, fetch_value
from ..engine.evaluation import EngineContext, run_lane
from ..features.runner import IncrementalRunner
from ..graph.builder import build as build_graph
from . import watermark

# One arbitrary constant, so two processes serialise on the same integer.
CYCLE_LOCK = 0x6C61_7373          # 'glass'


@dataclass
class CycleResult:
    """What one tick did. Every number is a count of rows it actually wrote."""

    ran: bool
    reason: str | None = None
    as_of: datetime | None = None
    since: datetime | None = None
    clusters: int = 0
    feature_values: int = 0
    lanes: dict[str, dict[str, int]] = field(default_factory=dict)
    duration_ms: float = 0.0


def run_cycle(conn: psycopg.Connection, ctx: EngineContext | None = None,
              force: bool = False) -> CycleResult:
    """Graph, features, both lanes — up to the newest event on record.

    Does NOT commit. The caller owns the transaction, so a tick that dies
    half-way advances no watermark and leaves no partial state, and the next one
    re-reads the same window. That is the whole of the resumability story, and
    it is why every watermark is written after the work it describes.
    """
    started = time.perf_counter()

    if not force and not _take_lock(conn):
        return CycleResult(
            ran=False,
            reason="another cycle is already running; this tick did nothing "
                   "rather than queueing behind it")

    as_of = watermark.frontier(conn)
    if as_of is None:
        return CycleResult(ran=False, reason="no rows in raw capture yet")

    marks = {stream: watermark.read(conn, stream) for stream in watermark.STREAMS}
    behind = [s for s, at in marks.items() if at is None or at < as_of]
    if not behind and not force:
        return CycleResult(
            ran=False, as_of=as_of, since=min(m for m in marks.values() if m),
            reason="nothing has arrived since the last cycle",
            duration_ms=(time.perf_counter() - started) * 1000)

    result = CycleResult(ran=True, as_of=as_of, since=marks[watermark.FEATURES])

    # 1. Graph. Only entity_links move it, and it is cheap when nothing has.
    built = build_graph(conn)
    result.clusters = len(built)
    fresh_clusters = [c.cluster_id for c in built if c.created]
    watermark.advance(conn, watermark.GRAPH, as_of)

    # 2. Features. `since=None` on a stream that has never run is a full pass,
    #    which is correct: a watermark that was never set has consumed nothing.
    runner = IncrementalRunner(conn)
    result.feature_values = sum(
        r.rows_written for r in runner.run_population(as_of, marks[watermark.FEATURES]))
    watermark.advance(conn, watermark.FEATURES, as_of)

    # 3. The lanes. Context is loaded AFTER the graph, because a cluster that
    #    came into existence in step 1 is a subject the planner must be able to
    #    reach in step 3.
    ctx = ctx or EngineContext.load(conn)
    run_id = f"cycle{int(time.time())}"
    for lane in watermark.LANES:
        since = marks[lane]
        subjects: Sequence[str] | None = None
        if since is not None:
            subjects = affected_subjects(conn, since, as_of, extra=fresh_clusters)
            if not subjects:
                # NOT `subject_ids=[]` — `plan_evaluations` treats an empty list
                # as "no filter" and would plan the entire population. An empty
                # affected set means there is nothing to do, and saying so is
                # the difference between a cheap tick and a full re-score.
                result.lanes[lane] = {"evaluations": 0}
                watermark.advance(conn, lane, as_of)
                continue
        result.lanes[lane] = run_lane(conn, lane, as_of, run_id=run_id,
                                      subject_ids=subjects, ctx=ctx)
        watermark.advance(conn, lane, as_of)

    result.duration_ms = (time.perf_counter() - started) * 1000
    return result


def affected_subjects(conn: psycopg.Connection, since: datetime, as_of: datetime,
                      extra: Sequence[str] = ()) -> list[str]:
    """Every subject id the rows arriving in (since, as_of] could have moved.

    Deliberately over-inclusive and deliberately id-shaped rather than
    type-shaped: `plan_evaluations` filters each subject type's query by
    `= ANY(ids)`, so one mixed bag of ids costs nothing and an id that belongs
    to no type simply matches nothing. Narrowing wrongly here would silently
    skip a subject; over-including only costs work.

    What is included, and why each one:

      * the transactions themselves — the inline lane's subjects
      * their card, account, customer, merchant and device — every dimension
        subject type the planner can reach, because a new charge changes what
        the latest transaction on each of them is
      * the clusters those accounts belong to — a ring is re-scored when money
        moves through any member, which is exactly §2.2's cluster cycle
      * `extra`, the clusters this tick's graph build created — they have no
        arriving transaction of their own and would otherwise wait for one
      * the subjects of arriving EVENTS — a password reset moves
        `min_since_password_reset` on an account with no new transaction at all
    """
    rows = fetch_all(
        conn,
        """
        WITH arriving AS (
            SELECT txn_id, card_id, account_id, customer_id, merchant_id, device_id
              FROM transactions
             WHERE occurred_at > %(since)s AND occurred_at <= %(as_of)s
        ),
        touched AS (
            SELECT txn_id       AS id FROM arriving
            UNION SELECT card_id     FROM arriving
            UNION SELECT account_id  FROM arriving
            UNION SELECT customer_id FROM arriving
            UNION SELECT merchant_id FROM arriving
            UNION SELECT device_id   FROM arriving
            UNION SELECT subject_id FROM events
                   WHERE occurred_at > %(since)s AND occurred_at <= %(as_of)s
            UNION SELECT cm.cluster_id
                     FROM cluster_members cm
                    WHERE cm.subject_type = 'account'
                      AND cm.subject_id IN (SELECT account_id FROM arriving)
        )
        SELECT id FROM touched WHERE id IS NOT NULL
        """,
        {"since": since, "as_of": as_of},
    )
    return sorted({r["id"] for r in rows} | set(extra))


def _take_lock(conn: psycopg.Connection) -> bool:
    """Session-scoped advisory lock, released when the connection closes.

    `pg_try_advisory_lock` rather than `pg_advisory_lock`: a tick that waits is a
    tick that has joined a queue, and a queue of identical cycles is the backlog
    §9 spends its whole existence preventing at the alert level.
    """
    return bool(fetch_value(conn, "SELECT pg_try_advisory_lock(%s) AS got",
                            (CYCLE_LOCK,)))
