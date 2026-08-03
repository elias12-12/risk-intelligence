"""The pipeline. This module owns the ORDER, and the order is the design.

    0    graph build            clusters + cluster_members; network subjects
    0.5  plan                   EvaluationRequests: subject, lane, trigger
    1    features (out of band) run_features.py, not here
    2    resolve                which entities each feature keys on
    3    point-in-time read     what was knowable at the bound
    4    conditions             fire / degrade, per §5
    5    per-rule score         BEFORE dedup, per §6
    6    consolidate            one signal per (feature, direction)
    7    band                   from score_bands, per subject type
    8    precedence             veto, authority, severity, prevention, cap
    9    persist                one decision; 0-or-1 alerts; signals; subjects

Stages 2 and 3 run per BATCH rather than per subject, so a cycle over ten
thousand transactions is a few dozen queries.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Iterable, Sequence

import psycopg

from .. import catalog as catalog_mod
from ..db import fetch_all
from ..features.predicate import load_allowlist
from ..types import EvaluationRequest, FeatureSpec, Rule, RuleScore, Signal, SignalPool, Subject
from . import bands as bands_mod
from . import conditions as conditions_mod
from . import consolidate as consolidate_mod
from . import persist as persist_mod
from . import precedence as precedence_mod
from . import scoring as scoring_mod
from .pit import PitRequest, bound_for, read_many
from .resolver import Graph, load_graph, resolve_many

BATCH = 400


@dataclass
class EngineContext:
    specs: dict[str, FeatureSpec]
    rules: dict[str, Rule]
    graph: Graph
    severity: dict[str, tuple[int, bool]]
    bands: dict[str, list[tuple[str, Decimal]]]

    @classmethod
    def load(cls, conn: psycopg.Connection) -> "EngineContext":
        allowlist = load_allowlist(conn)
        return cls(
            specs=catalog_mod.load_feature_specs(conn),
            rules={r.rule_id: r for r in catalog_mod.load_rules(conn)},
            graph=load_graph(conn, allowlist),
            severity=catalog_mod.load_action_severity(conn),
            bands=catalog_mod.load_score_bands(conn),
        )

    def rules_for(self, subject_type: str, lane: str) -> list[Rule]:
        return [r for r in self.rules.values()
                if r.subject_type == subject_type and r.execution_mode == lane]


@dataclass
class EvaluationResult:
    request: EvaluationRequest
    rule_scores: list[RuleScore]
    pool: SignalPool
    band: str
    outcome: Any
    degraded_features: list[str]
    rule_version_set: dict
    feature_version_set: dict
    pit_bound_at: datetime
    primary_rule_name: str | None = None
    decision_id: int | None = None


# ---------------------------------------------------------------- 0.5 plan
def plan_evaluations(conn: psycopg.Connection, ctx: EngineContext, lane: str,
                     as_of: datetime, run_id: str,
                     subject_ids: Sequence[str] | None = None) -> list[EvaluationRequest]:
    """One request per (subject, lane), each carrying its triggering row."""
    subject_types = sorted({r.subject_type for r in ctx.rules.values()
                            if r.execution_mode == lane})
    out: list[EvaluationRequest] = []
    seq = 0
    for subject_type in subject_types:
        for row in _subjects(conn, subject_type, as_of, subject_ids):
            seq += 1
            out.append(EvaluationRequest(
                evaluation_id=f"ev_{run_id}_{seq:07d}",
                subject=Subject(subject_type, row["subject_id"]),
                lane=lane,
                occurred_at=row["occurred_at"],
                trigger=(Subject(row["trigger_type"], row["trigger_id"])
                         if row.get("trigger_id") else None),
                evaluation_trigger=row["evaluation_trigger"],
            ))
    return out


def _dimension_subject(relation: str, pk: str, txn_column: str) -> str:
    """A dimension row is its own subject; its latest transaction is the trigger."""
    return f"""
        SELECT e."{pk}" AS subject_id, t.occurred_at,
               'transaction'::text AS trigger_type, t.txn_id AS trigger_id,
               '{relation}_activity'::text AS evaluation_trigger
          FROM {relation} e
          JOIN LATERAL (
               SELECT txn_id, occurred_at FROM transactions
                WHERE "{txn_column}" = e."{pk}" AND occurred_at <= %(as_of)s
                ORDER BY occurred_at DESC LIMIT 1
          ) t ON TRUE
         WHERE (%(ids)s::text[] IS NULL OR e."{pk}" = ANY(%(ids)s))
         ORDER BY e."{pk}"
    """


_SUBJECT_SQL: dict[str, str] = {
    # Every transaction is its own subject and its own trigger.
    "transaction": """
        SELECT txn_id AS subject_id, occurred_at,
               'transaction'::text AS trigger_type, txn_id AS trigger_id,
               'transaction_authorized'::text AS evaluation_trigger
          FROM transactions
         WHERE occurred_at <= %(as_of)s
           AND (%(ids)s::text[] IS NULL OR txn_id = ANY(%(ids)s))
         ORDER BY occurred_at, txn_id
    """,
    # An account is evaluated off its most recent MOVEMENT. That row is what
    # S-077's ip_is_datacenter resolves against — the account has no IP, the
    # transfer does.
    "account": """
        SELECT a.account_id AS subject_id, t.occurred_at,
               'transaction'::text AS trigger_type, t.txn_id AS trigger_id,
               'account_movement'::text AS evaluation_trigger
          FROM accounts a
          JOIN LATERAL (
               SELECT txn_id, occurred_at FROM transactions
                WHERE account_id = a.account_id AND occurred_at <= %(as_of)s
                ORDER BY (direction = 'outbound') DESC, occurred_at DESC
                LIMIT 1
          ) t ON TRUE
         WHERE (%(ids)s::text[] IS NULL OR a.account_id = ANY(%(ids)s))
         ORDER BY a.account_id
    """,
    # The remaining dimension subject types, each triggered by its most recent
    # transaction. They exist so that adding a rule on a merchant, card,
    # customer or device is genuinely an INSERT — §14's claim is only true if
    # the planner already knows how to reach every subject type the schema
    # defines. A subject type BEYOND ref_subject_type's seven is still a code
    # change, and the README says so.
    "merchant": _dimension_subject("merchants", "merchant_id", "merchant_id"),
    "card": _dimension_subject("cards", "card_id", "card_id"),
    "customer": _dimension_subject("customers", "customer_id", "customer_id"),
    "device": _dimension_subject("devices", "device_id", "device_id"),

    # A network is evaluated off the most recent INBOUND into any member. At
    # that instant the funds have not moved on yet — which is exactly what
    # evaluation_lag exists to see past.
    "network": """
        SELECT c.cluster_id AS subject_id, t.occurred_at,
               'transaction'::text AS trigger_type, t.txn_id AS trigger_id,
               'cluster_inbound'::text AS evaluation_trigger
          FROM clusters c
          JOIN LATERAL (
               SELECT tx.txn_id, tx.occurred_at
                 FROM cluster_members cm
                 JOIN transactions tx ON tx.account_id = cm.subject_id
                WHERE cm.cluster_id = c.cluster_id AND cm.subject_type = 'account'
                  AND tx.direction = 'inbound' AND tx.occurred_at <= %(as_of)s
                ORDER BY tx.occurred_at DESC LIMIT 1
          ) t ON TRUE
         WHERE (%(ids)s::text[] IS NULL OR c.cluster_id = ANY(%(ids)s))
         ORDER BY c.cluster_id
    """,
}


# The subject types the planner can actually reach. A rule on a subject type in
# ref_subject_type but NOT here would be loaded by the engine, never planned, and
# never evaluated — so `rules/validate.py` rejects one rather than letting it be
# published as a rule that silently does nothing.
PLANNABLE_SUBJECT_TYPES: frozenset[str] = frozenset(_SUBJECT_SQL)


def _subjects(conn, subject_type: str, as_of: datetime,
              ids: Sequence[str] | None) -> list[dict]:
    sql = _SUBJECT_SQL.get(subject_type)
    if sql is None:
        return []
    return fetch_all(conn, sql, {"as_of": as_of, "ids": list(ids) if ids else None})


# ---------------------------------------------------------------- 2..8
def evaluate_batch(conn: psycopg.Connection, ctx: EngineContext,
                   requests: Sequence[EvaluationRequest]) -> list[EvaluationResult]:
    if not requests:
        return []

    # --- 2. resolve, grouped by route across the whole batch ---------------
    pairs = []
    for i, req in enumerate(requests):
        for rule in ctx.rules_for(req.subject.type, req.lane):
            for cond in rule.conditions:
                spec = ctx.specs.get(cond.feature_key)
                if spec is not None:
                    pairs.append(((i, cond.feature_key), req, spec))
    resolutions = resolve_many(conn, _dedup_pairs(pairs), ctx.graph)

    # --- 3. point-in-time read --------------------------------------------
    pit_requests: list[PitRequest] = []
    bounds: dict[tuple[int, str], datetime] = {}
    for i, req in enumerate(requests):
        for rule in ctx.rules_for(req.subject.type, req.lane):
            bound = bound_for(req.occurred_at, rule.evaluation_lag)
            bounds[(i, rule.rule_id)] = bound
            for cond in rule.conditions:
                spec = ctx.specs.get(cond.feature_key)
                res = resolutions.get((i, cond.feature_key))
                if spec is None or res is None:
                    continue
                pit_requests.append(PitRequest(
                    key=(i, rule.rule_id, cond.feature_key),
                    spec=spec, resolution=res, bound_at=bound,
                    replay_as_of=req.replay_as_of))
    reads = read_many(conn, pit_requests)

    # --- 4..8 -------------------------------------------------------------
    results: list[EvaluationResult] = []
    for i, req in enumerate(requests):
        rule_scores: list[RuleScore] = []
        degraded: list[str] = []
        feature_versions: dict[str, int] = {}
        rule_versions: dict[str, int] = {}
        max_bound = req.occurred_at

        for rule in ctx.rules_for(req.subject.type, req.lane):
            rule_reads = {
                c.feature_key: reads[(i, rule.rule_id, c.feature_key)]
                for c in rule.conditions if (i, rule.rule_id, c.feature_key) in reads
            }
            evaluation = conditions_mod.evaluate_rule(rule, rule_reads)
            rule_scores.append(scoring_mod.score_rule(rule, evaluation))
            rule_versions[rule.rule_id] = rule.version
            for key, read in rule_reads.items():
                if read.spec_version is not None:
                    feature_versions[key] = read.spec_version
            for key in evaluation.degraded:
                if key not in degraded:
                    degraded.append(key)
            max_bound = max(max_bound, bounds[(i, rule.rule_id)])

        pool = consolidate_mod.consolidate(rule_scores)
        outcome = precedence_mod.decide(rule_scores, ctx.rules, ctx.severity)
        band = bands_mod.band_for(pool.subject_score, req.subject.type, ctx.bands)
        primary = (ctx.rules[outcome.action_source_rule].name
                   if outcome.action_source_rule else None)

        results.append(EvaluationResult(
            request=req, rule_scores=rule_scores, pool=pool, band=band,
            outcome=outcome, degraded_features=sorted(degraded),
            rule_version_set=rule_versions, feature_version_set=feature_versions,
            pit_bound_at=max_bound, primary_rule_name=primary))
    return results


def _dedup_pairs(pairs):
    seen, out = set(), []
    for key, req, spec in pairs:
        if key in seen:
            continue
        seen.add(key)
        out.append((key, req, spec))
    return out


# ---------------------------------------------------------------- 9. run
def evaluate_population(conn: psycopg.Connection, ctx: EngineContext,
                        requests: Sequence[EvaluationRequest]
                        ) -> Iterable[list[EvaluationResult]]:
    """Evaluate many requests, in batches, and HAND THE RESULTS BACK.

    `run_lane(persist=False)` counted evaluations and dropped everything it
    computed on the floor, which made it useless to the one caller that wants
    the results and not the rows: the rule what-if, which evaluates a population
    against a candidate rule inside a transaction that is rolled back.

    A generator rather than a list, because the transaction lane is ~9,800
    subjects and a caller that only needs aggregates should not have to hold
    every pool in memory to get them. `run_lane` consumes it too — one chunking
    loop, so the persisting path and the collecting path cannot batch
    differently.
    """
    for start in range(0, len(requests), BATCH):
        yield evaluate_batch(conn, ctx, requests[start:start + BATCH])


def run_lane(conn: psycopg.Connection, lane: str, as_of: datetime,
             run_id: str | None = None, subject_ids: Sequence[str] | None = None,
             ctx: EngineContext | None = None, persist: bool = True) -> dict:
    ctx = ctx or EngineContext.load(conn)
    run_id = run_id or datetime.now().strftime("%Y%m%d%H%M%S")
    requests = plan_evaluations(conn, ctx, lane, as_of, run_id, subject_ids)

    totals = {"evaluations": 0, "decisions": 0, "alerts": 0, "signals": 0}
    for results in evaluate_population(conn, ctx, requests):
        totals["evaluations"] += len(results)
        if persist:
            written = persist_mod.write_batch(conn, results)
            for k, v in written.items():
                # setdefault, not `+=`: write_batch reports folds, suppressions,
                # ledger rows and executions, and a writer that learns to count
                # something new should not have to be matched by an edit here.
                totals[k] = totals.get(k, 0) + v
    return totals


def evaluate(conn: psycopg.Connection, request: EvaluationRequest,
             ctx: EngineContext | None = None) -> EvaluationResult:
    """One evaluation, no persistence. The unit the tests reason about."""
    ctx = ctx or EngineContext.load(conn)
    return evaluate_batch(conn, ctx, [request])[0]
