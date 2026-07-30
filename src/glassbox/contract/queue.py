"""queue.v1 — the review queue, ordered by priority rather than by score (§9).

A SIBLING of alert.v1, not a successor. alert.v1 is byte-frozen and keeps being
served exactly as it is; nothing in models.py is touched, because `Subject`,
`Signal`, `Action` and `Evidence` all live inside alert.v1's `$defs` closure and
adding a field to any of them would change the published file's bytes.

This is where the fields §9 and §8 produced are published: `triggering_events`
(which §12's example shows but alert.v1 has no field for), the exposure behind
the ordering, and the three factors that make up the priority.

**Why the factors are published separately.** A single opaque `priority` number
deciding which customer an analyst looks at first is precisely the kind of thing
§1 refuses to allow for a score. The same argument applies with more force here,
because the queue order determines what gets human attention at all. So the
client receives score_factor, exposure_factor and recency_factor, their product,
and a `priority_basis` string naming the formula. An analyst can see why a 72 is
above an 88.

    score_factor    = score
    exposure_factor = 1 + exposure_weight * log10(1 + max(exposure, floor))
    recency_factor  = 0.5 ^ (age_hours / priority_half_life)
    priority        = score_factor * exposure_factor * recency_factor

log10 because amount_base spans orders of magnitude: undamped, exposure alone
decides the order and the score stops mattering. The floor is why a missing
exposure damps a priority instead of zeroing it — an unpriced alert must still be
reachable in a queue.

Age is measured from `last_event_at` — EVENT time, not `created_at`, which is
wall clock and would make a historical replay rank everything as brand new. And
`as_of` is a parameter rather than now() so the ordering is reproducible, which is
what lets a test assert it at all.
"""
from __future__ import annotations

import math
from datetime import datetime
from decimal import Decimal

import psycopg
from pydantic import BaseModel, ConfigDict

from ..config import reference_now
from ..db import fetch_all

STRICT = ConfigDict(extra="forbid", frozen=True)

BASIS = ("score x (1 + w*log10(1+max(exposure,floor))) x 0.5^(age_hours/half_life); "
         "weights from alert_policy, age measured on last_event_at")


class QueueEntry(BaseModel):
    model_config = STRICT

    alert_id: int
    subject_type: str
    subject_id: str
    title: str
    score: Decimal
    band: str
    status: str
    action_taken: str
    dedup_key: str | None = None

    # §9: one case, many triggering events.
    triggering_events: int
    first_event_at: datetime | None = None
    last_event_at: datetime | None = None

    # §9: the money at risk, and how it was worked out. Never a bare number.
    exposure_amount: Decimal | None = None
    exposure_basis: str | None = None

    # The ordering, decomposed.
    age_hours: float
    score_factor: float
    exposure_factor: float
    recency_factor: float
    priority: float
    priority_basis: str = BASIS

    # §8: what was actually done to the customer, and whether it is settled.
    executions: int = 0
    unresolved_executions: int = 0
    synthetic_outcomes: bool = False


_QUEUE_SQL = """
SELECT a.alert_id, a.subject_type, a.subject_id, a.title, a.score, a.band,
       a.status, a.dedup_key, a.triggering_events, a.first_event_at,
       a.last_event_at, a.exposure_amount, a.exposure_basis,
       d.action_taken,
       COALESCE(p.exposure_weight, 0)    AS exposure_weight,
       COALESCE(p.exposure_floor, 1)     AS exposure_floor,
       EXTRACT(EPOCH FROM COALESCE(p.priority_half_life, INTERVAL '24 hours'))
           / 3600.0                      AS half_life_hours,
       GREATEST(EXTRACT(EPOCH FROM (%(as_of)s::timestamptz
                                    - COALESCE(a.last_event_at, a.created_at)))
                / 3600.0, 0)             AS age_hours,
       COALESCE(x.n, 0)                  AS executions,
       COALESCE(x.unresolved, 0)         AS unresolved_executions,
       COALESCE(x.synthetic, FALSE)      AS synthetic_outcomes
  FROM alerts a
  JOIN decisions d ON d.decision_id = a.decision_id
  LEFT JOIN alert_policy p ON p.subject_type = a.subject_type
  LEFT JOIN (
       SELECT alert_id,
              COUNT(*)                                        AS n,
              COUNT(*) FILTER (WHERE resolved_at IS NULL)     AS unresolved,
              BOOL_OR(synthetic)                              AS synthetic
         FROM action_executions
        WHERE alert_id IS NOT NULL
        GROUP BY alert_id
  ) x ON x.alert_id = a.alert_id
 WHERE (%(status)s::text IS NULL OR a.status = %(status)s)
   AND (%(subject_type)s::text IS NULL OR a.subject_type = %(subject_type)s)
"""


def read_queue(conn: psycopg.Connection, status: str | None = "open",
               subject_type: str | None = None, as_of: datetime | None = None,
               limit: int = 50, offset: int = 0) -> list[QueueEntry]:
    as_of = as_of or reference_now()
    rows = fetch_all(conn, _QUEUE_SQL, {
        "as_of": as_of, "status": status, "subject_type": subject_type})

    entries = [_entry(r) for r in rows]
    # Sorted in Python, not SQL, because the formula is published here and
    # computing it twice — once to serve, once to order — is how the two drift.
    # alert_id breaks ties so the order is total and reproducible.
    entries.sort(key=lambda e: (-e.priority, e.alert_id))
    return entries[offset:offset + limit]


def _entry(row: dict) -> QueueEntry:
    exposure = row["exposure_amount"]
    floor = float(row["exposure_floor"])
    damped = max(float(exposure) if exposure is not None else 0.0, floor)
    exposure_factor = 1.0 + float(row["exposure_weight"]) * math.log10(1.0 + damped)

    age_hours = float(row["age_hours"])
    half_life = float(row["half_life_hours"]) or 1.0
    recency_factor = 0.5 ** (age_hours / half_life)

    score_factor = float(row["score"])

    # Round the factors FIRST, then multiply the rounded values. The client is
    # given three numbers and a formula; if the priority were computed from
    # full-precision factors, multiplying the published three would not reproduce
    # the published product and the explanation would be approximately true. §1's
    # standard for a score bar is that it adds up exactly, and a queue order that
    # decides who gets looked at first is not owed less.
    exposure_factor = round(exposure_factor, 3)
    recency_factor = round(recency_factor, 6)
    score_factor = round(score_factor, 3)
    return QueueEntry(
        alert_id=row["alert_id"], subject_type=row["subject_type"],
        subject_id=row["subject_id"], title=row["title"], score=row["score"],
        band=row["band"], status=row["status"], action_taken=row["action_taken"],
        dedup_key=row["dedup_key"], triggering_events=row["triggering_events"],
        first_event_at=row["first_event_at"], last_event_at=row["last_event_at"],
        exposure_amount=exposure, exposure_basis=row["exposure_basis"],
        age_hours=round(age_hours, 3),
        score_factor=score_factor,
        exposure_factor=exposure_factor,
        recency_factor=recency_factor,
        priority=round(score_factor * exposure_factor * recency_factor, 3),
        executions=row["executions"],
        unresolved_executions=row["unresolved_executions"],
        synthetic_outcomes=bool(row["synthetic_outcomes"]),
    )
