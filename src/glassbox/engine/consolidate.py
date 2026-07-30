"""Signal consolidation (§6).

Three rules can cite the same feature. Left alone, the subject's score counts
that evidence three times and the alert shows it three times — the score stops
being explainable, which is the one property the whole system exists to have.

Dedup is keyed on (feature_key, DIRECTION), not on feature_key alone, so an
aggravating and a mitigating claim on the same feature both survive. Collapsing
them would silently delete one side of a disagreement, and a disagreement is
information an analyst needs to see.

Only SATISFIED rules contribute. An unsatisfied rule has no case to make.
"""
from __future__ import annotations

from decimal import Decimal

from ..types import RuleScore, Signal, SignalPool, jsonable


def consolidate(rule_scores: list[RuleScore]) -> SignalPool:
    buckets: dict[tuple[str, str], Signal] = {}

    for rs in sorted(rule_scores, key=lambda r: r.rule_id):   # order-invariant
        if not rs.evaluation.satisfied:
            continue
        for fired in rs.evaluation.fired:
            cond = fired.condition
            direction = "aggravating" if cond.contribution_points >= 0 else "mitigating"
            key = (cond.feature_key, direction)
            contribution = Decimal(cond.contribution_points)

            existing = buckets.get(key)
            if existing is None:
                buckets[key] = Signal(
                    feature_key=cond.feature_key,
                    contribution=contribution,
                    direction=direction,
                    human_text=fired.human_text,
                    reason_code=cond.reason_code,
                    source_rule_id=cond.rule_id,
                    asserted_by_rules=[cond.rule_id],
                    feature_value=jsonable(fired.read.value),
                    value_as_of=fired.read.as_of,
                    value_computed_at=fired.read.computed_at,
                )
                continue

            # Every claimant is recorded even when its number loses, so the
            # alert can say WHO asserted a signal, not just that someone did.
            if cond.rule_id not in existing.asserted_by_rules:
                existing.asserted_by_rules.append(cond.rule_id)
            if abs(contribution) > abs(existing.contribution):
                existing.contribution = contribution
                existing.human_text = fired.human_text
                existing.reason_code = cond.reason_code
                existing.source_rule_id = cond.rule_id
                existing.feature_value = jsonable(fired.read.value)
                existing.value_as_of = fired.read.as_of
                existing.value_computed_at = fired.read.computed_at

    # A mitigator is a DEDUCTION FROM AN ACCUSATION. When the deductions consume
    # the accusation there is no accusation left, and emitting the remainder
    # anyway produces a negative risk score — which on an additive scale means
    # "safer than nothing", a claim the model cannot support. Left in, every
    # traveller who bought a flight and paid by chip-and-PIN scored -19.
    #
    # The test is the POOL'S SUM, not the mere presence of an aggravator. Week 2
    # wrote it as "no aggravating signal at all", which is the same rule for the
    # mitigator-only case and silently weaker for a mixed one: any pool whose
    # mitigators outweigh its aggravators still emitted a negative score. Nothing
    # on the shipped fixtures reached that until 0026 repriced
    # country_is_new_for_customer to +12, at which point TXN-48251 became
    # 12 - 9 - 6 - 4 = -7 — one decision in 9,923, and unexplainable.
    #
    # Dropping the pool keeps sum(signals) == score exact, which clamping would
    # not: clamping the score at zero while still showing the signals breaks
    # §12's invariant, and that invariant is the product.
    survivors = list(buckets.values())
    if sum((s.contribution for s in survivors), Decimal(0)) <= 0:
        survivors = []

    signals = sorted(survivors,
                     key=lambda s: (-abs(s.contribution), s.feature_key, s.direction))
    for rank, signal in enumerate(signals, start=1):
        signal.rank = rank
        signal.asserted_by_rules.sort()

    return SignalPool(signals=signals,
                      subject_score=sum((s.contribution for s in signals), Decimal(0)))
