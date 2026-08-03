"""Evaluating without acting.

Three simulations are planned (WEEK5-PLAN.md §2): re-score an existing subject,
what-if a candidate rule over history, and score a hypothetical transaction. They
differ in what the caller is allowed to fabricate — nothing, a rule, an event —
and they share one property, which is that **nothing survives the call**.

`simulation_scope` is that property, made structural. Sessions 2 and 4 fabricate
rows to get their answer; this session's simulation writes nothing at all and
still goes through the scope, because a discipline that applies only where it is
currently needed is a discipline nobody can rely on later.

Two guards, and the first one matters more than it looks:

  * **An autocommit connection is refused outright.** `conn.transaction()` on an
    autocommit connection is a no-op that silently commits, so the one line
    standing between "simulate a rule" and "publish a rule" would quietly do
    nothing. That is not a failure worth making recoverable, so it raises.
  * **force_rollback**, so the exit path is rollback whether the body raised or
    returned. Nested inside an outer transaction — which is every test, and every
    request handler that opened one — psycopg uses a SAVEPOINT, so the caller's
    transaction survives and only the simulation's work is undone.

Simulation is also the machinery shadow mode wants (D2): a shadow rule is one
that evaluates and persists a decision but takes no action, and a simulation is
one that evaluates and persists nothing. Session 3 builds the first out of the
second rather than writing a second evaluator.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Iterator, Sequence

import psycopg

from ..config import reference_now
from ..contract.catalog import RuleDraft
from ..db import fetch_all, fetch_one
from ..types import EvaluationRequest
from .evaluation import (
    EngineContext,
    EvaluationResult,
    evaluate,
    evaluate_population,
    plan_evaluations,
)

# WEEK5-PLAN O1, answered: 2,000 subjects by default, overridable per request,
# and the cap is PUBLISHED as the denominator of every rate derived from it. An
# HTTP request should not evaluate 9,844 subjects unbounded, and a sampled rate
# that does not say it was sampled is the same unearned claim §11 refuses on a
# KPI tile.
DEFAULT_SAMPLE_CAP = 2000


class SimulationUnsafe(RuntimeError):
    """The scope cannot guarantee rollback, so the simulation does not run."""


class SubjectNotEvaluable(LookupError):
    """No evaluation exists for this (subject, lane) — not an error in the
    engine, an answer about the request."""


@contextmanager
def simulation_scope(conn: psycopg.Connection) -> Iterator[psycopg.Connection]:
    if conn.autocommit:
        raise SimulationUnsafe(
            "simulation refuses an autocommit connection: conn.transaction() "
            "cannot roll back what autocommit has already committed, so the "
            "guarantee this scope exists to provide would be silently absent."
        )
    with conn.transaction(force_rollback=True):
        yield conn


def simulate_subject(conn: psycopg.Connection, subject_type: str, subject_id: str,
                     lane: str, as_of: datetime | None = None,
                     replay_as_of: datetime | None = None,
                     ctx: EngineContext | None = None) -> EvaluationResult:
    """Re-derive one subject's decision. Existing rows, existing rules, existing
    feature values; nothing fabricated and nothing written.

    The request is rebuilt through `plan_evaluations`, not hand-assembled, so a
    simulation sees the same trigger row, the same `occurred_at` and the same
    evaluation trigger the cycle would have produced. A hand-built request would
    be a second planner, and S-077 — whose subject is an account and whose IP
    condition resolves against the triggering transfer — is exactly the case
    where the two would diverge.

    `replay_as_of` is the `computed_at` ceiling: it bounds how far into our own
    knowledge the evaluation may see, which is what makes "what did this look
    like on 14 January?" answerable rather than a re-score with today's values.
    """
    ctx = ctx or EngineContext.load(conn)
    as_of = as_of or reference_now()

    plans = plan_evaluations(conn, ctx, lane, as_of, run_id="sim",
                             subject_ids=[subject_id])
    matching = [p for p in plans
                if p.subject.type == subject_type and p.subject.id == subject_id]
    if not matching:
        raise SubjectNotEvaluable(
            f"no {lane} evaluation is planned for {subject_type}:{subject_id} "
            f"at as_of={as_of.isoformat()} — either no rule targets this subject "
            f"type in this lane, or the subject has no triggering row at or "
            f"before that instant"
        )

    request = matching[0]
    if replay_as_of is not None:
        request = replace(request, replay_as_of=replay_as_of)

    with simulation_scope(conn):
        return evaluate(conn, request, ctx=ctx)


# ------------------------------------------------------------------ rule what-if
SAMPLING_BASIS = (
    "the most recent N subjects of this type in this lane, by the trigger row's "
    "occurred_at, where N is sample_cap. Deliberately the most recent rather "
    "than a random sample: a rule is authored against what is happening now, and "
    "a deterministic slice is one a second run reproduces. Every rate below has "
    "subjects_evaluated as its denominator, not the population."
)


@dataclass
class RuleWhatIf:
    """Everything the published what-if is derived from, and nothing published.

    Kept as a dataclass on the engine side for the same reason `EvaluationResult`
    is: `contract/simulation.py` decides what a caller sees, and the engine
    decides what happened.
    """
    draft: RuleDraft
    mode: str                       # draft | replacement
    lane: str
    subject_type: str
    as_of: datetime
    subjects_available: int
    sample_cap: int
    results: list[EvaluationResult]
    labels: dict[str, str | None]           # trigger id -> synthetic_label
    stored: dict[str, dict]                 # subject_id -> its stored decision


def simulate_rule(conn: psycopg.Connection, draft: RuleDraft,
                  as_of: datetime | None = None,
                  sample_cap: int = DEFAULT_SAMPLE_CAP,
                  subject_ids: Sequence[str] | None = None) -> RuleWhatIf:
    """Evaluate a candidate rule over history, and write nothing.

    The draft is applied to the control plane *inside* `simulation_scope` and the
    engine then runs unmodified — there is no "simulation mode" in the evaluator,
    because a second evaluation path would be a second answer. What the admin
    tests is the engine, and the only difference from publishing is that the
    transaction rolls back.

    **`EngineContext` is loaded AFTER the draft is applied.** It snapshots rules
    at load time, so a context built first would not contain the candidate and
    the what-if would confidently report that the new rule does nothing.

    **Replacement mode deletes the existing rule's conditions**, which cascades
    to their `decision_conditions` ledger rows. That is a large delete inside a
    transaction that is rolled back, so nothing is lost — but it is worth knowing
    it happens, because it means a what-if on an existing rule is not a read-only
    operation at the storage layer even though it is one from outside.
    """
    as_of = as_of or reference_now()
    existing = fetch_one(
        conn, "SELECT rule_id FROM rule_definitions WHERE rule_id = %s",
        (draft.rule_id,))
    mode = "replacement" if existing else "draft"

    with simulation_scope(conn):
        _apply_draft(conn, draft, replacing=bool(existing))
        ctx = EngineContext.load(conn)          # AFTER. See the docstring.

        plans = [p for p in plan_evaluations(conn, ctx, draft.execution_mode,
                                             as_of, run_id="whatif",
                                             subject_ids=subject_ids)
                 if p.subject.type == draft.subject_type]
        available = len(plans)
        sample = _most_recent(plans, sample_cap)

        results = [r for batch in evaluate_population(conn, ctx, sample)
                   for r in batch]
        labels = _labels(conn, sample)
        stored = _stored_decisions(conn, draft.subject_type,
                                   draft.execution_mode,
                                   [p.subject.id for p in sample])

    return RuleWhatIf(draft=draft, mode=mode, lane=draft.execution_mode,
                      subject_type=draft.subject_type, as_of=as_of,
                      subjects_available=available, sample_cap=sample_cap,
                      results=results, labels=labels, stored=stored)


def _most_recent(plans: list[EvaluationRequest], cap: int) -> list[EvaluationRequest]:
    """The cap, applied by trigger time rather than by planner order.

    The transaction planner already orders by `occurred_at`, but the dimension
    planners order by id — so taking a prefix or a suffix of the planner's own
    order would mean something different for every subject type, and on a
    merchant rule it would silently exclude the planted burst.
    """
    if cap <= 0 or len(plans) <= cap:
        return plans
    newest = sorted(plans, key=lambda p: (p.occurred_at, p.subject.id),
                    reverse=True)[:cap]
    return sorted(newest, key=lambda p: (p.occurred_at, p.subject.id))


def _apply_draft(conn: psycopg.Connection, draft: RuleDraft, replacing: bool) -> None:
    lag = timedelta(seconds=draft.evaluation_lag_seconds or 0)
    fields = (draft.name, draft.description, draft.subject_type,
              draft.execution_mode, draft.action, draft.review_threshold,
              draft.prevent_threshold, draft.is_veto, draft.combine.upper(),
              draft.status, lag, draft.recommended_action_text, draft.clear_text)

    with conn.cursor() as cur:
        if replacing:
            cur.execute(
                """
                UPDATE rule_definitions
                   SET name = %s, description = %s, subject_type = %s,
                       execution_mode = %s, action = %s, review_threshold = %s,
                       prevent_threshold = %s, is_veto = %s, combine = %s,
                       status = %s, evaluation_lag = %s,
                       recommended_action_text = %s, clear_text = %s
                 WHERE rule_id = %s
                """,
                (*fields, draft.rule_id),
            )
            # Cascades to decision_conditions. Rolled back with everything else.
            cur.execute("DELETE FROM rule_conditions WHERE rule_id = %s",
                        (draft.rule_id,))
        else:
            cur.execute(
                """
                INSERT INTO rule_definitions
                    (rule_id, name, description, subject_type, execution_mode,
                     action, review_threshold, prevent_threshold, is_veto,
                     combine, status, evaluation_lag, recommended_action_text,
                     clear_text, created_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        'simulation')
                """,
                (draft.rule_id, *fields),
            )

        for cond in draft.conditions:
            cur.execute(
                """
                INSERT INTO rule_conditions
                    (rule_id, condition_group, feature_key, operator,
                     threshold_num, threshold_text, contribution_points,
                     reason_code, signal_template, is_required)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (draft.rule_id, cond.condition_group, cond.feature_key,
                 cond.operator, cond.threshold_num, cond.threshold_text,
                 cond.contribution_points, cond.reason_code,
                 cond.signal_template, cond.is_required),
            )


def _labels(conn: psycopg.Connection,
            requests: Sequence[EvaluationRequest]) -> dict[str, str | None]:
    """Planted ground truth for the sampled subjects, keyed by TRIGGER id.

    Every planner triggers off a transaction, so a merchant or account rule gets
    a label too — the label of the row that caused the evaluation. Exact on this
    dataset and meaningless outside it, which is what the published caveat says.
    """
    ids = sorted({r.trigger.id for r in requests if r.trigger})
    if not ids:
        return {}
    return {
        row["txn_id"]: row["synthetic_label"]
        for row in fetch_all(
            conn,
            "SELECT txn_id, synthetic_label FROM transactions "
            " WHERE txn_id = ANY(%s)",
            (ids,))
    }


def _stored_decisions(conn: psycopg.Connection, subject_type: str, lane: str,
                      subject_ids: Sequence[str]) -> dict[str, dict]:
    """The latest stored decision per sampled subject, for the diff.

    Read inside the scope, which is harmless: the scope rolls back WRITES, and
    the draft cannot change rows that were written before it existed.
    """
    if not subject_ids:
        return {}
    rows = fetch_all(
        conn,
        """
        SELECT DISTINCT ON (subject_id)
               subject_id, score, band, action_taken, action_source_rule
          FROM decisions
         WHERE subject_type = %s AND execution_mode = %s
           AND subject_id = ANY(%s)
         ORDER BY subject_id, decision_id DESC
        """,
        (subject_type, lane, list(subject_ids)),
    )
    return {r["subject_id"]: r for r in rows}
