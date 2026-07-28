"""The shapes that move between pipeline stages.

Deliberately dataclasses, not Pydantic: these are internal. The Pydantic models
in contract/ are the published surface, and keeping the two apart is what lets
the contract be frozen while the engine keeps moving.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

# ---------------------------------------------------------------- statuses
FeatureStatus = Literal["present", "absent", "stale", "unresolvable", "fanout_error"]
DEGRADED_STATUSES: frozenset[str] = frozenset(
    {"absent", "stale", "unresolvable", "fanout_error"}
)

_WINDOW_RE = re.compile(r"^\s*(\d+)\s*([smhdw])\s*$", re.IGNORECASE)
_WINDOW_UNITS = {
    "s": "seconds",
    "m": "minutes",
    "h": "hours",
    "d": "days",
    "w": "weeks",
}


def parse_window(spec: str | None) -> timedelta | None:
    """'90s' -> 90 seconds. Returns None for a stateless feature.

    Parsed in Python rather than cast in SQL so a malformed catalog value fails
    at load with a readable message instead of inside a query plan.
    """
    if spec is None or str(spec).strip() == "":
        return None
    m = _WINDOW_RE.match(str(spec))
    if not m:
        raise ValueError(f"unparseable window_spec: {spec!r} (expected e.g. '90s', '24h', '7d')")
    return timedelta(**{_WINDOW_UNITS[m.group(2).lower()]: int(m.group(1))})


# ---------------------------------------------------------------- subjects
@dataclass(frozen=True)
class Subject:
    type: str
    id: str

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.type}:{self.id}"


@dataclass(frozen=True)
class EvaluationRequest:
    """One pass of one lane over one subject (§2.2), plus the triggering row.

    trigger is what makes §3.2's `trigger` resolution root possible: S-077's
    subject is an account, but 'the transfer came from a datacenter IP' is a
    fact about one specific transaction, not about the account's history.
    """
    evaluation_id: str
    subject: Subject
    lane: str                        # inline_sync | async
    occurred_at: datetime
    trigger: Subject | None = None
    evaluation_trigger: str | None = None   # human label: what caused this pass
    replay_as_of: datetime | None = None    # computed_at ceiling; None = live


# ---------------------------------------------------------------- features
@dataclass(frozen=True)
class FeatureSpec:
    feature_key: str
    display_name: str
    entity_type: str
    value_type: str
    window_spec: str | None
    source_kind: str | None
    source_relation: str | None
    subject_key: str | None
    scope_key: str | None
    resolution_path: str
    filter_predicate: dict | None
    value_expr: str | None
    aggregation: str | None
    baseline_spec: dict | None
    refresh: str
    max_staleness: timedelta | None
    default_when_absent: Any
    has_default: bool
    fanout_policy: str
    spec_version: int
    default_reason_code: str | None
    inline_capable: bool

    @property
    def window(self) -> timedelta | None:
        return parse_window(self.window_spec)


@dataclass(frozen=True)
class Resolution:
    """Which entities a subject's feature actually keys on."""
    feature_key: str
    entity_type: str
    entity_ids: tuple[str, ...]
    route: str                       # how it was reached, for the audit trail
    status: Literal["ok", "unresolvable"] = "ok"
    reason: str | None = None


@dataclass(frozen=True)
class FeatureRead:
    feature_key: str
    status: FeatureStatus
    value: Any = None
    as_of: datetime | None = None
    computed_at: datetime | None = None
    spec_version: int | None = None
    entity_type: str | None = None
    entity_ids: tuple[str, ...] = ()
    reason: str | None = None

    @property
    def is_degraded(self) -> bool:
        return self.status in DEGRADED_STATUSES


# ---------------------------------------------------------------- rules
@dataclass(frozen=True)
class RuleCondition:
    condition_id: int
    rule_id: str
    condition_group: int
    feature_key: str
    operator: str
    threshold_num: Decimal | None
    threshold_text: str | None
    contribution_points: Decimal
    reason_code: str | None
    signal_template: str | None
    is_required: bool

    @property
    def is_mitigating(self) -> bool:
        return self.contribution_points < 0


@dataclass(frozen=True)
class Rule:
    rule_id: str
    name: str
    subject_type: str
    execution_mode: str
    action: str
    review_threshold: Decimal
    combine: str
    status: str
    version: int
    is_veto: bool
    prevent_threshold: Decimal | None
    evaluation_lag: timedelta
    recommended_action_text: str | None
    clear_text: str | None
    conditions: tuple[RuleCondition, ...]


@dataclass(frozen=True)
class FiredCondition:
    condition: RuleCondition
    read: FeatureRead
    human_text: str


@dataclass
class RuleEvaluation:
    rule_id: str
    satisfied: bool
    fired: list[FiredCondition] = field(default_factory=list)
    degraded: list[str] = field(default_factory=list)
    preventive_authority: bool = True
    veto_established: bool | None = False   # None = indeterminate
    reads: dict[str, FeatureRead] = field(default_factory=dict)


@dataclass
class RuleScore:
    rule_id: str
    score: Decimal
    evaluation: RuleEvaluation


# ---------------------------------------------------------------- output
@dataclass
class Signal:
    feature_key: str | None
    contribution: Decimal
    direction: str                   # aggravating | mitigating | veto
    human_text: str
    reason_code: str | None
    source_rule_id: str | None
    asserted_by_rules: list[str] = field(default_factory=list)
    feature_value: Any = None
    value_as_of: datetime | None = None
    value_computed_at: datetime | None = None
    rank: int = 0


@dataclass
class SignalPool:
    signals: list[Signal]
    subject_score: Decimal


@dataclass
class ActionOutcome:
    action: str
    action_source_rule: str | None
    vetoed_by: str | None
    prevent_threshold_met: bool | None
    authorised_rules: list[str]
    veto_signals: list[Signal] = field(default_factory=list)
