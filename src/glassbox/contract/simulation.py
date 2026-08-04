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

from ..engine.simulate import (
    ARRIVAL_DEFAULTS,
    DEFAULT_SAMPLE_CAP,
    FABRICATION_BASIS,
    SAMPLING_BASIS,
    SHADOW_NOTE,
)
from ..types import jsonable
from .catalog import RuleDraft
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
    # A shadow rule is evaluated and traced exactly like a live one and is
    # excluded from the score, the signals and the action (migration 0030).
    # Published here because "which rules looked at this" is the question a
    # trace answers, and a rule that looked and was not allowed to speak is
    # still a rule that looked.
    shadow: bool = False


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

    # What this evaluation would have been had the rules in `shadow_rules` been
    # active. Null when nothing was in shadow. Mirrors decisions.shadow_* so a
    # simulated answer and a stored one are read the same way.
    shadow_rules: list[str] = Field(default_factory=list)
    shadow_score: Decimal | None = None
    shadow_action: str | None = None

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
        rules=([_trace(rs, rules[rs.rule_id], outcome) for rs in result.rule_scores]
               + [_trace(rs, rules[rs.rule_id], outcome, shadow=True)
                  for rs in result.shadow_scores]),
        would_alert=bool(outcome.authorised_rules),
        shadow_rules=list(result.shadow.rules) if result.shadow else [],
        shadow_score=result.shadow.score if result.shadow else None,
        shadow_action=result.shadow.action if result.shadow else None,
    )


def _trace(rule_score, rule, outcome, shadow: bool = False) -> RuleTrace:
    evaluation = rule_score.evaluation
    return RuleTrace(
        shadow=shadow,
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


# ------------------------------------------------------------------ rule what-if
RULE_BASIS = (
    "A candidate rule applied to the control plane inside a transaction that is "
    "rolled back, then the engine's own pipeline over stored rows. No rule, "
    "condition, decision, alert, signal or execution was written. Every count "
    "below is over subjects_evaluated, which is not the population — see "
    "population.basis."
)

HYGIENE_CAVEAT = (
    "would_alert counts EVALUATIONS that would raise a case, not cases. §9's "
    "hygiene — folding a repeat onto an open case, restating it, suppressing "
    "under an investigation — happens at persist time, and nothing is persisted "
    "here. On a subject that already has an open case the real cycle would fold "
    "rather than raise, so this is an upper bound on new cases."
)

GROUND_TRUTH_CAVEAT = (
    "Measured against transactions.synthetic_label on the row that triggered "
    "each evaluation. Planted ground truth: exact on this dataset and "
    "meaningless outside it."
)


class SampledPopulation(BaseModel):
    """What was actually evaluated, and what it was drawn from.

    The cap is published as the denominator rather than applied silently,
    because a rate whose denominator is a sample and does not say so is the same
    unearned claim §11 refuses on a KPI tile.
    """
    model_config = STRICT

    subject_type: str
    lane: str
    as_of: datetime
    subjects_available: int
    subjects_evaluated: int
    sample_cap: int
    truncated: bool
    basis: str


class ScoreDistribution(BaseModel):
    model_config = STRICT

    scored: int                              # subjects whose pooled score > 0
    bands: dict[str, int] = Field(default_factory=dict)
    min_score: Decimal | None = None
    median_score: Decimal | None = None
    max_score: Decimal | None = None


class GroundTruth(BaseModel):
    """Both numbers, never just the rate."""
    model_config = STRICT

    labelled: int = 0
    flagged: int = 0
    flagged_labelled: int = 0
    flagged_on_fraud: int = 0
    precision_pct: Decimal | None = None
    caveat: str = GROUND_TRUTH_CAVEAT


class ConditionOutcomeSummary(BaseModel):
    """One condition of the candidate, measured over the sample.

    The stored `condition_id` is deliberately absent: a draft's conditions are
    inserted inside the rolled-back scope, so their ids are real for the length
    of one transaction and mean nothing afterwards. Publishing one would be
    publishing a key that resolves to a different row, or to none.
    """
    model_config = STRICT

    condition_group: int
    feature_key: str
    operator: str
    threshold: str | None = None
    contribution_points: Decimal
    direction: str
    is_required: bool = True

    evaluated: int = 0
    fired: int = 0
    degraded: int = 0
    fire_rate_pct: Decimal | None = None
    fired_labelled: int = 0
    fired_on_fraud: int = 0
    precision_pct: Decimal | None = None      # direction-aware, as §10 measures it


class WorkedExample(BaseModel):
    model_config = STRICT

    subject_id: str
    occurred_at: datetime | None = None
    score: Decimal
    band: str
    action: str
    action_source_rule: str | None = None
    would_alert: bool
    label: str | None = None
    signals: list[Signal] = Field(default_factory=list)


class DecisionDiff(BaseModel):
    """A subject whose stored decision and simulated decision disagree.

    `stored_*` is null where the subject has no stored decision for this lane —
    which is not a change of mind, it is the rule reaching something nothing
    reached before, and `change` says which of the two it is.
    """
    model_config = STRICT

    subject_id: str
    change: str                        # new | score | action | score+action
    stored_score: Decimal | None = None
    simulated_score: Decimal
    stored_action: str | None = None
    simulated_action: str
    stored_source_rule: str | None = None
    simulated_source_rule: str | None = None


class RuleSimulation(BaseModel):
    """What a candidate rule WOULD do, with nothing written down.

    Published as part of simulation.v1 rather than catalog.v1 because it is a
    simulation: the guarantee it carries is `persisted: false`, and that
    guarantee is what this contract is for.
    """
    model_config = STRICT

    persisted: Literal[False] = False
    rule_id: str
    rule_name: str
    mode: str                          # draft | replacement
    status: str                        # what the draft ASKS to be published as
    # ...and what it was evaluated as, which is always `active`. A shadow rule
    # holds no authority and contributes no signal, so simulating one as
    # authored would report that it does nothing — accurate, and useless.
    evaluated_as: str = "active"
    shadow_note: str = SHADOW_NOTE
    action: str
    review_threshold: Decimal
    prevent_threshold: Decimal | None = None

    population: SampledPopulation

    would_fire: int = 0                # the candidate was satisfied and scored
    would_authorise: int = 0           # ...and crossed its own review threshold
    would_carry_action: int = 0        # ...and carried the action after precedence
    would_alert: int = 0               # the evaluation would raise a case at all
    hygiene_caveat: str = HYGIENE_CAVEAT

    distribution: ScoreDistribution
    ground_truth: GroundTruth
    conditions: list[ConditionOutcomeSummary] = Field(default_factory=list)
    examples: list[WorkedExample] = Field(default_factory=list)

    diff_total: int = 0
    diff_new: int = 0
    diff_score_changed: int = 0
    diff_action_changed: int = 0
    diff: list[DecisionDiff] = Field(default_factory=list)

    basis: str = RULE_BASIS


def to_rule_simulation(whatif, examples: int = 5,
                       diff_limit: int = 25) -> RuleSimulation:
    """`RuleWhatIf` -> the published shape.

    Duck-typed rather than imported: `engine.simulate` imports this package's
    `RuleDraft`, so naming its dataclass here would close the cycle.
    """
    from ..engine.persist import ranked_signals

    draft = whatif.draft
    rule_id = draft.rule_id
    scored: list[Decimal] = []
    bands: dict[str, int] = {}
    per_condition: dict[tuple, dict] = {}
    counts = {"fire": 0, "authorise": 0, "carry": 0, "alert": 0}
    flagged = flagged_labelled = flagged_on_fraud = labelled = 0
    chosen: list = []
    diffs: list[DecisionDiff] = []

    for result in whatif.results:
        label = _label_of(whatif, result)
        if label is not None:
            labelled += 1

        candidate = next((rs for rs in result.rule_scores if rs.rule_id == rule_id),
                         None)
        if candidate is None:
            # The rule does not apply to this subject at all. Recorded by its
            # absence from the counts rather than as a zero.
            continue

        _accumulate_conditions(per_condition, candidate, label)

        outcome = result.outcome
        fired = bool(candidate.evaluation.fired) and candidate.evaluation.satisfied
        authorised = rule_id in outcome.authorised_rules
        counts["fire"] += int(fired)
        counts["authorise"] += int(authorised)
        counts["carry"] += int(outcome.action_source_rule == rule_id)
        counts["alert"] += int(bool(outcome.authorised_rules))

        if authorised:
            flagged += 1
            if label is not None:
                flagged_labelled += 1
                flagged_on_fraud += int(label == "fraud")

        score = result.pool.subject_score
        if score > 0:
            scored.append(score)
        bands[result.band] = bands.get(result.band, 0) + 1

        if authorised and len(chosen) < examples:
            chosen.append((result, label, ranked_signals(result)))

        diff = _diff(whatif, result)
        if diff is not None:
            diffs.append(diff)

    ordered = sorted(scored)
    evaluated = len(whatif.results)

    return RuleSimulation(
        rule_id=rule_id, rule_name=draft.name, mode=whatif.mode,
        status=draft.status, evaluated_as=whatif.evaluated_as,
        action=draft.action,
        review_threshold=draft.review_threshold,
        prevent_threshold=draft.prevent_threshold,
        population=SampledPopulation(
            subject_type=whatif.subject_type, lane=whatif.lane,
            as_of=whatif.as_of,
            subjects_available=whatif.subjects_available,
            subjects_evaluated=evaluated,
            sample_cap=whatif.sample_cap,
            truncated=evaluated < whatif.subjects_available,
            basis=SAMPLING_BASIS,
        ),
        would_fire=counts["fire"], would_authorise=counts["authorise"],
        would_carry_action=counts["carry"], would_alert=counts["alert"],
        distribution=ScoreDistribution(
            scored=len(ordered), bands=bands,
            min_score=ordered[0] if ordered else None,
            median_score=ordered[len(ordered) // 2] if ordered else None,
            max_score=ordered[-1] if ordered else None,
        ),
        ground_truth=GroundTruth(
            labelled=labelled, flagged=flagged,
            flagged_labelled=flagged_labelled,
            flagged_on_fraud=flagged_on_fraud,
            precision_pct=_pct(flagged_on_fraud, flagged_labelled),
        ),
        conditions=[_condition_summary(v) for v in per_condition.values()],
        examples=[
            WorkedExample(
                subject_id=r.request.subject.id, occurred_at=r.request.occurred_at,
                score=r.pool.subject_score, band=r.band,
                action=r.outcome.action,
                action_source_rule=r.outcome.action_source_rule,
                would_alert=bool(r.outcome.authorised_rules), label=label,
                signals=[
                    Signal(
                        rank=s.rank, feature_key=s.feature_key,
                        contribution=s.contribution, direction=s.direction,
                        human_text=s.human_text, reason_code=s.reason_code,
                        source_rule_id=s.source_rule_id,
                        asserted_by_rules=list(s.asserted_by_rules),
                        feature_value=jsonable(s.feature_value),
                        value_as_of=s.value_as_of,
                        value_computed_at=s.value_computed_at,
                    )
                    for s in signals
                ],
            )
            for r, label, signals in chosen
        ],
        diff_total=len(diffs),
        diff_new=sum(1 for d in diffs if d.change == "new"),
        diff_score_changed=sum(1 for d in diffs if "score" in d.change),
        diff_action_changed=sum(1 for d in diffs if "action" in d.change),
        diff=diffs[:diff_limit],
    )


def _label_of(whatif, result) -> str | None:
    trigger = result.request.trigger
    return whatif.labels.get(trigger.id) if trigger else None


def _pct(numerator: int, denominator: int) -> Decimal | None:
    if not denominator:
        return None
    return (Decimal(numerator) * 100 / Decimal(denominator)).quantize(Decimal("0.01"))


def _accumulate_conditions(acc: dict, candidate, label: str | None) -> None:
    for visited in candidate.evaluation.evaluated:
        cond = visited.condition
        key = (cond.condition_group, cond.feature_key, cond.operator,
               str(cond.threshold_num), str(cond.threshold_text))
        row = acc.setdefault(key, {
            "condition": cond, "evaluated": 0, "fired": 0, "degraded": 0,
            "fired_labelled": 0, "fired_on_fraud": 0})
        row["evaluated"] += 1
        row["degraded"] += int(visited.read.is_degraded)
        if visited.fired:
            row["fired"] += 1
            if label is not None:
                row["fired_labelled"] += 1
                row["fired_on_fraud"] += int(label == "fraud")


def _condition_summary(row: dict) -> ConditionOutcomeSummary:
    cond = row["condition"]
    aggravating = cond.contribution_points >= 0
    on_fraud, labelled = row["fired_on_fraud"], row["fired_labelled"]
    # Direction-aware, exactly as v_condition_performance measures it: an
    # aggravator is right when it fires on fraud, a mitigator when it fires on
    # legitimate traffic. Scoring a deduction as though it were an accusation is
    # the inversion §5 objects to, and it ranks the best condition in the catalog
    # as the worst.
    hits = on_fraud if aggravating else (labelled - on_fraud)
    return ConditionOutcomeSummary(
        condition_group=cond.condition_group, feature_key=cond.feature_key,
        operator=cond.operator,
        threshold=(str(cond.threshold_num) if cond.threshold_num is not None
                   else cond.threshold_text),
        contribution_points=cond.contribution_points,
        direction="aggravating" if aggravating else "mitigating",
        is_required=cond.is_required,
        evaluated=row["evaluated"], fired=row["fired"], degraded=row["degraded"],
        fire_rate_pct=_pct(row["fired"], row["evaluated"]),
        fired_labelled=labelled, fired_on_fraud=on_fraud,
        precision_pct=_pct(hits, labelled),
    )


def _diff(whatif, result) -> DecisionDiff | None:
    """Only subjects whose decision would MOVE. A diff listing everything that
    stayed the same is a diff nobody reads."""
    subject_id = result.request.subject.id
    stored = whatif.stored.get(subject_id)
    score = result.pool.subject_score
    action = result.outcome.action

    if stored is None:
        if score == 0 and action == "allow":
            return None            # nothing before, nothing now
        return DecisionDiff(
            subject_id=subject_id, change="new", simulated_score=score,
            simulated_action=action,
            simulated_source_rule=result.outcome.action_source_rule)

    stored_score = Decimal(str(stored["score"]))
    moved = []
    if stored_score != score:
        moved.append("score")
    if stored["action_taken"] != action:
        moved.append("action")
    if not moved:
        return None
    return DecisionDiff(
        subject_id=subject_id, change="+".join(moved),
        stored_score=stored_score, simulated_score=score,
        stored_action=stored["action_taken"], simulated_action=action,
        stored_source_rule=stored["action_source_rule"],
        simulated_source_rule=result.outcome.action_source_rule)


# ------------------------------------------------------- hypothetical charge
class FabricationLimit(BaseModel):
    """One thing this answer cannot tell you, and why.

    Published as rows rather than as one prose caveat because they are not all
    the same KIND of limit — two are properties of the feature layer, one is a
    property of what was evaluated, one is a property of what a fabricated row
    is allowed to carry — and because `features` lets a console name the
    specific keys instead of asserting a general shape.
    """
    model_config = STRICT

    code: str
    detail: str
    features: list[str] = Field(default_factory=list)


class ScopedFeaturePass(BaseModel):
    """What the fabricated row was actually scored against.

    Without this a caller cannot tell a feature that was recomputed with the new
    charge in it from one that was read at its stored value — and those are
    different claims about the same number. `card_cnp_count` is the first kind;
    a graph feature is the second.
    """
    model_config = STRICT

    as_of: datetime
    recomputed: dict[str, int] = Field(default_factory=dict)   # key -> rows written
    not_recomputed: dict[str, str] = Field(default_factory=dict)  # key -> why


class TransactionSimulation(BaseModel):
    """A charge that never happened, scored, with nothing written down.

    A sibling on simulation.v1, and `decision` is a `SimulatedDecision` reused
    whole rather than flattened: the score bar for a fabricated charge and the
    score bar for a stored one are the same object, and the §1 invariants are
    already enforced on it.

    `fabricated` is the row as INSERTED, including everything the caller left
    unstated — a reader comparing a score against the charge they described
    rather than the one that was scored is exactly the confusion this endpoint
    is most able to cause.
    """
    model_config = STRICT

    persisted: Literal[False] = False
    fabricated: dict[str, Any] = Field(default_factory=dict)
    lane: str
    features: ScopedFeaturePass
    decision: SimulatedDecision
    limits: list[FabricationLimit] = Field(default_factory=list)
    basis: str = FABRICATION_BASIS


def to_transaction_simulation(whatif, rules: dict) -> TransactionSimulation:
    """`TransactionWhatIf` -> the published shape.

    The limits are DERIVED from what the pass actually did, not written out as
    a constant: `not_recomputed` comes back from the runner keyed by feature
    with the reason attached, and the novelty list is whichever features carry a
    `baseline_lag` today rather than the two that carry it now. A caveat that
    cannot go stale is the same argument session 1 made for the disposition
    tiles.
    """
    elsewhere = sorted(whatif.features_elsewhere)
    uncomputable = sorted(whatif.features_uncomputable)

    limits = [
        FabricationLimit(
            code="novelty_cannot_self_establish",
            detail=(
                "Novelty features look at history up to as_of minus their "
                "baseline_lag (seed 0019), so the fabricated charge cannot make "
                "its own merchant category or country look familiar — and "
                "equally cannot be made familiar by anything else at the same "
                "instant. That is the feature layer being correct, and it means "
                "a fabricated FIRST charge of a kind reads as new however many "
                "you fabricate."),
            features=list(whatif.novelty_features)),
        FabricationLimit(
            code="not_recomputed",
            detail=(
                "These features are driven by a relation other than "
                "`transactions` — the link layer, the event log, the cluster "
                "members — so an arriving charge does not change them and the "
                "scoped pass did not run them. They were read at their STORED "
                "value, which is the right answer for a graph that a "
                "hypothetical charge would not have rebuilt anyway."),
            features=elsewhere),
        FabricationLimit(
            code="no_ground_truth",
            detail=(
                "The row carries no synthetic_label, and the engine refuses one: "
                "it is planted ground truth, and a fabricated charge that "
                "labelled itself would be answering the question it was asked. "
                "So this decision sits outside every ground-truth join — no "
                "false-negative rate, no precision, no §11 tile counts it."),
            features=[]),
        FabricationLimit(
            code="transaction_subject_only",
            detail=(
                "Only the fabricated transaction is evaluated, in one lane. The "
                "card, account, customer, merchant and device it references are "
                "not re-evaluated, so a rule whose subject is one of those — "
                "S-077 on an account, L-203 on a network — does not appear here "
                "even though the charge would have reached it in a real cycle."),
            features=[]),
    ]
    if uncomputable:
        limits.append(FabricationLimit(
            code="unsupported_spec",
            detail=("The runner cannot compute these at all — the `sequence` "
                    "source kind is still unbuilt — so they were read at their "
                    "stored value, hand-seeded or absent."),
            features=uncomputable))

    return TransactionSimulation(
        fabricated=jsonable_row(whatif.row),
        lane=whatif.lane,
        features=ScopedFeaturePass(
            as_of=whatif.as_of,
            recomputed=dict(whatif.features_recomputed),
            not_recomputed=dict(whatif.features_not_recomputed)),
        decision=to_simulation(whatif.result, rules, as_of=whatif.as_of),
        limits=limits,
    )


def jsonable_row(row: dict) -> dict[str, Any]:
    """The fabricated row, as JSON. `Decimal` and `datetime` both appear in it —
    the first through `jsonable`, the second by isoformat, because the echo has
    to survive a round trip through the wire and back into a test."""
    out: dict[str, Any] = {}
    for key, value in sorted(row.items()):
        out[key] = value.isoformat() if isinstance(value, datetime) else jsonable(value)
    return out


class TransactionDraft(BaseModel):
    """A charge to invent. The writable subset of `transactions`, closed.

    Every reference is to an entity that must ALREADY EXIST — `transactions` has
    foreign keys to cards, accounts, customers, merchants and devices (0003), and
    the engine checks them before the sandbox opens so an unknown card is an
    answer rather than an IntegrityError. The console picks a real card and makes
    up the charge.

    `synthetic_label` is deliberately absent and is refused by the engine even if
    a caller reaches it another way: it is planted ground truth.

    An unstated `currency`, `direction`, `txn_type` or `auth_result` takes the
    value in `engine.simulate.ARRIVAL_DEFAULTS`, which is the same default
    `generate_synthetic.mk_txn` applies to every row it writes. `amount_base`
    falls back to `amount`, and `txn_id` is generated as `SIM-…` when unstated.
    """
    model_config = ConfigDict(extra="forbid")

    txn_id: str | None = None
    occurred_at: datetime | None = None
    amount: Decimal
    currency: str | None = None
    amount_base: Decimal | None = None
    direction: str | None = None
    txn_type: str | None = None

    card_id: str | None = None
    account_id: str | None = None
    customer_id: str | None = None
    merchant_id: str | None = None
    device_id: str | None = None

    mcc: str | None = None
    channel: str | None = None
    entry_mode: str | None = None
    auth_result: str | None = None
    decline_reason: str | None = None

    txn_country: str | None = None
    txn_lat: Decimal | None = None
    txn_lon: Decimal | None = None
    ip_address: str | None = None

    payee_id: str | None = None
    counterparty: str | None = None
    billing_country: str | None = None
    shipping_country: str | None = None

    def columns(self) -> dict[str, Any]:
        """The stated columns only. An omitted field is not the same as a null
        one — `prepare` fills the omissions, and a column explicitly set to null
        would be indistinguishable from an omission if this returned both."""
        return {k: v for k, v in self.model_dump().items() if v is not None}


class TransactionSimulationRequest(BaseModel):
    """What an admin sends to /simulate/transaction (WEEK5-PLAN O4)."""
    model_config = ConfigDict(extra="forbid")

    transaction: TransactionDraft
    lane: Literal["inline_sync", "async"] = "inline_sync"


assert set(ARRIVAL_DEFAULTS) <= set(TransactionDraft.model_fields), (
    "a default exists for a column the draft cannot express")


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


class RuleSimulationRequest(BaseModel):
    """What an admin sends to /simulate/rule.

    `rule` is a `RuleDraft` — the SAME body `POST /rules` will accept in session
    3, validated by the same `rules/validate.py`. That is decision 6: the test
    path and the write path share a validator so the tested thing and the written
    thing cannot diverge, and they stay separate endpoints so the test path can
    never accidentally become the write path.
    """
    model_config = ConfigDict(extra="forbid")

    rule: RuleDraft
    as_of: datetime | None = None
    # O1, answered: default 2,000, overridable, and published as the denominator.
    sample_cap: int = Field(default=DEFAULT_SAMPLE_CAP, ge=1, le=50_000)
    subject_ids: list[str] | None = None
    examples: int = Field(default=5, ge=0, le=50)
    diff_limit: int = Field(default=25, ge=0, le=500)
