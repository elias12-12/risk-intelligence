"""Action precedence (§7).

Order matters and is fixed:

  0. VETO       — an established veto caps severity at `monitor`. An
                  INDETERMINATE veto caps too: if we cannot tell whether the
                  exonerating evidence holds, we do not get to act as if it
                  does not.
  1. AUTHORITY  — a non-veto rule that is satisfied AND scores at or above its
                  own review_threshold.
  2. SEVERITY   — the most severe authorised action wins, ties broken by
                  rule_id ascending, so a shuffled rule order cannot change the
                  outcome.
  3. PREVENTION — a preventive action must additionally clear its own
                  prevent_threshold and hold preventive authority (§5). If it
                  cannot, it DEMOTES to the most severe non-preventive action
                  rather than failing open into `allow`.
  4. CAP        — final = min(chosen severity, veto cap).

The veto pass runs FIRST and is applied LAST, because a cap that is computed
after prevention would let a preventive action fire before being capped.
"""
from __future__ import annotations

from decimal import Decimal

from ..types import ActionOutcome, Rule, RuleScore, Signal

MONITOR = "monitor"
ALLOW = "allow"

# The two reason codes this module emits, and the direction it emits them with.
#
# Named rather than inline because they are the answer to a question asked
# elsewhere: no `rule_conditions` row cites either of these, so a catalog trying
# to say whether a reason code argues for or against a customer cannot find them
# by looking at what rules price. `contract/catalog.py` reads these constants for
# exactly that, and a third reason code emitted here has to be added to this
# tuple to be classifiable — which is the intended amount of friction.
VETO_DIRECTION = "veto"
VETO_REASON_CODE = "VETO_APPLIED"
DEGRADED_REASON_CODE = "DEGRADED_EVIDENCE"
VETO_EMITTED_REASON_CODES: tuple[str, ...] = (VETO_REASON_CODE, DEGRADED_REASON_CODE)


def decide(rule_scores: list[RuleScore], rules: dict[str, Rule],
           severity: dict[str, tuple[int, bool]]) -> ActionOutcome:
    veto_cap: int | None = None
    vetoed_by: str | None = None
    veto_signals: list[Signal] = []

    # ---- 0. veto pass -------------------------------------------------
    for rs in sorted(rule_scores, key=lambda r: r.rule_id):
        rule = rules[rs.rule_id]
        if not rule.is_veto:
            continue
        established = rs.evaluation.veto_established
        if established is True:
            veto_cap = severity[MONITOR][0]
            vetoed_by = rule.rule_id
            veto_signals.append(Signal(
                feature_key=None, contribution=Decimal(0), direction=VETO_DIRECTION,
                human_text=(rule.recommended_action_text
                            or f"{rule.rule_id} established exonerating evidence"),
                reason_code=VETO_REASON_CODE, source_rule_id=rule.rule_id,
                asserted_by_rules=[rule.rule_id]))
        elif established is None:
            veto_cap = severity[MONITOR][0]
            veto_signals.append(Signal(
                feature_key=None, contribution=Decimal(0), direction=VETO_DIRECTION,
                human_text=(f"{rule.rule_id} could not be established: "
                            f"{', '.join(rs.evaluation.degraded) or 'evidence missing'}. "
                            f"Severity is capped until the evidence is available."),
                reason_code=DEGRADED_REASON_CODE, source_rule_id=rule.rule_id,
                asserted_by_rules=[rule.rule_id]))

    # ---- 1. authority --------------------------------------------------
    authorised = [
        rs for rs in rule_scores
        if not rules[rs.rule_id].is_veto
        and rs.evaluation.satisfied
        and rs.score >= Decimal(rules[rs.rule_id].review_threshold or 0)
    ]

    if not authorised:
        action = ALLOW
        chosen: RuleScore | None = None
        prevent_met: bool | None = None
    else:
        # ---- 2. severity, deterministic under shuffle ------------------
        chosen = max(authorised,
                     key=lambda rs: (severity[rules[rs.rule_id].action][0], _neg(rs.rule_id)))
        rule = rules[chosen.rule_id]
        action = rule.action
        prevent_met = None

        # ---- 3. prevention --------------------------------------------
        if severity[action][1]:
            prevent_met = bool(
                rule.prevent_threshold is not None
                and chosen.score >= Decimal(rule.prevent_threshold)
                and chosen.evaluation.preventive_authority
            )
            if not prevent_met:
                action = _highest_non_preventive(authorised, rules, severity)

    # ---- 4. cap ---------------------------------------------------------
    if veto_cap is not None and severity[action][0] > veto_cap:
        action = _action_at(severity, veto_cap)

    return ActionOutcome(
        action=action,
        action_source_rule=chosen.rule_id if chosen else None,
        vetoed_by=vetoed_by,
        prevent_threshold_met=prevent_met,
        authorised_rules=sorted(rs.rule_id for rs in authorised),
        veto_signals=veto_signals,
    )


class _neg:
    """Sort helper: ascending rule_id inside a descending max()."""

    __slots__ = ("value",)

    def __init__(self, value: str):
        self.value = value

    def __lt__(self, other: "_neg") -> bool:
        return self.value > other.value

    def __eq__(self, other: object) -> bool:  # pragma: no cover
        return isinstance(other, _neg) and self.value == other.value


def _highest_non_preventive(authorised, rules, severity) -> str:
    options = [rules[rs.rule_id].action for rs in authorised
               if not severity[rules[rs.rule_id].action][1]]
    if not options:
        # Every authorised rule wanted to prevent and none may. The most severe
        # thing left that does not touch the customer's money is `alert`.
        return "alert"
    return max(options, key=lambda a: severity[a][0])


def _action_at(severity: dict[str, tuple[int, bool]], level: int) -> str:
    for action, (sev, _) in severity.items():
        if sev == level:
            return action
    return MONITOR  # pragma: no cover
