"""executions.v1 — what was actually done, and how it turned out (§8).

A sibling of alert.v1. Nothing in models.py is touched.

`synthetic` is on the wire and not optional. On this dataset every outcome was
settled by scripts/resolve_actions.py against transactions.synthetic_label, and a
client that renders a challenge pass rate without saying so would be making
exactly the kind of unearned claim §11 objects to in the console copy. The flag
travels with the data so that is not a matter of remembering.

`latency_seconds` is issued_at -> resolved_at. It is here because it is the one
thing a decision cannot tell you and an execution can: a passed step-up comes back
in seconds, an abandoned one only when the window expires, and those are different
events for the customer even though both are 'not fraud confirmed'.
"""
from __future__ import annotations

from datetime import datetime

import psycopg
from pydantic import BaseModel, ConfigDict

from ..db import fetch_all

STRICT = ConfigDict(extra="forbid", frozen=True)


class ExecutionRecord(BaseModel):
    model_config = STRICT

    execution_id: int
    decision_id: int
    alert_id: int | None = None
    action: str
    channel: str | None = None
    subject_type: str | None = None
    subject_id: str | None = None
    issued_at: datetime
    resolved_at: datetime | None = None
    outcome: str | None = None
    outcome_source: str | None = None
    latency_seconds: float | None = None
    # TRUE when the outcome was synthesised rather than observed. Fixture
    # artifact; must not exist in a production schema, and a surface that
    # reports a rate computed from these rows has to say so.
    synthetic: bool


_SQL = """
SELECT execution_id, decision_id, alert_id, action, channel, subject_type,
       subject_id, issued_at, resolved_at, outcome, outcome_source, synthetic,
       CASE WHEN resolved_at IS NULL THEN NULL
            ELSE EXTRACT(EPOCH FROM (resolved_at - issued_at)) END AS latency_seconds
  FROM action_executions
 WHERE (%(alert_id)s::bigint IS NULL OR alert_id = %(alert_id)s)
   AND (%(decision_id)s::bigint IS NULL OR decision_id = %(decision_id)s)
 ORDER BY execution_id
"""


def read_executions(conn: psycopg.Connection, alert_id: int | None = None,
                    decision_id: int | None = None) -> list[ExecutionRecord]:
    rows = fetch_all(conn, _SQL, {"alert_id": alert_id, "decision_id": decision_id})
    return [
        ExecutionRecord(
            execution_id=r["execution_id"], decision_id=r["decision_id"],
            alert_id=r["alert_id"], action=r["action"], channel=r["channel"],
            subject_type=r["subject_type"], subject_id=r["subject_id"],
            issued_at=r["issued_at"], resolved_at=r["resolved_at"],
            outcome=r["outcome"], outcome_source=r["outcome_source"],
            latency_seconds=(float(r["latency_seconds"])
                             if r["latency_seconds"] is not None else None),
            synthetic=r["synthetic"],
        )
        for r in rows
    ]
