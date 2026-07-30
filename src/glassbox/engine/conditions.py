"""Condition evaluation and the §5 degraded-evidence policy.

Satisfaction semantics, stated once so nobody has to guess:

  * conditions within a condition_group are OR'd
  * groups are combined by rule_definitions.combine
  * ONLY is_required conditions participate in satisfaction
  * contribution is summed over conditions that FIRED, required or not

The last two are what make a mitigator a mitigator. Under plain AND over all
conditions, T-021 could not be satisfied unless its mitigators fired — which
inverts what a mitigator is for.

The §5 policy, per condition whose evidence did not arrive:

    contribution >= 0  ->  0 points, feature recorded as degraded
    contribution <  0  ->  0 points, degraded, AND preventive_authority = False
    veto rule          ->  veto becomes indeterminate
    is_required        ->  the rule is not satisfied

A missing mitigator RAISES the score. That is the correct arithmetic and the
wrong action, which is precisely why it also strips preventive authority.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from ..types import (
    DEGRADED_STATUSES,
    FeatureRead,
    FiredCondition,
    Rule,
    RuleCondition,
    RuleEvaluation,
)


def _as_decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001
        return None


def fires(cond: RuleCondition, value: Any) -> bool:
    """Apply one stored operator to one read value."""
    op = cond.operator
    if op in (">", ">=", "<", "<="):
        num = _as_decimal(value)
        if num is None or cond.threshold_num is None:
            return False
        thr = Decimal(cond.threshold_num)
        return {">": num > thr, ">=": num >= thr, "<": num < thr, "<=": num <= thr}[op]

    if op in ("=", "!=", "<>"):
        negate = op != "="
        if cond.threshold_text in ("true", "false"):
            hit = bool(value) is (cond.threshold_text == "true") and value is not None
        elif cond.threshold_num is not None:
            num = _as_decimal(value)
            hit = num is not None and num == Decimal(cond.threshold_num)
        else:
            hit = value is not None and str(value) == str(cond.threshold_text)
        return (not hit) if negate else hit

    if op == "in":
        options = [s.strip() for s in (cond.threshold_text or "").split(",") if s.strip()]
        return value is not None and str(value) in options

    return False


def render(cond: RuleCondition, read: FeatureRead) -> str:
    """Fill {v} / {n} in the seeded signal template with the real value."""
    template = cond.signal_template or f"{cond.feature_key} fired"
    value = read.value
    if isinstance(value, bool):
        text = "yes" if value else "no"
    elif isinstance(value, Decimal):
        text = str(value.normalize().quantize(Decimal(1)) if value == value.to_integral_value()
                   else round(value, 2))
    else:
        text = "" if value is None else str(value)
    return template.replace("{v}", text).replace("{n}", text)


def evaluate_rule(rule: Rule, reads: dict[str, FeatureRead]) -> RuleEvaluation:
    ev = RuleEvaluation(rule_id=rule.rule_id, satisfied=False, reads=dict(reads))

    groups: dict[int, list[bool]] = {}
    resolved_any = False
    degraded_any = False

    for cond in rule.conditions:
        read = reads.get(cond.feature_key) or FeatureRead(cond.feature_key, "absent")
        if read.status not in DEGRADED_STATUSES:
            resolved_any = True
        elif read.status != "unresolvable":
            # 'unresolvable' means the rule does not apply to this subject at
            # all; 'absent'/'stale' mean it does apply and the evidence is
            # missing. Only the second is a degradation worth recording.
            resolved_any = True

        if read.status == "present" and fires(cond, read.value):
            ev.fired.append(FiredCondition(cond, read, render(cond, read)))
            if cond.is_required:
                groups.setdefault(cond.condition_group, []).append(True)
            continue

        if read.is_degraded:
            if read.status != "unresolvable":
                degraded_any = True
                if cond.feature_key not in ev.degraded:
                    ev.degraded.append(cond.feature_key)
            if cond.contribution_points < 0:
                # §5: an absent mitigator cannot be allowed to authorise
                # prevention. The score goes UP because the deduction is gone,
                # and acting preventively on that number would be acting on
                # evidence we do not have.
                ev.preventive_authority = False

        if cond.is_required:
            groups.setdefault(cond.condition_group, []).append(False)

    if groups:
        results = [any(g) for g in groups.values()]
        ev.satisfied = all(results) if rule.combine.upper() == "AND" else any(results)
    else:
        # A rule with no required conditions — a mitigation rule — is satisfied
        # when it has something to say. Gating its contribution on a required
        # mitigator is what inverts a mitigator into a precondition.
        ev.satisfied = bool(ev.fired)

    if rule.is_veto:
        ev.veto_established = _veto_state(rule, reads, ev)

    return ev


def _veto_state(rule: Rule, reads: dict[str, FeatureRead], ev: RuleEvaluation) -> bool | None:
    """A veto is established by EXONERATING evidence being present.

    Deliberately independent of `satisfied`: satisfaction gates what a rule
    CONTRIBUTES, establishment gates what it PREVENTS. Tying them together makes
    one of §5's and §7's criteria unreachable whichever way the flag is set.
    """
    exonerating = [c for c in rule.conditions if c.contribution_points < 0]
    if not exonerating:
        return False

    states = [reads.get(c.feature_key) or FeatureRead(c.feature_key, "absent")
              for c in exonerating]

    if all(s.status == "unresolvable" for s in states):
        # §5's veto clause, NARROWED (judgment call 2). Read literally, an
        # enrichment outage would make T-021 unestablishable for EVERY
        # transaction and cap every preventive action on the platform at
        # monitor. The cap applies only where the veto rule's conditions
        # actually resolved to entities — an outage silences vetoes for the
        # subjects they would have applied to, not for the entire lane.
        return False

    if any(s.status in ("absent", "stale", "fanout_error") for s in states):
        return None                     # indeterminate: we cannot tell

    fired_keys = {f.condition.feature_key for f in ev.fired}
    return all(c.feature_key in fired_keys for c in exonerating)
