"""Evaluating without acting.

Three simulations (WEEK5-PLAN.md §2), and all three are built: re-score an
existing subject, what-if a candidate rule over history, and score a
hypothetical transaction. They differ in what the caller is allowed to
fabricate — nothing, a rule, an event — and they share one property, which is
that **nothing survives the call**.

`simulation_scope` is that property, made structural. The rule what-if and the
hypothetical charge fabricate rows to get their answer; `simulate_subject`
writes nothing at all and still goes through the scope, because a discipline
that applies only where it is currently needed is a discipline nobody can rely
on later. Session 4's path is the sharpest case for it: the scoped feature pass
INSERTs into `feature_values`, which is append-only and bitemporal, so the
difference between rolled back and committed there is the difference between a
what-if and a corrupted audit store.

Two guards, and the first one matters more than it looks:

  * **An autocommit connection is refused outright.** `conn.transaction()` on an
    autocommit connection is a no-op that silently commits, so the one line
    standing between "simulate a rule" and "publish a rule" would quietly do
    nothing. That is not a failure worth making recoverable, so it raises.
  * **force_rollback**, so the exit path is rollback whether the body raised or
    returned. Nested inside an outer transaction — which is every test, and every
    request handler that opened one — psycopg uses a SAVEPOINT, so the caller's
    transaction survives and only the simulation's work is undone.

Shadow mode (D2, session 3) is the neighbouring idea and is deliberately NOT
built on this file: a shadow rule evaluates and persists a decision while taking
no action, which is a property of the rule rather than of the call, so it lives
in `evaluate_batch` where the live and shadow rule sets are separated once. The
overlap shows up in the other direction — `simulate_rule` applies its candidate
as `active` inside the sandbox, because a what-if on a rule the gate would
silence is a what-if that reports nothing.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any, Iterator, Mapping, Sequence

import psycopg

from ..config import reference_now
from ..contract.catalog import RuleDraft
from ..db import fetch_all, fetch_one
from ..features.predicate import load_allowlist
from ..features.runner import DriverSplit, IncrementalRunner
from ..ingest import records
from ..ingest.records import RecordRefused
from ..rules.publish import apply_definition
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


# One refusal type for "a row this system will not write down", whether the
# caller meant to keep it or not. This module owned its own for one session and
# the messages were already converging; two exception classes for one meaning is
# how a route ends up catching the wrong one. The name is kept because it reads
# correctly at the fabrication site.
FabricationRefused = RecordRefused


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


SHADOW_NOTE = (
    "A candidate is evaluated as if ACTIVE, whatever status it asks for: the "
    "question a what-if answers is what the rule would DO. Publishing it lands "
    "it in shadow, where (migration 0030) it scores, records its conditions and "
    "records the action it would have taken — and takes none — until an admin "
    "promotes it."
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
    evaluated_as: str = "active"


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

    **The candidate is applied as `active`**, whatever status it carries. After
    the shadow gate (0030) a rule in shadow contributes no signal and holds no
    authority, so simulating one as authored would report — accurately and
    uselessly — that it does nothing. `evaluated_as` rides on the payload so the
    substitution is stated rather than assumed.
    """
    as_of = as_of or reference_now()
    existing = fetch_one(
        conn, "SELECT rule_id FROM rule_definitions WHERE rule_id = %s",
        (draft.rule_id,))
    mode = "replacement" if existing else "draft"

    with simulation_scope(conn):
        apply_definition(conn, draft.model_copy(update={"status": "active"}),
                         replacing=bool(existing), created_by="simulation")
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


# --------------------------------------------------------- hypothetical charge
# The relation a fabricated transaction arrives in. Named once: it is both the
# table written and the driver relation the scoped feature pass selects on, and
# those two being the same relation is the whole reason the pass is scoped.
ARRIVAL_RELATION = "transactions"

# Columns no caller may fabricate, whatever the request model happens to expose.
#
# `synthetic_label` is the one that matters: it is PLANTED GROUND TRUTH, the
# denominator of §11's false-negative tile and of every precision number the
# rule what-if publishes. A fabricated row carrying a label would be a row that
# invented its own correct answer. Refusing it here rather than only omitting it
# from the request model is the same layered enforcement §1 uses for the sum
# invariant — and it is what makes "nothing labels it" an enforced limit rather
# than a described one.
#
# Note what is NOT here: `auth_result`. A fabricated charge may say it was
# declined, because `merchant_decline_burst` counts declines and a what-if on a
# card-testing rule needs to be able to describe one. The authorization path
# forbids it for the opposite reason — there the engine chooses it.
UNFABRICABLE_COLUMNS: frozenset[str] = frozenset({"synthetic_label", "ingested_at",
                                                  "source"})

# The scoped pass recomputes at exactly the fabricated instant, so its watermark
# has to sit just below it: `since` is an exclusive lower bound, and a watermark
# equal to occurred_at would exclude the very row the pass exists to see.
_ARRIVAL_EPSILON = timedelta(microseconds=1)

FABRICATION_BASIS = (
    "A transaction that never happened, inserted inside a transaction that is "
    "rolled back, followed by a feature pass scoped to the instant it claims to "
    "have occurred at, then the engine's own pipeline. No transaction, feature "
    "value, decision, alert, signal or execution was written."
)


@dataclass
class TransactionWhatIf:
    """What the engine did with a charge nobody made.

    `row` is echoed back deliberately: a simulation of a fabricated event has to
    publish the fabrication, or a reader is left comparing a score against a
    charge they described rather than against the one that was scored.
    """
    row: dict[str, Any]
    lane: str
    as_of: datetime
    result: EvaluationResult
    features_recomputed: dict[str, int] = field(default_factory=dict)
    # Two ways of not being recomputed, kept apart: a feature driven by another
    # relation was read at a stored value that is correct, and one the compiler
    # refuses has no computed value at all.
    features_elsewhere: dict[str, str] = field(default_factory=dict)
    features_uncomputable: dict[str, str] = field(default_factory=dict)
    novelty_features: list[str] = field(default_factory=list)

    @property
    def features_not_recomputed(self) -> dict[str, str]:
        return {**self.features_elsewhere, **self.features_uncomputable}


def simulate_transaction(conn: psycopg.Connection, row: Mapping[str, Any],
                         lane: str = "inline_sync", as_of: datetime | None = None,
                         ctx: EngineContext | None = None) -> TransactionWhatIf:
    """Score a charge that never happened, and write none of it down.

    Three steps inside one scope, and the scope is the only thing standing
    between them and a corrupted store. The middle one is why: the feature pass
    INSERTs into `feature_values`, which is append-only and bitemporal, so a leak
    there is not a stray row — it is a value some future decision would read as
    the truth about an instant, with no way to tell it apart from one the runner
    computed from real data.

    **The feature pass is scoped, not full.** It runs only the features an
    arriving `transactions` row drives, and only at the fabricated instant. A
    full pass would recompute 21 features across the whole population to answer a
    question about one charge; a pass that ran nothing would read the STORED
    value for every feature keyed on a card or a customer and `absent` for every
    feature keyed on the transaction itself — which is the same as scoring a
    charge nobody made against evidence that predates it.

    **`EngineContext` is loaded BEFORE the scope**, which is the exact opposite
    of `simulate_rule` and for the same reason: the context snapshots the CONTROL
    PLANE, and this simulation fabricates data rather than rules. Nothing it
    writes could change what the context holds.
    """
    ctx = ctx or EngineContext.load(conn)
    allowlist = load_allowlist(conn)
    fabricated = prepare(conn, row, allowlist, as_of=as_of)
    occurred_at, txn_id = fabricated["occurred_at"], fabricated["txn_id"]

    with simulation_scope(conn):
        # A fabricated charge may present a fingerprint nobody has seen, and
        # that is the most interesting what-if there is: `device_first_seen_min`
        # is 21 of R-114's 87 points, so "what if this came from a brand-new
        # device" is exactly the question an analyst asks. The device is created
        # inside the scope like everything else here, and vanishes with it.
        records.ensure_device(conn, ARRIVAL_RELATION, fabricated, "authorized")
        insert_arrival(conn, fabricated, allowlist)
        recomputed, split, novelty = scoped_feature_pass(conn, occurred_at)

        plans = plan_evaluations(conn, ctx, lane, occurred_at, run_id="sim-txn",
                                 subject_ids=[txn_id])
        matching = [p for p in plans
                    if p.subject.type == "transaction" and p.subject.id == txn_id]
        if not matching:
            raise SubjectNotEvaluable(
                f"the fabricated transaction is not evaluable in the {lane!r} "
                f"lane: no rule takes a transaction subject there. Only the "
                f"transaction itself is scored — the card, account and customer "
                f"it references are not re-evaluated"
            )
        result = evaluate(conn, matching[0], ctx=ctx)

    return TransactionWhatIf(
        row=dict(fabricated), lane=lane, as_of=occurred_at, result=result,
        features_recomputed=recomputed, features_elsewhere=split.elsewhere,
        features_uncomputable=split.uncomputable, novelty_features=novelty)


def prepare(conn: psycopg.Connection, row: Mapping[str, Any],
            allowlist: dict[str, frozenset[str]],
            as_of: datetime | None = None) -> dict[str, Any]:
    """Validate a fabricated row and fill in what it did not say.

    A thin call onto `ingest.records.prepare`, and the thinness is the point:
    the row a caller TESTS here and the row a caller COMMITS through
    `/authorize` or `/ingest/transactions` pass through the same gate. A second,
    laxer gate on the simulation side would make the what-if worthless in the
    one direction that matters — it would report a verdict on a charge the live
    path would have refused outright.

    Only two things differ, and both are about what a HYPOTHETICAL row may say:
    the forbidden set (`synthetic_label`, because a fabricated charge may not
    label itself) and the id prefix, which is `SIM-` so a fabricated id reads as
    fabricated wherever it is quoted back.
    """
    return records.prepare(conn, ARRIVAL_RELATION, row, allowlist,
                           forbidden=UNFABRICABLE_COLUMNS, id_prefix="SIM",
                           as_of=as_of)


def insert_arrival(conn: psycopg.Connection, row: Mapping[str, Any],
                   allowlist: dict[str, frozenset[str]]) -> None:
    """The one INSERT, shared with the two paths that mean to keep it."""
    records.insert_row(conn, ARRIVAL_RELATION, row, allowlist)


def scoped_feature_pass(conn: psycopg.Connection, occurred_at: datetime
                         ) -> tuple[dict[str, int], DriverSplit, list[str]]:
    """Recompute what the arriving row drives, at the instant it arrived.

    The window is one microsecond wide and closes on `occurred_at`, so the work
    list is the fabricated row and anything real that shares its instant. What
    that buys is precise: `card_cnp_count` is recomputed for the card WITH the
    new charge in it, and `mcc_is_new_for_customer` — which keys on `txn_id` —
    gets a value at all, where before the pass it would read `absent` and take
    R-114's satisfaction gate down with it.
    """
    runner = IncrementalRunner(conn)
    split = runner.driven_by(ARRIVAL_RELATION)

    recomputed: dict[str, int] = {}
    for report in runner.run_population(occurred_at, occurred_at - _ARRIVAL_EPSILON,
                                        split.driven):
        if report.skipped:
            # Unreachable while `driven_by` compiles every spec first — kept so a
            # runner that learns a new way to skip cannot report it as a success.
            split.uncomputable[report.feature_key] = report.skipped
        else:
            recomputed[report.feature_key] = report.rows_written

    # A novelty feature looks at history up to as_of - baseline_lag (seed 0019),
    # so a fabricated charge cannot make its own category or country look
    # familiar. Derived from the specs rather than named in a constant: the
    # limit is true of whichever features carry the lag, not of the two that
    # carry it today.
    novelty = sorted(k for k, spec in runner.specs.items()
                     if (spec.baseline_spec or {}).get("baseline_lag"))
    return recomputed, split, novelty


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
