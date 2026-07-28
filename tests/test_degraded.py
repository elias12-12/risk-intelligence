"""§5 — missing evidence degrades, it does not silently score."""
from __future__ import annotations

from decimal import Decimal

from conftest import request_for
from glassbox.db import fetch_all
from glassbox.engine.evaluation import EngineContext, evaluate


def _rule(result, rule_id):
    return next(rs for rs in result.rule_scores if rs.rule_id == rule_id)


def test_every_mitigator_defaults_to_null(conn):
    """THE catalog invariant.

    If an absent mitigator defaulted to false, the runner would write false,
    the condition would not fire, it would contribute 0 — and no degradation
    would be recorded. §5's entire policy becomes unreachable and T-021's
    acceptance test passes for the wrong reason.
    """
    offenders = fetch_all(
        conn,
        """
        SELECT DISTINCT c.feature_key, fc.default_when_absent
          FROM rule_conditions c
          JOIN feature_catalog fc USING (feature_key)
         WHERE c.contribution_points < 0
           AND fc.default_when_absent IS NOT NULL
        """,
    )
    assert offenders == [], (
        "a feature cited by a negative contribution must have no default: "
        f"{offenders}"
    )


def test_removing_a_mitigator_raises_the_score_and_caps_the_severity(conn, ctx):
    """The §5 criterion, stated by the doc as one criterion and tested as one.

    Deleting recent_travel_purchase makes T-021's -9 disappear, so the score
    goes UP — which is the correct arithmetic and the wrong action. The veto
    becomes indeterminate, severity is capped at monitor, and the feature is
    named in degraded_features so the reason is visible rather than inferred.
    """
    request = request_for(conn, ctx, "inline_sync", "transaction", "TXN-48300")
    before = evaluate(conn, request, ctx)
    assert before.pool.subject_score == 68
    assert before.outcome.action == "monitor"
    assert before.outcome.vetoed_by == "T-021"
    assert before.degraded_features == []

    with conn.cursor() as cur:
        cur.execute("DELETE FROM feature_values WHERE feature_key='recent_travel_purchase'")

    after = evaluate(conn, request, EngineContext.load(conn))
    assert after.pool.subject_score > before.pool.subject_score
    assert after.pool.subject_score == 77          # 87 - 6 - 4; the -9 is gone
    assert after.outcome.action == "monitor", "severity stays capped while we cannot tell"
    assert after.outcome.vetoed_by is None, "an indeterminate veto names no rule"
    assert "recent_travel_purchase" in after.degraded_features
    assert any(s.reason_code == "DEGRADED_EVIDENCE" for s in after.outcome.veto_signals)


def test_the_same_removal_raises_the_travel_case_too(conn, ctx):
    request = request_for(conn, ctx, "inline_sync", "transaction", "TXN-48251")
    assert evaluate(conn, request, ctx).pool.subject_score == 31

    with conn.cursor() as cur:
        cur.execute("DELETE FROM feature_values WHERE feature_key='recent_travel_purchase'")
    after = evaluate(conn, request, EngineContext.load(conn))
    assert after.pool.subject_score == 40          # 50 - 6 - 4
    assert "recent_travel_purchase" in after.degraded_features


def test_a_missing_mitigator_strips_preventive_authority(conn, ctx):
    """The score rose because a deduction vanished. Acting preventively on that
    number would be acting on evidence we do not have."""
    request = request_for(conn, ctx, "inline_sync", "transaction", "TXN-48300")
    with conn.cursor() as cur:
        cur.execute("DELETE FROM feature_values WHERE feature_key='recent_travel_purchase'")
    after = evaluate(conn, request, EngineContext.load(conn))
    assert _rule(after, "T-021").evaluation.preventive_authority is False


def test_an_absent_aggravator_contributes_zero_and_is_recorded(conn, ctx):
    request = request_for(conn, ctx, "inline_sync", "transaction", "TXN-48291")
    assert _rule(evaluate(conn, request, ctx), "R-114").score == 87

    with conn.cursor() as cur:
        cur.execute("DELETE FROM feature_values WHERE feature_key='session_geo_jump_km'")
    after = evaluate(conn, request, EngineContext.load(conn))
    r114 = _rule(after, "R-114")
    assert not r114.evaluation.satisfied
    assert r114.score == 0
    assert "session_geo_jump_km" in after.degraded_features
    assert r114.evaluation.preventive_authority is True, (
        "an absent AGGRAVATOR does not strip preventive authority — only an "
        "absent mitigator does, because only that inflates the score"
    )


def test_an_unresolvable_veto_does_not_cap_the_whole_lane(conn, ctx):
    """§5's veto clause, narrowed (judgment call 2).

    Read literally, one enrichment outage would make T-021 unestablishable for
    every transaction and cap every preventive action on the platform at
    monitor. An outage must silence vetoes for the subjects they would have
    applied to, not for the entire lane.
    """
    request = request_for(conn, ctx, "inline_sync", "transaction", "TXN-48291")
    with conn.cursor() as cur:
        # No route from a transaction to this entity type: every T-021
        # mitigator becomes unresolvable rather than merely absent.
        cur.execute("UPDATE feature_catalog SET entity_type='merchant', "
                    "resolution_path='self' WHERE feature_key IN "
                    "('recent_travel_purchase','amount_vs_baseline_z','entry_mode_chip_pin')")
    result = evaluate(conn, request, EngineContext.load(conn))
    assert _rule(result, "T-021").evaluation.veto_established is False
    assert result.outcome.action == "challenge", "R-114 is unaffected by an inapplicable veto"


def test_degraded_features_reach_the_stored_decision(conn):
    """It has to be visible on the record, not just inside the engine."""
    rows = fetch_all(
        conn,
        "SELECT degraded_features FROM decisions "
        "WHERE degraded_features IS NOT NULL AND cardinality(degraded_features) > 0 LIMIT 5",
    )
    assert rows, "the population should contain at least some degraded evidence"
    assert all(isinstance(r["degraded_features"], list) for r in rows)
