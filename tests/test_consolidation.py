"""§6 — one decision per (subject, lane, evaluation); signals that add up."""
from __future__ import annotations

import random
from decimal import Decimal

from conftest import request_for
from glassbox import config
from glassbox.db import fetch_all, fetch_value
from glassbox.engine.consolidate import consolidate
from glassbox.engine.evaluation import EngineContext, evaluate, run_lane


def test_one_decision_per_subject_per_lane_per_evaluation(conn):
    duplicates = fetch_all(
        conn,
        """
        SELECT subject_type, subject_id, execution_mode, evaluation_id, count(*) AS n
          FROM decisions
         GROUP BY 1, 2, 3, 4 HAVING count(*) > 1
        """,
    )
    assert duplicates == []


def test_a_multi_rule_subject_yields_one_decision_and_one_alert(conn):
    """TXN-48300 is scored by R-114 and T-021. The old scorer produced a
    decision per rule and then joined decisions to alerts on (subject_id,
    subject_type) alone — a cartesian product for exactly this subject."""
    decisions = fetch_all(
        conn,
        "SELECT decision_id, rules_fired FROM decisions "
        "WHERE subject_id='TXN-48300' AND execution_mode='inline_sync'",
    )
    assert len(decisions) == 1
    assert sorted(decisions[0]["rules_fired"]) == ["R-114", "T-021"]
    assert fetch_value(
        conn, "SELECT count(*) FROM alerts WHERE subject_id='TXN-48300'") == 1


def test_signals_sum_to_the_score_on_every_alert(conn):
    failures = fetch_all(conn, "SELECT * FROM v_alert_invariants WHERE NOT sum_ok")
    assert failures == []


def test_opposing_signals_on_one_feature_both_survive(conn, ctx):
    """Dedup is keyed on (feature_key, DIRECTION). Collapsing on feature_key
    alone would silently delete one side of a disagreement, and a disagreement
    is information an analyst needs."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO rule_conditions
                (rule_id, condition_group, feature_key, operator, threshold_text,
                 contribution_points, reason_code, signal_template, is_required)
            VALUES ('R-114', 9, 'entry_mode_chip_pin', '=', 'true', 5,
                    'CARD_PRESENT', 'Card-present on a card under active testing',
                    FALSE)
            """
        )
    result = evaluate(conn,
                      request_for(conn, EngineContext.load(conn), "inline_sync",
                                  "transaction", "TXN-48300"),
                      EngineContext.load(conn))

    chip = [s for s in result.pool.signals if s.feature_key == "entry_mode_chip_pin"]
    assert {s.direction for s in chip} == {"aggravating", "mitigating"}
    assert {s.contribution for s in chip} == {Decimal(5), Decimal(-4)}
    assert result.pool.subject_score == sum(s.contribution for s in result.pool.signals)


def test_the_larger_claim_wins_but_every_claimant_is_recorded(conn, ctx):
    """Two rules citing one feature in the same direction collapse to the
    biggest number — and the loser is still named, so the alert can say WHO
    asserted a signal rather than just that someone did."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO rule_conditions
                (rule_id, condition_group, feature_key, operator, threshold_num,
                 contribution_points, reason_code, signal_template, is_required)
            VALUES ('T-021', 9, 'card_cnp_count', '>=', 5, 12,
                    'VELOCITY_SPIKE', 'Velocity spike seen from the travel rule', FALSE)
            """
        )
    fresh = EngineContext.load(conn)
    result = evaluate(conn,
                      request_for(conn, fresh, "inline_sync", "transaction", "TXN-48300"),
                      fresh)
    signal = next(s for s in result.pool.signals if s.feature_key == "card_cnp_count")
    assert signal.contribution == 34                      # R-114's larger claim
    assert signal.asserted_by_rules == ["R-114", "T-021"]  # both are on the record


def test_consolidation_is_invariant_under_rule_order(conn, ctx):
    """A shuffled rule order must not change a score. Determinism is the
    difference between an explanation and a coincidence."""
    request = request_for(conn, ctx, "inline_sync", "transaction", "TXN-48300")
    baseline = evaluate(conn, request, ctx)

    for seed in range(20):
        shuffled = list(baseline.rule_scores)
        random.Random(seed).shuffle(shuffled)
        pool = consolidate(shuffled)
        assert pool.subject_score == baseline.pool.subject_score
        assert [(s.feature_key, s.direction, s.contribution, s.rank) for s in pool.signals] == \
               [(s.feature_key, s.direction, s.contribution, s.rank)
                for s in baseline.pool.signals]


def test_an_unsatisfied_rule_contributes_nothing(conn, ctx):
    """No partial scores. Under the seeded condition_group = 1 every condition
    OR'd into one group, so one condition out of four produced a score."""
    result = evaluate(conn,
                      request_for(conn, ctx, "inline_sync", "transaction", "TXN-48251"),
                      ctx)
    r114 = next(rs for rs in result.rule_scores if rs.rule_id == "R-114")
    assert not r114.evaluation.satisfied and r114.score == 0
    assert not any(s.source_rule_id == "R-114" for s in result.pool.signals)


def test_mitigators_never_produce_a_negative_score(conn):
    """A mitigator is a deduction from an accusation. When the deductions consume
    the accusation there is nothing left to publish, and a negative risk score is
    not a claim the additive model can support."""
    assert fetch_value(conn, "SELECT count(*) FROM decisions WHERE score < 0") == 0


def test_the_drop_is_on_the_pool_sum_not_the_presence_of_an_aggravator(conn, ctx):
    """The generalisation 0026 forced, pinned so it is not narrowed back.

    Week 2 wrote the guard as "no aggravating signal at all", which is correct
    for a mitigator-only pool and silently weaker for a mixed one. TXN-48251 has
    an aggravator — country_is_new_for_customer at +12 — and three mitigators
    worth -19, so under the old guard it published -7. Under the current guard
    the pool is dropped whole, which keeps sum(signals) == score exact where
    clamping the score to zero would have broken it.
    """
    result = evaluate(conn,
                      request_for(conn, ctx, "inline_sync", "transaction", "TXN-48251"),
                      ctx)
    t021 = next(rs for rs in result.rule_scores if rs.rule_id == "T-021")
    assert t021.evaluation.satisfied, "the rule fired; it is the POOL that is dropped"
    assert any(c.condition.contribution_points > 0 for c in t021.evaluation.fired), (
        "an aggravating condition did fire — the old guard would have kept the pool")
    assert t021.score == -7                        # 12 - 9 - 6 - 4
    assert result.pool.signals == []
    assert result.pool.subject_score == 0
    assert sum((s.contribution for s in result.pool.signals), Decimal(0)) == \
        result.pool.subject_score


def test_rerunning_a_lane_adds_a_new_evaluation_not_a_duplicate(conn, ctx):
    before = fetch_value(conn, "SELECT count(*) FROM decisions WHERE subject_id='TXN-48291'")
    run_lane(conn, "inline_sync", config.reference_now(), run_id="second",
             subject_ids=["TXN-48291"], ctx=ctx)
    after = fetch_all(
        conn,
        "SELECT evaluation_id, score FROM decisions WHERE subject_id='TXN-48291' "
        "ORDER BY decision_id",
    )
    assert len(after) == before + 1
    assert len({r["evaluation_id"] for r in after}) == len(after)
    assert len({r["score"] for r in after}) == 1, "the same evidence must score the same"
