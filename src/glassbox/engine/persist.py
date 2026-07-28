"""Write one evaluation's outcome.

EVERY subject that goes through decisioning gets a `decisions` row, including
silent allows — that is what makes a block rate and a false-positive-on-blocks
rate computable at all.

An ALERT is raised iff at least one rule had AUTHORITY. Not "the score landed in
an elevated band": banding on a consolidated score would surface T-021's 31 in
the review queue and contradict the entire point of that case. The decision row
still records the evaluation, so nothing is lost by not alerting.

alert_subjects for a network subject is INSERT ... SELECT from cluster_members.
No literal ids anywhere — that is §3.3's acceptance criterion, and the previous
scorer failed it with `WHERE el.from_id = 'DEV-F90D2'`.
"""
from __future__ import annotations

import json
from typing import Any, Sequence

import psycopg

DECISION_COLUMNS = (
    "subject_type", "subject_id", "occurred_at", "execution_mode", "score", "band",
    "action_taken", "fail_mode", "model_ref", "rules_fired", "evaluation_id",
    "evaluation_trigger", "trigger_type", "trigger_id", "rule_version_set",
    "feature_version_set", "degraded_features", "action_source_rule", "vetoed_by",
    "prevent_threshold_met", "pit_bound_at", "replay_as_of",
)


def dedup_key(result) -> str:
    """Matches §12's "network:RING-1187:L-203". Week 2 computes and stores it;
    the folding behaviour it enables is Week 3 (§9)."""
    rules = "+".join(sorted(result.outcome.authorised_rules))
    return f"{result.request.subject.type}:{result.request.subject.id}:{rules}"


def write_batch(conn: psycopg.Connection, results: Sequence[Any]) -> dict[str, int]:
    if not results:
        return {"decisions": 0, "alerts": 0, "signals": 0}

    rows = [_decision_row(r) for r in results]
    placeholder = "(" + ", ".join(["%s"] * len(DECISION_COLUMNS)) + ")"
    sql = (f"INSERT INTO decisions ({', '.join(DECISION_COLUMNS)}) VALUES "
           + ", ".join([placeholder] * len(rows))
           + " RETURNING decision_id")
    flat = [v for row in rows for v in row]

    with conn.cursor() as cur:
        cur.execute(sql, flat)
        decision_ids = [r["decision_id"] for r in cur.fetchall()]

    alerts = 0
    signals = 0
    for result, decision_id in zip(results, decision_ids):
        result.decision_id = decision_id
        if not result.outcome.authorised_rules:
            continue
        alerts += 1
        signals += _write_alert(conn, result, decision_id)
    return {"decisions": len(rows), "alerts": alerts, "signals": signals}


def _decision_row(r) -> tuple:
    req = r.request
    return (
        req.subject.type, req.subject.id, req.occurred_at, req.lane,
        r.pool.subject_score, r.band, r.outcome.action,
        "open" if req.lane == "inline_sync" else None,
        "rule-engine:" + "+".join(sorted(rs.rule_id for rs in r.rule_scores)),
        # Rules that actually put a signal on the bar — not merely rules that
        # were satisfied. A mitigation rule is satisfied on almost every
        # card-present charge; listing it as "fired" on a score of 0 tells a
        # reviewer nothing and reads like a bug.
        sorted({rid for s in r.pool.signals for rid in s.asserted_by_rules}),
        req.evaluation_id, req.evaluation_trigger,
        req.trigger.type if req.trigger else None,
        req.trigger.id if req.trigger else None,
        json.dumps(r.rule_version_set), json.dumps(r.feature_version_set),
        r.degraded_features or None,
        r.outcome.action_source_rule, r.outcome.vetoed_by,
        r.outcome.prevent_threshold_met, r.pit_bound_at, req.replay_as_of,
    )


def _write_alert(conn: psycopg.Connection, r, decision_id: int) -> int:
    rule_name = r.primary_rule_name or f"{r.request.subject.type} alert"
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO alerts (decision_id, subject_type, subject_id, title, score,
                                band, status, dedup_key, triggering_events)
            VALUES (%s, %s, %s, %s, %s, %s, 'open', %s, 1)
            RETURNING alert_id
            """,
            (decision_id, r.request.subject.type, r.request.subject.id, rule_name,
             r.pool.subject_score, r.band, dedup_key(r)),
        )
        alert_id = cur.fetchone()["alert_id"]

        payload = [
            (alert_id, s.feature_key, s.contribution, s.direction, s.human_text,
             s.reason_code, s.source_rule_id, s.rank, s.asserted_by_rules,
             json.dumps(s.feature_value), s.value_as_of, s.value_computed_at)
            for s in (list(r.pool.signals) + _ranked_vetoes(r))
        ]
        if payload:
            cur.executemany(
                """
                INSERT INTO alert_signals
                    (alert_id, feature_key, contribution, direction, human_text,
                     reason_code, source_rule_id, rank, asserted_by_rules,
                     feature_value, value_as_of, value_computed_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                payload,
            )

        if r.request.subject.type == "network":
            # Derived, never literal: the alert covers exactly the accounts the
            # cluster builder found, and deleting a member changes the coverage.
            cur.execute(
                """
                INSERT INTO alert_subjects (alert_id, subject_type, subject_id, role)
                SELECT %s, cm.subject_type, cm.subject_id, cm.role
                  FROM cluster_members cm
                 WHERE cm.cluster_id = %s
                ON CONFLICT DO NOTHING
                """,
                (alert_id, r.request.subject.id),
            )
        else:
            cur.execute(
                """
                INSERT INTO alert_subjects (alert_id, subject_type, subject_id, role)
                VALUES (%s, %s, %s, 'primary') ON CONFLICT DO NOTHING
                """,
                (alert_id, r.request.subject.type, r.request.subject.id),
            )
    return len(payload)


def _ranked_vetoes(r) -> list:
    base = len(r.pool.signals)
    for i, s in enumerate(r.outcome.veto_signals, start=1):
        s.rank = base + i
    return list(r.outcome.veto_signals)
