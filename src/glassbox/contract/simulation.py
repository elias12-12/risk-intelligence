"""simulation.v1 — what the engine WOULD say, with nothing written down.

A SIBLING of alert.v1, like queue.v1, executions.v1, kpis.v1 and
explanation.v1. `Subject`, `Signal`, `Action` and `Evidence` are REUSED from
models.py exactly as they are: a simulated score bar and a stored one are the
same object seen at different moments, and giving the simulation its own signal
type would be the first step toward the two disagreeing. Reuse is free; adding
one field to any of them for this contract's benefit would change alert.v1's
bytes and break the digest, so this module adds none.

**Why this is not an AlertDetail.** An alert has an `alert_id`, a `decision_id`,
a status, a dedup key and a routing outcome, and every one of those is a fact
about a row that exists. A simulation has none of them and must not appear to:
a payload that looked like an alert would be one screenshot away from being
presented as one. `persisted` is on the wire as a literal `false` for the same
reason — the guarantee is stated by the payload, not inferred from the URL it
came from.

**The invariants travel with it.** `sum(signals) == score`, and an action that
is not `allow` names the rule that chose it. They hold here for the same reason
they hold on an alert: the pool and the precedence outcome are the engine's, not
this module's, and a simulation that could quietly break them would be a way of
seeing a number the alert surface refuses to show.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..types import jsonable
from .models import Action, ContractViolation, Evidence, Signal, Subject

STRICT = ConfigDict(extra="forbid", frozen=True)

BASIS = (
    "Simulated evaluation: the engine's own pipeline (resolve, point-in-time "
    "read, conditions, per-rule score, consolidate, band, precedence) over "
    "stored rows, inside a transaction that is rolled back. No decision, alert, "
    "signal, execution or feature value was written. Nothing here was acted on."
)


class RuleTrace(BaseModel):
    """One rule's own verdict, before consolidation and before precedence.

    Published because a simulation is something a person runs to understand WHY,
    and the two questions an alert cannot answer are "which rules looked at this
    and declined to fire" and "which fired but had no authority". The stored
    equivalent is `decision_conditions`, which exists for the population and is
    far too coarse to read one subject out of.
    """
    model_config = STRICT

    rule_id: str
    name: str
    action: str
    score: Decimal
    review_threshold: Decimal | None = None
    prevent_threshold: Decimal | None = None
    satisfied: bool
    authorised: bool
    is_veto: bool = False
    veto_established: bool | None = None      # None = indeterminate (§5)
    preventive_authority: bool = True
    degraded_features: list[str] = Field(default_factory=list)


class SimulatedDecision(BaseModel):
    model_config = STRICT

    persisted: Literal[False] = False
    subject: Subject
    lane: str
    as_of: datetime
    occurred_at: datetime | None = None

    score: Decimal
    band: str
    action: Action
    signals: list[Signal] = Field(default_factory=list)
    evidence: Evidence
    rules: list[RuleTrace] = Field(default_factory=list)

    # True when at least one rule had authority — i.e. this evaluation would have
    # produced a case. NOT "the score is in an elevated band": banding on a
    # consolidated score would surface a subject the rules deliberately let pass.
    would_alert: bool = False
    basis: str = BASIS

    @model_validator(mode="after")
    def _invariants(self) -> "SimulatedDecision":
        total = sum((s.contribution for s in self.signals), Decimal(0))
        if total != self.score:
            raise ContractViolation(
                f"simulation for {self.subject.type}:{self.subject.id}: signals "
                f"sum to {total} but the score is {self.score}. The score bar "
                f"would not add up."
            )
        if self.action.taken != "allow" and not self.action.source_rule:
            raise ContractViolation(
                f"simulation for {self.subject.type}:{self.subject.id}: action is "
                f"{self.action.taken!r} with no source_rule. An action nobody "
                f"claims is not explainable."
            )
        return self


def to_simulation(result, rules: dict, as_of: datetime) -> SimulatedDecision:
    """`EvaluationResult` -> the published shape.

    Signals come from `engine.persist.ranked_signals`, which is the same function
    that writes a stored alert's bar. One definition of what goes on the bar and
    in what order, so a simulated bar and the alert it predicts cannot disagree.
    """
    from ..engine.persist import ranked_signals   # local: avoids a cycle

    request, outcome = result.request, result.outcome
    source = rules.get(outcome.action_source_rule) if outcome.action_source_rule else None

    return SimulatedDecision(
        subject=Subject(type=request.subject.type, id=request.subject.id),
        lane=request.lane,
        as_of=as_of,
        occurred_at=request.occurred_at,
        score=result.pool.subject_score,
        band=result.band,
        action=Action(
            taken=outcome.action,
            source_rule=outcome.action_source_rule,
            vetoed_by=outcome.vetoed_by,
            prevent_threshold_met=outcome.prevent_threshold_met,
            recommended_text=source.recommended_action_text if source else None,
            clear_text=source.clear_text if source else None,
        ),
        signals=[
            Signal(
                rank=s.rank,
                feature_key=s.feature_key,
                contribution=s.contribution,
                direction=s.direction,
                human_text=s.human_text,
                reason_code=s.reason_code,
                source_rule_id=s.source_rule_id,
                asserted_by_rules=list(s.asserted_by_rules),
                feature_value=jsonable(s.feature_value),
                value_as_of=s.value_as_of,
                value_computed_at=s.value_computed_at,
            )
            for s in ranked_signals(result)
        ],
        evidence=Evidence(
            evaluation_id=request.evaluation_id,
            evaluation_trigger=request.evaluation_trigger,
            trigger_type=request.trigger.type if request.trigger else None,
            trigger_id=request.trigger.id if request.trigger else None,
            pit_bound_at=result.pit_bound_at,
            replay_as_of=request.replay_as_of,
            degraded_features=list(result.degraded_features),
            rule_version_set=dict(result.rule_version_set),
            feature_version_set=dict(result.feature_version_set),
        ),
        rules=[_trace(rs, rules[rs.rule_id], outcome) for rs in result.rule_scores],
        would_alert=bool(outcome.authorised_rules),
    )


def _trace(rule_score, rule, outcome) -> RuleTrace:
    evaluation = rule_score.evaluation
    return RuleTrace(
        rule_id=rule.rule_id,
        name=rule.name,
        action=rule.action,
        score=rule_score.score,
        review_threshold=rule.review_threshold,
        prevent_threshold=rule.prevent_threshold,
        satisfied=evaluation.satisfied,
        authorised=rule.rule_id in outcome.authorised_rules,
        is_veto=rule.is_veto,
        veto_established=evaluation.veto_established,
        preventive_authority=evaluation.preventive_authority,
        degraded_features=list(evaluation.degraded),
    )


class SimulationRequest(BaseModel):
    """What a caller sends to /simulate/subject. Not part of the published read
    shape — an input model — but kept here so the pair is read together."""
    model_config = ConfigDict(extra="forbid")

    subject_type: str
    subject_id: str
    lane: Literal["inline_sync", "async"] = "inline_sync"
    as_of: datetime | None = None
    # The `computed_at` ceiling. Set it to a stored decision's `decided_at` to
    # ask what that decision saw, rather than re-scoring with today's values.
    replay_as_of: datetime | None = None
