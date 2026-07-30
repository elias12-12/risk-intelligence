"""The published read contract (§12), frozen before Week 3 begins.

The Pydantic models are the source of truth; contract/alert.v1.schema.json is
the generated, committed artifact. test_contract.py regenerates the schema in
memory and asserts byte-equality, so any model change breaks the build and
unfreezing becomes a visible diff in a pull request. alert.v1.schema.json is
NEVER edited: a breaking change becomes alert.v2.schema.json alongside it, and
v1 keeps being served. That is what "frozen and published" means operationally,
and it is what lets a console bind to this without fear.

Two invariants are enforced here, on the server, as a raising validator:

    sum(signals) == score
    action.source_rule is present whenever action.taken != 'allow'

They are enforced in three redundant layers — here, in db/views/v_alert_invariants.sql,
and in the test suite — because §1 says this is the property everything else
serves. The API returns 500 rather than a payload that lies, which is the right
failure for a system whose product is the explanation.

All arithmetic is Decimal (psycopg maps NUMERIC natively), so `==` is exact and
needs no tolerance.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

STRICT = ConfigDict(extra="forbid", frozen=True)


class ContractViolation(ValueError):
    """An alert whose numbers do not explain themselves. Never returned to a
    caller as data — it is a 500.

    Pydantic WRAPS anything a model_validator raises in a ValidationError, so
    callers that want to trap this must catch ValidationError as well; the
    original message is preserved inside it. `is_contract_violation` exists so
    that check lives in one place rather than being re-derived at each call
    site (and forgotten at one of them).
    """


def is_contract_violation(exc: BaseException) -> bool:
    if isinstance(exc, ContractViolation):
        return True
    return any(
        isinstance(err.get("ctx", {}).get("error"), ContractViolation)
        for err in getattr(exc, "errors", lambda: [])()
    )


class Subject(BaseModel):
    model_config = STRICT
    type: str
    id: str
    role: str | None = None


class Signal(BaseModel):
    model_config = STRICT
    rank: int
    feature_key: str | None
    contribution: Decimal
    direction: Literal["aggravating", "mitigating", "veto"]
    human_text: str
    reason_code: str | None = None
    source_rule_id: str | None = None
    asserted_by_rules: list[str] = Field(default_factory=list)
    feature_value: Any = None
    value_as_of: datetime | None = None
    value_computed_at: datetime | None = None


class Action(BaseModel):
    model_config = STRICT
    taken: str
    source_rule: str | None = None
    vetoed_by: str | None = None
    prevent_threshold_met: bool | None = None
    recommended_text: str | None = None
    clear_text: str | None = None


class Evidence(BaseModel):
    """What the decision could see, and what it could not."""
    model_config = STRICT
    evaluation_id: str | None = None
    evaluation_trigger: str | None = None
    trigger_type: str | None = None
    trigger_id: str | None = None
    pit_bound_at: datetime | None = None
    replay_as_of: datetime | None = None
    degraded_features: list[str] = Field(default_factory=list)
    rule_version_set: dict[str, int] = Field(default_factory=dict)
    feature_version_set: dict[str, int] = Field(default_factory=dict)


class AlertSummary(BaseModel):
    model_config = STRICT
    alert_id: int
    subject: Subject
    title: str
    score: Decimal
    band: str
    status: str
    action_taken: str
    dedup_key: str | None = None
    created_at: datetime


class AlertDetail(BaseModel):
    model_config = STRICT
    alert_id: int
    decision_id: int
    subject: Subject
    subjects: list[Subject] = Field(default_factory=list)
    title: str
    score: Decimal
    band: str
    status: str
    occurred_at: datetime | None = None
    decided_at: datetime | None = None
    execution_mode: str | None = None
    action: Action
    signals: list[Signal] = Field(default_factory=list)
    evidence: Evidence
    rules_fired: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _invariants(self) -> "AlertDetail":
        total = sum((s.contribution for s in self.signals), Decimal(0))
        if total != self.score:
            raise ContractViolation(
                f"alert {self.alert_id}: signals sum to {total} but the score is "
                f"{self.score}. The score bar would not add up."
            )
        if self.action.taken != "allow" and not self.action.source_rule:
            raise ContractViolation(
                f"alert {self.alert_id}: action is {self.action.taken!r} with no "
                f"source_rule. An action nobody claims is not explainable."
            )
        return self
