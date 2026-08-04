"""How far the background cycle has consumed, in EVENT time.

`IncrementalRunner.run_feature(as_of, since)` has been watermark-driven since
Week 2 and there has never been anywhere to keep the watermark: `run_features.py`
takes `--since` as an argument and a human supplies it. That is fine for a batch
rebuild and useless for something that has to answer "what has changed since I
last looked" without being told.

**Event time, never wall clock**, and this is not a preference. The fixtures are
pinned to `GLASSBOX_NOW` — 2026-01-15 — and an ingested charge is written at the
instant it claims to have occurred at, which is near that. A wall-clock
watermark would sit seven months later than every row it is supposed to gate,
so the first tick would consume everything and every tick after it would consume
nothing. The same reasoning W3.6 gives for using `first_event_at` rather than
`alerts.created_at`.

**Advanced only after the work that consumed it committed.** A crashed tick
re-reads its window rather than skipping it. That is at-least-once and it is
deliberate: the feature runner is append-only, so re-running a window rewrites
the same values with a later `computed_at`, and §9's folding means a
re-evaluated subject produces the same alert count on the second pass as on the
first. Both halves are already idempotent, which is what makes the cheap choice
the correct one — exactly-once here would be machinery bought for nothing.
"""
from __future__ import annotations

from datetime import datetime

import psycopg

from ..db import fetch_one, fetch_value

FEATURES = "features"
GRAPH = "graph"
LANES = ("inline_sync", "async")

# Every stage the cycle runs, in the order it runs them. Named here rather than
# in the scheduler because the watermark table is what makes the order
# resumable: a tick that dies between features and lanes must not advance the
# feature watermark past work the lanes never saw.
STREAMS = (GRAPH, FEATURES, *LANES)


def read(conn: psycopg.Connection, stream: str) -> datetime | None:
    """The event instant this stream has consumed up to. NULL means never run,
    and the next pass over it is a full one."""
    row = fetch_one(
        conn, "SELECT watermark_at FROM ingest_watermark WHERE stream = %s",
        (stream,))
    return row["watermark_at"] if row else None


def advance(conn: psycopg.Connection, stream: str, to: datetime | None) -> None:
    """Move a stream forward. Never backward.

    `GREATEST` rather than an assignment because a manual `run_cycle --as-of
    <historical>` and a live tick can both write here, and a replay of January
    must not persuade the scheduler that it has not yet seen July. A backward
    watermark would make the next tick re-evaluate the whole population, which
    is survivable, and re-issue nothing, which is only true by accident of §9.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ingest_watermark (stream, watermark_at, last_run_at, runs)
            VALUES (%s, %s, now(), 1)
            ON CONFLICT (stream) DO UPDATE
               SET watermark_at = GREATEST(
                       ingest_watermark.watermark_at,
                       COALESCE(EXCLUDED.watermark_at, ingest_watermark.watermark_at)),
                   last_run_at  = now(),
                   runs         = ingest_watermark.runs + 1
            """,
            (stream, to))


def frontier(conn: psycopg.Connection) -> datetime | None:
    """The newest event this system has any record of.

    The cycle's `as_of`, and it is read from the DATA rather than from a clock
    for the reason at the top of this file. `entity_links.first_seen` is in the
    union because an ingested `opened_on` edge is what makes a ring knowable,
    and a cycle bounded below it would build the cluster and then decline to
    score it.
    """
    return fetch_value(
        conn,
        """
        SELECT max(t) AS t FROM (
            SELECT max(occurred_at) AS t FROM transactions
            UNION ALL SELECT max(occurred_at) FROM events
            UNION ALL SELECT max(first_seen)  FROM entity_links
        ) s
        """)


def state(conn: psycopg.Connection) -> list[dict]:
    """Every stream, for the operator surface. Published so a console can show
    that something is running rather than asserting it."""
    from ..db import fetch_all
    return fetch_all(
        conn,
        "SELECT stream, watermark_at, last_run_at, runs, note "
        "  FROM ingest_watermark ORDER BY stream")
