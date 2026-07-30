"""Per-rule scoring.

Computed BEFORE dedup, per §6: a rule's authority is judged on its own evidence,
not on what survived consolidation with other rules. Otherwise two rules citing
the same feature would each lose half their case to the other.

A rule whose required conditions are unsatisfied contributes NOTHING. That is
the fix for the partial-firing bug: today every condition sits in condition_group
1, they all OR together, and one condition out of four produces a score.
"""
from __future__ import annotations

from decimal import Decimal

from ..types import Rule, RuleEvaluation, RuleScore


def score_rule(rule: Rule, evaluation: RuleEvaluation) -> RuleScore:
    total = Decimal(0)
    if evaluation.satisfied:
        for f in evaluation.fired:
            total += Decimal(f.condition.contribution_points)
    return RuleScore(rule_id=rule.rule_id, score=total, evaluation=evaluation)
