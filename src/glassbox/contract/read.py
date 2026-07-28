"""Read alerts out of the store and into the frozen contract shape."""
from __future__ import annotations

from typing import Any

import psycopg

from ..db import fetch_all, fetch_one
from .models import Action, AlertDetail, AlertSummary, Evidence, Signal, Subject

_DETAIL_SQL = """
SELECT a.alert_id, a.decision_id, a.subject_type, a.subject_id, a.title, a.score,
       a.band, a.status, a.created_at, a.dedup_key,
       d.occurred_at, d.decided_at, d.execution_mode, d.action_taken,
       d.action_source_rule, d.vetoed_by, d.prevent_threshold_met,
       d.evaluation_id, d.evaluation_trigger, d.trigger_type, d.trigger_id,
       d.pit_bound_at, d.replay_as_of, d.degraded_features,
       d.rule_version_set, d.feature_version_set, d.rules_fired,
       r.recommended_action_text, r.clear_text
  FROM alerts a
  JOIN decisions d ON d.decision_id = a.decision_id
  LEFT JOIN rule_definitions r ON r.rule_id = d.action_source_rule
 WHERE a.alert_id = %s
"""

_SUMMARY_SQL = """
SELECT a.alert_id, a.subject_type, a.subject_id, a.title, a.score, a.band,
       a.status, a.created_at, a.dedup_key, d.action_taken
  FROM alerts a
  JOIN decisions d ON d.decision_id = a.decision_id
 WHERE (%(status)s::text IS NULL OR a.status = %(status)s)
   AND (%(band)s::text IS NULL OR a.band = %(band)s)
   AND (%(subject_type)s::text IS NULL OR a.subject_type = %(subject_type)s)
 ORDER BY a.score DESC, a.alert_id
 LIMIT %(limit)s OFFSET %(offset)s
"""


def get_alert(conn: psycopg.Connection, alert_id: int) -> AlertDetail | None:
    row = fetch_one(conn, _DETAIL_SQL, (alert_id,))
    if row is None:
        return None

    signals = [
        Signal(
            rank=s["rank"], feature_key=s["feature_key"], contribution=s["contribution"],
            direction=s["direction"], human_text=s["human_text"],
            reason_code=s["reason_code"], source_rule_id=s["source_rule_id"],
            asserted_by_rules=list(s["asserted_by_rules"] or []),
            feature_value=s["feature_value"], value_as_of=s["value_as_of"],
            value_computed_at=s["value_computed_at"],
        )
        for s in fetch_all(
            conn,
            "SELECT rank, feature_key, contribution, direction, human_text, reason_code, "
            "source_rule_id, asserted_by_rules, feature_value, value_as_of, value_computed_at "
            "FROM alert_signals WHERE alert_id = %s ORDER BY rank",
            (alert_id,),
        )
    ]

    subjects = [
        Subject(type=s["subject_type"], id=s["subject_id"], role=s["role"])
        for s in fetch_all(
            conn,
            "SELECT subject_type, subject_id, role FROM alert_subjects "
            "WHERE alert_id = %s ORDER BY role, subject_id",
            (alert_id,),
        )
    ]

    return AlertDetail(
        alert_id=row["alert_id"],
        decision_id=row["decision_id"],
        subject=Subject(type=row["subject_type"], id=row["subject_id"]),
        subjects=subjects,
        title=row["title"],
        score=row["score"],
        band=row["band"],
        status=row["status"],
        occurred_at=row["occurred_at"],
        decided_at=row["decided_at"],
        execution_mode=row["execution_mode"],
        action=Action(
            taken=row["action_taken"],
            source_rule=row["action_source_rule"],
            vetoed_by=row["vetoed_by"],
            prevent_threshold_met=row["prevent_threshold_met"],
            recommended_text=row["recommended_action_text"],
            clear_text=row["clear_text"],
        ),
        signals=signals,
        evidence=Evidence(
            evaluation_id=row["evaluation_id"],
            evaluation_trigger=row["evaluation_trigger"],
            trigger_type=row["trigger_type"],
            trigger_id=row["trigger_id"],
            pit_bound_at=row["pit_bound_at"],
            replay_as_of=row["replay_as_of"],
            degraded_features=list(row["degraded_features"] or []),
            rule_version_set=row["rule_version_set"] or {},
            feature_version_set=row["feature_version_set"] or {},
        ),
        rules_fired=list(row["rules_fired"] or []),
    )


def list_alerts(conn: psycopg.Connection, status: str | None = None, band: str | None = None,
                subject_type: str | None = None, limit: int = 50,
                offset: int = 0) -> list[AlertSummary]:
    rows = fetch_all(conn, _SUMMARY_SQL, {
        "status": status, "band": band, "subject_type": subject_type,
        "limit": limit, "offset": offset,
    })
    return [
        AlertSummary(
            alert_id=r["alert_id"],
            subject=Subject(type=r["subject_type"], id=r["subject_id"]),
            title=r["title"], score=r["score"], band=r["band"], status=r["status"],
            action_taken=r["action_taken"], dedup_key=r["dedup_key"],
            created_at=r["created_at"],
        )
        for r in rows
    ]


def all_alert_ids(conn: psycopg.Connection) -> list[int]:
    return [r["alert_id"] for r in fetch_all(conn, "SELECT alert_id FROM alerts ORDER BY alert_id")]
