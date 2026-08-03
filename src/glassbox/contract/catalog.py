"""catalog.v1 — the control plane, published.

A SIBLING of alert.v1, like queue.v1, executions.v1, kpis.v1, explanation.v1,
dispositions.v1 and simulation.v1. Nothing in models.py is touched and
alert.v1's digest does not move.

This is the read surface an admin authors against. Everything on it already
existed as rows — `rule_definitions`, `rule_conditions`, `feature_catalog`, the
four `ref_*` vocabularies — and the reason to publish it as a contract rather
than let a console query the database is the same reason the alert surface is a
contract: **the dropdowns and the rejections have to agree**. A UI that offers an
operator `rules/validate.py` will refuse has invited the user to author a rule
that never fires, which is D4 in a different costume.

Three properties are deliberate.

**A rule detail carries its measured performance, per condition.** §10's whole
finding was that a condition can be *priced* at one number and *earn* another,
and the place an admin is most able to act on that is the screen where they are
editing the price. `v_condition_performance` is the source, direction-aware
precision and all — an aggravator is scored by its fraud rate and a mitigator by
its legitimate rate, because scoring a deduction as though it were an accusation
is the same inversion §5 objects to.

**Performance is null, with a reason, for a condition that has never been
evaluated.** A newly authored rule has no ledger rows behind it. Publishing
zeroes there would render as "fires 0% of the time, precision 0%" — a measurement
of nothing that looks exactly like a measurement of something bad.

**`takes_action` is computed, not assumed.** A `shadow` rule is supposed to score
without acting. Today it acts (WEEK5-PLAN D2 — `Rule.status` is loaded and never
read again), so this field publishes `true` for a shadow rule and says so in its
own docstring. Session 3 builds the gate and the field starts publishing `false`
without a console change. A defect that is visible on the wire is one somebody
can find; one that lives only in a plan file is one they cannot.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import psycopg
from pydantic import BaseModel, ConfigDict, Field

from ..catalog import EVALUATED_RULE_STATUSES, RULE_STATUSES
from ..db import fetch_all, fetch_one
from ..engine.conditions import (
    EQUALITY_OPERATORS,
    MEMBERSHIP_OPERATORS,
    NUMERIC_OPERATORS,
    SUPPORTED_OPERATORS,
)

STRICT = ConfigDict(extra="forbid", frozen=True)

COMBINE_VALUES: tuple[str, ...] = ("AND", "OR")

PERFORMANCE_BASIS = (
    "v_condition_performance over decision_conditions: every condition of every "
    "applicable rule, fired or not. precision_pct is direction-aware — the fraud "
    "rate for an aggravator, the legitimate rate for a mitigator — measured "
    "against transactions.synthetic_label, which is exact on this dataset and "
    "meaningless outside it. points_per_precision_point is what the catalog "
    "charges per unit of measured precision, and HIGH IS BAD."
)

NEVER_EVALUATED = (
    "This condition has never been evaluated, so there is nothing measured to "
    "publish. A zero here would read as a fire rate of 0% rather than as an "
    "absence of evidence."
)


# ---------------------------------------------------------------- conditions
class ConditionPerformance(BaseModel):
    """What a condition actually did, as opposed to what it was priced at."""
    model_config = STRICT

    evaluated: int
    fired: int
    degraded: int
    fire_rate_pct: Decimal | None = None
    mean_contribution: Decimal | None = None

    # Against planted ground truth. Both numbers, never just the rate.
    fired_labelled: int = 0
    fired_on_fraud: int = 0
    precision_pct: Decimal | None = None

    # Against analyst dispositions on the cases these firings landed in.
    fired_on_cases: int = 0
    alert_precision_pct: Decimal | None = None

    points_per_precision_point: Decimal | None = None
    basis: str = PERFORMANCE_BASIS


class ConditionView(BaseModel):
    model_config = STRICT

    condition_id: int
    rule_id: str
    condition_group: int
    feature_key: str
    operator: str
    threshold_num: Decimal | None = None
    threshold_text: str | None = None
    contribution_points: Decimal
    direction: str                      # aggravating | mitigating
    reason_code: str | None = None
    signal_template: str | None = None
    is_required: bool = True

    performance: ConditionPerformance | None = None
    performance_absent_because: str | None = None


# ---------------------------------------------------------------- rules
class RuleVersionView(BaseModel):
    """One published snapshot of a rule.

    `rule_versions` is empty for every rule shipped so far — nothing has ever
    published — so a stored `rule_version_set` names a version with no definition
    behind it. Session 3 closes that. Until it does, `GET /rules/{id}` returns an
    empty list here and `versions_resolve: false` rather than implying history.
    """
    model_config = STRICT

    version: int
    published_at: datetime
    published_by: str | None = None
    status: str | None = None


class RuleSummary(BaseModel):
    model_config = STRICT

    rule_id: str
    name: str
    description: str | None = None
    subject_type: str | None = None
    execution_mode: str | None = None
    action: str | None = None
    review_threshold: Decimal | None = None
    prevent_threshold: Decimal | None = None
    is_veto: bool = False
    combine: str = "AND"
    status: str = "active"
    version: int = 1
    evaluation_lag_seconds: float = 0.0
    conditions: int = 0
    created_by: str | None = None
    created_at: datetime | None = None
    shadow_since: datetime | None = None

    # Does the engine load this rule at all.
    evaluated: bool = True
    # Does a decision this rule reaches let it TAKE the action it names. See the
    # module docstring: this is `true` for a shadow rule today, which is D2 and
    # not a description of what shadow mode is supposed to mean.
    takes_action: bool = True
    status_caveat: str | None = None


class RuleDetail(RuleSummary):
    model_config = STRICT

    recommended_action_text: str | None = None
    clear_text: str | None = None
    condition_set: list[ConditionView] = Field(default_factory=list)
    versions: list[RuleVersionView] = Field(default_factory=list)
    versions_resolve: bool = False


# ---------------------------------------------------------------- features
class FeatureView(BaseModel):
    """One catalog row, spec and all.

    `default_when_absent` is published as-is, including the difference between
    SQL NULL and JSON `null`: `has_default` is the field that carries it, because
    §5's whole distinction is between "no default, let absence stay observable"
    and "0 is the real answer" and JSON cannot express that difference alone.
    """
    model_config = STRICT

    feature_key: str
    display_name: str
    description: str | None = None
    entity_type: str | None = None
    value_type: str
    window_spec: str | None = None
    inline_capable: bool
    is_graph: bool = False
    source: str | None = None
    status: str | None = None

    source_kind: str | None = None
    source_relation: str | None = None
    subject_key: str | None = None
    scope_key: str | None = None
    resolution_path: str = "auto"
    filter_predicate: Any = None
    value_expr: str | None = None
    aggregation: str | None = None
    baseline_spec: Any = None
    refresh: str = "incremental"
    max_staleness_seconds: float | None = None
    default_when_absent: Any = None
    has_default: bool = False
    fanout_policy: str = "error"
    spec_version: int = 1
    default_reason_code: str | None = None


# ---------------------------------------------------------------- reference
class NamedValue(BaseModel):
    model_config = STRICT

    value: str
    description: str | None = None


class ActionValue(BaseModel):
    model_config = STRICT

    action: str
    severity: int
    is_preventive: bool
    description: str | None = None


class OperatorValue(BaseModel):
    model_config = STRICT

    operator: str
    takes: str            # threshold_num | threshold_text | csv threshold_text
    description: str


class ReferenceVocabulary(BaseModel):
    """Every vocabulary an authoring UI needs, from the rows that enforce it.

    The point of serving this rather than hardcoding it in the console: these are
    the same values `rules/validate.py` rejects against, and the same rows the
    foreign keys enforce. A dropdown built from anything else is a dropdown that
    can offer an option the validator refuses.
    """
    model_config = STRICT

    operators: list[OperatorValue] = Field(default_factory=list)
    actions: list[ActionValue] = Field(default_factory=list)
    subject_types: list[NamedValue] = Field(default_factory=list)
    execution_modes: list[NamedValue] = Field(default_factory=list)
    reason_codes: list[NamedValue] = Field(default_factory=list)
    rule_statuses: list[str] = Field(default_factory=lambda: list(RULE_STATUSES))
    combine_values: list[str] = Field(default_factory=lambda: list(COMBINE_VALUES))
    aggregations: list[str] = Field(default_factory=list)
    source_relations: list[str] = Field(default_factory=list)


_OPERATOR_HELP: dict[str, tuple[str, str]] = {
    ">":  ("threshold_num", "value greater than the numeric threshold"),
    ">=": ("threshold_num", "value at or above the numeric threshold"),
    "<":  ("threshold_num", "value below the numeric threshold"),
    "<=": ("threshold_num", "value at or below the numeric threshold"),
    "=":  ("threshold_num or threshold_text",
           "equal; 'true'/'false' in threshold_text compares a boolean feature"),
    "!=": ("threshold_num or threshold_text", "not equal"),
    "<>": ("threshold_num or threshold_text", "not equal (SQL spelling)"),
    "in": ("threshold_text", "comma-separated set membership"),
}


# ---------------------------------------------------------------- readers
_RULE_SQL = """
SELECT r.rule_id, r.name, r.description, r.subject_type, r.execution_mode,
       r.action, r.review_threshold, r.prevent_threshold, r.is_veto, r.combine,
       r.status, r.version, r.created_by, r.created_at, r.shadow_since,
       r.recommended_action_text, r.clear_text,
       EXTRACT(EPOCH FROM r.evaluation_lag) AS evaluation_lag_seconds,
       (SELECT count(*) FROM rule_conditions c WHERE c.rule_id = r.rule_id) AS conditions
  FROM rule_definitions r
 WHERE (%(status)s::text IS NULL OR r.status = %(status)s)
   AND (%(subject_type)s::text IS NULL OR r.subject_type = %(subject_type)s)
   AND (%(rule_id)s::text IS NULL OR r.rule_id = %(rule_id)s)
 ORDER BY r.rule_id
"""

_CONDITION_SQL = """
SELECT condition_id, rule_id, condition_group, feature_key, operator,
       threshold_num, threshold_text, contribution_points, reason_code,
       signal_template, is_required
  FROM rule_conditions
 WHERE rule_id = %(rule_id)s
 ORDER BY condition_group, condition_id
"""

_PERFORMANCE_SQL = """
SELECT condition_id, evaluated, fired, degraded, fire_rate_pct,
       mean_contribution, fired_labelled, fired_on_fraud, precision_pct,
       fired_on_cases, alert_precision_pct, points_per_precision_point
  FROM v_condition_performance
 WHERE rule_id = %(rule_id)s
"""

_VERSION_SQL = """
SELECT version, published_at, published_by, status
  FROM rule_versions
 WHERE rule_id = %(rule_id)s
 ORDER BY version DESC
"""


def _summary_fields(row: dict) -> dict:
    status = row["status"] or "active"
    evaluated = status in EVALUATED_RULE_STATUSES
    caveat = None
    if status == "shadow":
        caveat = (
            "Authored as shadow. The engine currently scores, alerts and issues "
            "preventive actions for a shadow rule exactly as it does for an "
            "active one — the status is loaded and never read again "
            "(WEEK5-PLAN D2). Until the gate exists, shadow is a label, not a "
            "guarantee."
        )
    elif status == "inactive":
        caveat = "Inactive: not loaded by the engine, and retained because a rule that ever acted cannot be deleted."
    return {
        "rule_id": row["rule_id"],
        "name": row["name"],
        "description": row["description"],
        "subject_type": row["subject_type"],
        "execution_mode": row["execution_mode"],
        "action": row["action"],
        "review_threshold": row["review_threshold"],
        "prevent_threshold": row["prevent_threshold"],
        "is_veto": bool(row["is_veto"]),
        "combine": row["combine"] or "AND",
        "status": status,
        "version": row["version"],
        "evaluation_lag_seconds": float(row["evaluation_lag_seconds"] or 0),
        "conditions": row["conditions"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "shadow_since": row["shadow_since"],
        "evaluated": evaluated,
        # D2, on the wire. Session 3 makes this `status == 'active'`.
        "takes_action": evaluated,
        "status_caveat": caveat,
    }


def read_rules(conn: psycopg.Connection, status: str | None = None,
               subject_type: str | None = None) -> list[RuleSummary]:
    """Every rule, including `inactive` ones — this is the control plane, not the
    engine's view of it."""
    rows = fetch_all(conn, _RULE_SQL, {"status": status,
                                       "subject_type": subject_type,
                                       "rule_id": None})
    return [RuleSummary(**_summary_fields(r)) for r in rows]


def read_rule(conn: psycopg.Connection, rule_id: str) -> RuleDetail | None:
    row = fetch_one(conn, _RULE_SQL, {"status": None, "subject_type": None,
                                      "rule_id": rule_id})
    if row is None:
        return None

    measured = {p["condition_id"]: p
                for p in fetch_all(conn, _PERFORMANCE_SQL, {"rule_id": rule_id})}
    conditions = [
        _condition(c, measured.get(c["condition_id"]))
        for c in fetch_all(conn, _CONDITION_SQL, {"rule_id": rule_id})
    ]
    versions = [RuleVersionView(**v)
                for v in fetch_all(conn, _VERSION_SQL, {"rule_id": rule_id})]

    return RuleDetail(
        **_summary_fields(row),
        recommended_action_text=row["recommended_action_text"],
        clear_text=row["clear_text"],
        condition_set=conditions,
        versions=versions,
        # False for every rule shipped so far: nothing has ever published, so a
        # stored rule_version_set names a number with no definition behind it.
        versions_resolve=any(v.version == row["version"] for v in versions),
    )


def _condition(row: dict, measured: dict | None) -> ConditionView:
    performance = None
    if measured is not None:
        performance = ConditionPerformance(
            evaluated=measured["evaluated"], fired=measured["fired"],
            degraded=measured["degraded"],
            fire_rate_pct=measured["fire_rate_pct"],
            mean_contribution=measured["mean_contribution"],
            fired_labelled=measured["fired_labelled"],
            fired_on_fraud=measured["fired_on_fraud"],
            precision_pct=measured["precision_pct"],
            fired_on_cases=measured["fired_on_cases"],
            alert_precision_pct=measured["alert_precision_pct"],
            points_per_precision_point=measured["points_per_precision_point"],
        )
    return ConditionView(
        condition_id=row["condition_id"], rule_id=row["rule_id"],
        condition_group=row["condition_group"], feature_key=row["feature_key"],
        operator=row["operator"], threshold_num=row["threshold_num"],
        threshold_text=row["threshold_text"],
        contribution_points=row["contribution_points"],
        direction=("aggravating" if row["contribution_points"] >= 0 else "mitigating"),
        reason_code=row["reason_code"], signal_template=row["signal_template"],
        is_required=row["is_required"],
        performance=performance,
        performance_absent_because=None if performance else NEVER_EVALUATED,
    )


_FEATURE_SQL = """
SELECT feature_key, display_name, description, entity_type, value_type,
       window_spec, inline_capable, is_graph, source, status, source_kind,
       source_relation, subject_key, scope_key, resolution_path,
       filter_predicate, value_expr, aggregation, baseline_spec, refresh,
       max_staleness, default_when_absent,
       fanout_policy, spec_version, default_reason_code
  FROM feature_catalog
 WHERE (%(status)s::text IS NULL OR status = %(status)s)
   AND (%(inline_capable)s::boolean IS NULL OR inline_capable = %(inline_capable)s)
 ORDER BY feature_key
"""


def read_features(conn: psycopg.Connection, status: str | None = "active",
                  inline_capable: bool | None = None) -> list[FeatureView]:
    rows = fetch_all(conn, _FEATURE_SQL,
                     {"status": status, "inline_capable": inline_capable})
    return [
        FeatureView(
            **{k: v for k, v in r.items() if k != "max_staleness"},
            max_staleness_seconds=_seconds(r["max_staleness"]),
            # Read exactly as `catalog._json_default` reads it, rather than as
            # `IS NOT NULL` in SQL. The two differ on a JSONB `null`, and the
            # one that governs the engine's behaviour is this one.
            has_default=r["default_when_absent"] is not None,
        )
        for r in rows
    ]


def _seconds(value: timedelta | None) -> float | None:
    return None if value is None else value.total_seconds()


def read_reference(conn: psycopg.Connection) -> ReferenceVocabulary:
    """The vocabularies, from the rows that enforce them.

    `operators` and `aggregations` are the exception and deliberately so: they
    are not rows, they are the set of things the interpreter IMPLEMENTS, and
    publishing them from anywhere but the implementation would be the second
    definition D4 is about.
    """
    from ..features.aggregations import REDUCERS
    from ..features.predicate import ALLOWED_RELATIONS

    return ReferenceVocabulary(
        operators=[
            OperatorValue(operator=op, takes=_OPERATOR_HELP[op][0],
                          description=_OPERATOR_HELP[op][1])
            for op in SUPPORTED_OPERATORS
        ],
        actions=[
            ActionValue(**r) for r in fetch_all(
                conn,
                "SELECT action, severity, is_preventive, description "
                "FROM ref_action ORDER BY severity")
        ],
        subject_types=[
            NamedValue(value=r["subject_type"], description=r["description"] or None)
            for r in fetch_all(
                conn, "SELECT subject_type, description FROM ref_subject_type "
                      "ORDER BY subject_type")
        ],
        execution_modes=[
            NamedValue(value=r["mode"], description=r["description"])
            for r in fetch_all(
                conn, "SELECT mode, description FROM ref_execution_mode ORDER BY mode")
        ],
        reason_codes=[
            NamedValue(value=r["reason_code"], description=r["description"])
            for r in fetch_all(
                conn, "SELECT reason_code, description FROM ref_reason_code "
                      "ORDER BY reason_code")
        ],
        aggregations=sorted(REDUCERS),
        source_relations=sorted(ALLOWED_RELATIONS),
    )


# ---------------------------------------------------------------- drafts (input)
class ConditionDraft(BaseModel):
    """One condition as an admin authors it. An INPUT model, not part of the
    published read shape — but kept here so the pair is read together, exactly as
    `SimulationRequest` sits beside `SimulatedDecision`.

    Every field is typed loosely on purpose. The vocabularies live in `ref_*`
    rows and in the interpreter's own operator tuple, so a Pydantic `Literal`
    here would be a THIRD copy that drifts the moment somebody INSERTs a reason
    code. `rules/validate.py` is where a value is checked, against the rows.
    """
    model_config = ConfigDict(extra="forbid")

    condition_group: int = 1
    feature_key: str
    operator: str
    threshold_num: Decimal | None = None
    threshold_text: str | None = None
    contribution_points: Decimal
    reason_code: str | None = None
    signal_template: str | None = None
    is_required: bool = True


class RuleDraft(BaseModel):
    """A candidate rule. The same body simulate (session 2) and publish (session
    3) accept, so the tested thing and the written thing cannot diverge."""
    model_config = ConfigDict(extra="forbid")

    rule_id: str
    name: str
    description: str | None = None
    subject_type: str
    execution_mode: str
    action: str
    review_threshold: Decimal
    prevent_threshold: Decimal | None = None
    is_veto: bool = False
    combine: str = "AND"
    status: str = "shadow"
    evaluation_lag_seconds: float = 0.0
    recommended_action_text: str | None = None
    clear_text: str | None = None
    conditions: list[ConditionDraft] = Field(default_factory=list)
