"""Write one evaluation's outcome.

EVERY subject that goes through decisioning gets a `decisions` row, including
silent allows — that is what makes a block rate and a false-positive-on-blocks
rate computable at all.

An ALERT is raised iff at least one rule had AUTHORITY. Not "the score landed in
an elevated band": banding on a consolidated score would surface T-021's 31 in
the review queue and contradict the entire point of that case. The decision row
still records the evaluation, so nothing is lost by not alerting.

Authority is necessary but no longer sufficient (§9). An evaluation that would
alert is first routed — folded onto an open case, restated over it, suppressed
under an investigation already running, or raised — and `decisions.alert_routing`
records which. Every one of the ~9,900 decisions therefore says what became of
it, which is the only way alert volume has a denominator.

alert_subjects for a network subject is INSERT ... SELECT from cluster_members.
No literal ids anywhere — that is §3.3's acceptance criterion, and the previous
scorer failed it with `WHERE el.from_id = 'DEV-F90D2'`.

Every decision also writes its CONDITION LEDGER (§10): one row per condition of
every applicable rule, fired or not. That is the only record of what the engine
looked at across the population — alert_signals holds the post-consolidation
survivors of the alerted subjects, which is 7 rows out of 9,923 decisions — and
without it "which conditions are mispriced" is unanswerable.
"""
from __future__ import annotations

import json
from decimal import Decimal
from typing import Any, Sequence

import psycopg

from ..db import fetch_all, fetch_one
from ..types import jsonable
from .execute import write_executions
from .exposure import exposure_for

DECISION_COLUMNS = (
    "subject_type", "subject_id", "occurred_at", "execution_mode", "score", "band",
    "action_taken", "fail_mode", "model_ref", "rules_fired", "evaluation_id",
    "evaluation_trigger", "trigger_type", "trigger_id", "rule_version_set",
    "feature_version_set", "degraded_features", "action_source_rule", "vetoed_by",
    "prevent_threshold_met", "pit_bound_at", "replay_as_of", "alert_routing",
)

CONDITION_COLUMNS = (
    "decision_id", "condition_id", "rule_id", "feature_key", "read_status", "fired",
    "rule_satisfied", "priced_points", "contributed", "entity_type", "entity_ids",
    "feature_value", "value_as_of", "value_computed_at", "spec_version",
)

# 15 columns x 800 rows = 12,000 bound parameters, against a protocol limit of
# 65,535. A 400-decision batch of transactions is 3,200 ledger rows, so this
# chunks into four statements rather than 3,200 round trips (executemany) — and
# COPY is not an option while the extension test's DDL hook watches this path.
_LEDGER_CHUNK = 800


def dedup_key(result) -> str:
    """Matches §12's "network:RING-1187:L-203".

    The open window is NOT part of the key. §9 describes the key as
    (subject_type, subject_id, rule_set, open_window), but putting a window in
    the string would make the key change as time passes, so a published key
    would stop identifying its own case — and §12 publishes it. The window is a
    predicate applied at fold time instead (_open_alert).
    """
    rules = "+".join(sorted(result.outcome.authorised_rules))
    return f"{result.request.subject.type}:{result.request.subject.id}:{rules}"


def write_batch(conn: psycopg.Connection, results: Sequence[Any]) -> dict[str, int]:
    if not results:
        return {"decisions": 0, "conditions": 0, "alerts": 0, "signals": 0}

    rows = [_decision_row(r) for r in results]
    placeholder = "(" + ", ".join(["%s"] * len(DECISION_COLUMNS)) + ")"
    sql = (f"INSERT INTO decisions ({', '.join(DECISION_COLUMNS)}) VALUES "
           + ", ".join([placeholder] * len(rows))
           + " RETURNING decision_id")
    flat = [v for row in rows for v in row]

    with conn.cursor() as cur:
        cur.execute(sql, flat)
        decision_ids = [r["decision_id"] for r in cur.fetchall()]

    for result, decision_id in zip(results, decision_ids):
        result.decision_id = decision_id

    conditions = _write_conditions(conn, results)

    # Only alerting results need a hygiene decision or a money number, and on a
    # population pass that is 7 results out of 9,844.
    alerting = [r for r in results if r.outcome.authorised_rules]
    policies = _load_policies(conn) if alerting else {}
    exposures = exposure_for(conn, alerting) if alerting else {}

    tally = {"raised": 0, "folded": 0, "restated": 0, "suppressed": 0}
    signals = 0
    routing: list[tuple[int, int | None, str]] = []
    routed: list[tuple[Any, int, str]] = []
    for result in alerting:
        kind, alert_id, written = _route_alert(conn, result, policies, exposures)
        tally[kind] += 1
        signals += written
        routing.append((result.decision_id, alert_id, kind))
        routed.append((result, alert_id, kind))

    _write_routing(conn, routing)
    # §8 runs after routing, and reads it: a folded evaluation must not send the
    # customer a second step-up for a situation they were already challenged on.
    executions = write_executions(conn, routed)
    return {"decisions": len(rows), "conditions": conditions,
            "alerts": tally["raised"], "folded": tally["folded"],
            "restated": tally["restated"], "suppressed": tally["suppressed"],
            "signals": signals, "executions": executions}


# ---------------------------------------------------------------- §9 hygiene
def _load_policies(conn: psycopg.Connection) -> dict[str, dict]:
    """Read per-call, not cached: a test that widens a window inside its
    transaction must see the change on the very next evaluation."""
    return {p["subject_type"]: p for p in fetch_all(
        conn, "SELECT subject_type, open_window, suppress_while_open FROM alert_policy")}


def _route_alert(conn: psycopg.Connection, r, policies: dict[str, dict],
                 exposures: dict) -> tuple[str, int | None, int]:
    """Fold, restate, suppress or raise — in that order, and the order matters.

    Without this, re-running a lane creates a duplicate alert every time: a ring
    re-evaluated every fifteen minutes raises the same case 96 times a day and
    alert volume becomes a count of cycles that rises when nothing changed.

    RESTATEMENT exists because a bare fold would leave an analyst looking at a
    stale score for a case that got worse. It replaces the alert's numbers AND
    its signal set together, because an alert's signals must always be exactly
    one decision's pool — anything else breaks sum(signals) == score, which is
    enforced in the contract, in v_alert_invariants and in the test suite, and
    which is the product.

    The comparison is STRICTLY greater. An equal re-score must not rewrite the
    signal rows: re-running a lane over unchanged data would then churn the very
    rows the sum invariant is checked against, on every run, forever.
    """
    policy = policies.get(r.request.subject.type)

    open_alert = _open_alert(conn, r, policy)
    if open_alert is not None:
        if r.pool.subject_score > open_alert["score"]:
            return "restated", open_alert["alert_id"], _restate_alert(
                conn, open_alert, r, exposures)
        _fold_alert(conn, open_alert, r)
        return "folded", open_alert["alert_id"], 0

    blocking = _suppressing_alert(conn, r, policy)
    if blocking is not None:
        # Recorded, never silent (§9): the decision exists, says it was
        # suppressed, and names the case that suppressed it.
        return "suppressed", blocking["alert_id"], 0

    alert_id, written = _raise_alert(conn, r, exposures)
    return "raised", alert_id, written


def _open_alert(conn: psycopg.Connection, r, policy: dict | None) -> dict | None:
    """The open case this evaluation belongs to, if there is one.

    The window is anchored on first_event_at, not slid along last_event_at, so a
    case has a bounded life: a ring active for a month is four weekly cases
    rather than one immortal one, which is what keeps median triage time a
    meaningful number.

    Measured on EVENT time. alerts.created_at is DEFAULT now(), so a window
    measured on it would fold a January replay against today.

    BETWEEN rather than a one-sided comparison because events do arrive out of
    order, and an older event belongs to the same case as a newer one.
    """
    if policy is None:
        return None
    return fetch_one(
        conn,
        """
        SELECT alert_id, score, triggering_events
          FROM alerts
         WHERE dedup_key = %s
           AND status = 'open'
           AND first_event_at IS NOT NULL
           AND %s::timestamptz BETWEEN first_event_at - %s::interval
                                   AND first_event_at + %s::interval
         ORDER BY alert_id DESC
         LIMIT 1
        FOR UPDATE
        """,
        (dedup_key(r), r.request.occurred_at,
         policy["open_window"], policy["open_window"]),
    )


def _suppressing_alert(conn: psycopg.Connection, r, policy: dict | None) -> dict | None:
    """An open, undispositioned case on this subject under a DIFFERENT rule set.

    Otherwise an analyst's own investigation generates noise that lands back in
    their own queue. Undispositioned is the test that matters: once a case has a
    case_outcomes row it is closed business and a new pattern on the subject
    deserves its own alert.
    """
    if policy is None or not policy["suppress_while_open"]:
        return None
    return fetch_one(
        conn,
        """
        SELECT a.alert_id
          FROM alerts a
          LEFT JOIN case_outcomes co ON co.alert_id = a.alert_id
         WHERE a.subject_type = %s AND a.subject_id = %s
           AND a.status = 'open'
           AND co.outcome_id IS NULL
           AND a.first_event_at IS NOT NULL
           AND %s::timestamptz BETWEEN a.first_event_at - %s::interval
                                   AND a.first_event_at + %s::interval
         ORDER BY a.alert_id DESC
         LIMIT 1
        """,
        (r.request.subject.type, r.request.subject.id, r.request.occurred_at,
         policy["open_window"], policy["open_window"]),
    )


def _fold_alert(conn: psycopg.Connection, alert: dict, r) -> None:
    """One more triggering event on an existing case. Score untouched: this
    evaluation did not score higher, so the alert already shows the worst view."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE alerts
               SET triggering_events = triggering_events + 1,
                   first_event_at = LEAST(first_event_at, %s::timestamptz),
                   last_event_at  = GREATEST(last_event_at, %s::timestamptz)
             WHERE alert_id = %s
            """,
            (r.request.occurred_at, r.request.occurred_at, alert["alert_id"]),
        )


def _restate_alert(conn: psycopg.Connection, alert: dict, r, exposures: dict) -> int:
    """Re-point an open case at a worse evaluation, numbers and evidence together.

    The UPDATE and the signal replacement are one operation on purpose. Updating
    the score while leaving the old signals would leave an alert whose bar does
    not add up to its own number, which the contract refuses to serve at all.

    alert_subjects is re-upserted and never deleted: the ring's five subjects
    come from cluster_members, and deleting them to rebuild would drop coverage
    for any member the builder has since retired.
    """
    exposure, basis = exposures.get(
        (r.request.subject.type, r.request.subject.id), (None, None))
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE alerts
               SET decision_id = %s, title = %s, score = %s, band = %s,
                   exposure_amount = %s, exposure_basis = %s,
                   triggering_events = triggering_events + 1,
                   first_event_at = LEAST(first_event_at, %s::timestamptz),
                   last_event_at  = GREATEST(last_event_at, %s::timestamptz)
             WHERE alert_id = %s
            """,
            (r.decision_id, _title(r), r.pool.subject_score, r.band,
             exposure, basis, r.request.occurred_at, r.request.occurred_at,
             alert["alert_id"]),
        )
        cur.execute("DELETE FROM alert_signals WHERE alert_id = %s", (alert["alert_id"],))
        written = _write_signals(cur, alert["alert_id"], r)
        _write_subjects(cur, alert["alert_id"], r)
    return written


# ---------------------------------------------------------------- §10 ledger
def _write_conditions(conn: psycopg.Connection, results: Sequence[Any]) -> int:
    rows = [row for r in results for row in _condition_rows(r)]
    if not rows:
        return 0

    placeholder = "(" + ", ".join(["%s"] * len(CONDITION_COLUMNS)) + ")"
    with conn.cursor() as cur:
        for start in range(0, len(rows), _LEDGER_CHUNK):
            chunk = rows[start:start + _LEDGER_CHUNK]
            cur.execute(
                f"INSERT INTO decision_conditions ({', '.join(CONDITION_COLUMNS)}) VALUES "
                + ", ".join([placeholder] * len(chunk))
                + " ON CONFLICT (decision_id, condition_id) DO NOTHING",
                [v for row in chunk for v in row],
            )
    return len(rows)


def _condition_rows(r) -> list[tuple]:
    """One row per condition VISITED, which is not the same as fired.

    `contributed` is what this decision actually took: zero unless the condition
    fired AND its rule was satisfied, because scoring.score_rule gates the whole
    rule on satisfaction. Keeping the catalog price alongside it is the point —
    the gap between priced_points and contributed is what §10 is looking for.
    """
    out = []
    for rs in r.rule_scores:
        satisfied = rs.evaluation.satisfied
        for co in rs.evaluation.evaluated:
            cond, read = co.condition, co.read
            priced = Decimal(cond.contribution_points)
            out.append((
                r.decision_id, cond.condition_id, cond.rule_id, cond.feature_key,
                read.status, co.fired, satisfied, priced,
                priced if (co.fired and satisfied) else Decimal(0),
                read.entity_type, list(read.entity_ids),
                json.dumps(jsonable(read.value), default=str),
                read.as_of, read.computed_at, read.spec_version,
            ))
    return out


def _write_routing(conn: psycopg.Connection, rows: Sequence[tuple]) -> None:
    """Correct the provisional alert_routing once the alert step has run.

    Routing is only knowable AFTER the alert decision, the decision row has to
    exist first (alerts.decision_id is NOT NULL), and PostgreSQL cannot defer a
    CHECK — so the row is inserted provisionally and corrected here, inside the
    same transaction. One UPDATE per batch, not one per decision.
    """
    if not rows:
        return
    values = ", ".join(["(%s::bigint, %s::bigint, %s::text)"] * len(rows))
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE decisions AS d SET alert_id = v.alert_id, alert_routing = v.routing "
            f"FROM (VALUES {values}) AS v(decision_id, alert_id, routing) "
            f"WHERE d.decision_id = v.decision_id",
            [v for row in rows for v in row],
        )


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
        # Provisional: corrected by _write_routing once the alert step has run.
        # 'no_authority' is already final for the ~9,916 decisions that no rule
        # had authority over, which is most of them.
        "raised" if r.outcome.authorised_rules else "no_authority",
    )


def _title(r) -> str:
    return r.primary_rule_name or f"{r.request.subject.type} alert"


def _raise_alert(conn: psycopg.Connection, r, exposures: dict) -> tuple[int, int]:
    exposure, basis = exposures.get(
        (r.request.subject.type, r.request.subject.id), (None, None))
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO alerts (decision_id, subject_type, subject_id, title, score,
                                band, status, dedup_key, triggering_events,
                                first_event_at, last_event_at,
                                exposure_amount, exposure_basis)
            VALUES (%s, %s, %s, %s, %s, %s, 'open', %s, 1, %s, %s, %s, %s)
            RETURNING alert_id
            """,
            (r.decision_id, r.request.subject.type, r.request.subject.id, _title(r),
             r.pool.subject_score, r.band, dedup_key(r),
             r.request.occurred_at, r.request.occurred_at, exposure, basis),
        )
        alert_id = cur.fetchone()["alert_id"]
        written = _write_signals(cur, alert_id, r)
        _write_subjects(cur, alert_id, r)
    return alert_id, written


def _write_signals(cur: psycopg.Cursor, alert_id: int, r) -> int:
    """The score bar. Always the whole pool of ONE decision, never a merge of
    two — that is what keeps sum(signals) == score true by construction."""
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
    return len(payload)


def _write_subjects(cur: psycopg.Cursor, alert_id: int, r) -> None:
    """Upsert-only, never delete. On a restatement the existing coverage must
    survive: a member the cluster builder has since retired was still part of the
    case an analyst has been reading."""
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


def _ranked_vetoes(r) -> list:
    base = len(r.pool.signals)
    for i, s in enumerate(r.outcome.veto_signals, start=1):
        s.rank = base + i
    return list(r.outcome.veto_signals)
