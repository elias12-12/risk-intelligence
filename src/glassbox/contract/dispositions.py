"""dispositions.v1 — the analyst's verdict on a case, and its history.

A SIBLING of alert.v1, like queue.v1 and executions.v1. Nothing in models.py is
touched; `alert.v1`'s digest does not move.

This is the first WRITE surface in the project. Three properties are deliberate:

**Append-only, latest wins.** `case_outcomes` has no uniqueness constraint and
this endpoint does not add one. An analyst who changes their mind writes a second
row and the first judgement stays in the record — the same argument the migration
ledger and `feature_values` both make. `v_kpi_cases` publishes the latest as the
case's verdict (Week 5 changed it from first-wins, which was correct only while
the sole writer was a script that wrote each case exactly once).

**`alerts.status` is not touched.** The engine owns the alert lifecycle: it
raises, folds, restates and suppresses. The analyst owns the verdict. Two writers
on one column is how a status becomes unreadable, and the queue does not need it
— it filters on whether a *person* has dispositioned the case (contract/queue.py).

**`decided_at` is real wall-clock time, not the fixture instant.** A human
decision happened when it happened. The consequence on this dataset is worth
knowing rather than papering over: the fixtures are pinned to `GLASSBOX_NOW` in
January, so a disposition written today gives that case a triage time of months.
It does not distort the tile, because `v_kpi_cases` measures the clock to the
FIRST disposition and every fixture case already carries a synthetic one — but a
case dispositioned for the first time by a person will read exactly that way, and
it will be telling the truth.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

import psycopg
from pydantic import BaseModel, ConfigDict, Field

from ..db import fetch_all, fetch_one

STRICT = ConfigDict(extra="forbid", frozen=True)

# The same four `v_kpi_cases` classifies on, and the same four 0029 CHECKs.
# Three layers, one vocabulary.
DispositionValue = Literal[
    "confirmed_fraud", "false_positive", "confirmed_legit", "inconclusive"
]


class DispositionRequest(BaseModel):
    """What an analyst sends. `analyst_id` is deliberately absent: it comes from
    the authenticated principal, never from the body, or the audit column records
    whoever the caller claimed to be."""
    model_config = ConfigDict(extra="forbid")

    disposition: DispositionValue
    notes: str | None = Field(default=None, max_length=4000)
    feeds_retrain: bool = True


class Disposition(BaseModel):
    model_config = STRICT

    outcome_id: int
    alert_id: int
    disposition: DispositionValue
    analyst_id: str | None = None
    source: Literal["analyst", "synthetic"]
    decided_at: datetime
    notes: str | None = None
    feeds_retrain: bool = True


class CaseVerdict(BaseModel):
    """The state of one case's disposition, and every row behind it.

    `verdict` is what the KPI views count; `history` is why it says that. A
    surface that showed only the verdict would hide a correction, which is the
    one thing an append-only table exists to preserve.
    """
    model_config = STRICT

    alert_id: int
    verdict: DispositionValue | None = None
    verdict_source: Literal["analyst", "synthetic"] | None = None
    decided_at: datetime | None = None          # FIRST disposition — the clock
    dispositions: int = 0
    worked_by_analyst: bool = False
    history: list[Disposition] = Field(default_factory=list)


_HISTORY_SQL = """
SELECT outcome_id, alert_id, disposition, analyst_id, source, decided_at, notes,
       feeds_retrain
  FROM case_outcomes
 WHERE alert_id = %(alert_id)s
 ORDER BY decided_at DESC, outcome_id DESC
"""


def read_verdict(conn: psycopg.Connection, alert_id: int) -> CaseVerdict:
    """The case's current verdict. An undispositioned case is a real answer, not
    a 404 — the alert exists, nobody has judged it yet."""
    rows = fetch_all(conn, _HISTORY_SQL, {"alert_id": alert_id})
    history = [Disposition(**r) for r in rows]
    if not history:
        return CaseVerdict(alert_id=alert_id)
    # rows are newest-first, matching v_kpi_cases' latest-wins verdict.
    latest = history[0]
    return CaseVerdict(
        alert_id=alert_id,
        verdict=latest.disposition,
        verdict_source=latest.source,
        decided_at=min(d.decided_at for d in history),
        dispositions=len(history),
        worked_by_analyst=any(d.source == "analyst" for d in history),
        history=history,
    )


def write_disposition(conn: psycopg.Connection, alert_id: int,
                      request: DispositionRequest, actor: str) -> CaseVerdict:
    """Append one verdict. Returns the whole case state, not just the row.

    The caller gets back what the KPI views will now say about this case,
    including the correction history — so a console never has to re-derive
    "did that stick?" from a second request.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO case_outcomes
                (alert_id, disposition, analyst_id, notes, feeds_retrain, source)
            VALUES (%s, %s, %s, %s, %s, 'analyst')
            """,
            (alert_id, request.disposition, actor, request.notes,
             request.feeds_retrain),
        )
    return read_verdict(conn, alert_id)


def alert_exists(conn: psycopg.Connection, alert_id: int) -> bool:
    return fetch_one(
        conn, "SELECT 1 AS ok FROM alerts WHERE alert_id = %s", (alert_id,)
    ) is not None
