"""§7 — veto, authority, severity, prevention, cap."""
from __future__ import annotations

import random

from conftest import request_for
from glassbox.db import fetch_one
from glassbox.engine.evaluation import EngineContext, evaluate
from glassbox.engine.precedence import decide


def test_the_veto_fixture_caps_a_high_score_at_monitor(conn, ctx):
    """§7's acceptance criterion, made satisfiable.

    As written it says "a veto rule scoring 31 caps a score of 87" — but the 31
    is T-021 on TXN-48251 and the 87 is R-114 on TXN-48291, DIFFERENT subjects.
    A veto caps severity within one (subject, lane, evaluation) and must not
    reach across to another transaction, so the criterion could not pass
    against an engine that is right. TXN-48300 is the fixture that makes it
    pass honestly: one subject, both rules, R-114 wanting to challenge and
    T-021 holding it to monitor.
    """
    result = evaluate(conn,
                      request_for(conn, ctx, "inline_sync", "transaction", "TXN-48300"),
                      ctx)
    r114 = next(rs for rs in result.rule_scores if rs.rule_id == "R-114")

    assert r114.score == 87
    assert ctx.rules["R-114"].action == "challenge"
    assert result.outcome.action == "monitor"
    assert result.outcome.action_source_rule == "R-114"
    assert result.outcome.vetoed_by == "T-021"
    assert any(s.direction == "veto" and s.reason_code == "VETO_APPLIED"
               for s in result.outcome.veto_signals)


def test_the_veto_signal_reaches_the_stored_alert(conn):
    row = fetch_one(
        conn,
        """
        SELECT s.direction, s.reason_code, s.contribution
          FROM alerts a JOIN alert_signals s ON s.alert_id = a.alert_id
         WHERE a.subject_id = 'TXN-48300' AND s.direction = 'veto'
        """,
    )
    assert row is not None
    assert row["reason_code"] == "VETO_APPLIED"
    assert row["contribution"] == 0, "a veto explains, it does not score"


def test_an_inapplicable_veto_does_not_cap(conn, ctx):
    """TXN-48291's mitigators are all PRESENT and all FALSE — the evidence was
    checked and does not exonerate. That must not cap anything."""
    result = evaluate(conn,
                      request_for(conn, ctx, "inline_sync", "transaction", "TXN-48291"),
                      ctx)
    t021 = next(rs for rs in result.rule_scores if rs.rule_id == "T-021")
    assert t021.evaluation.veto_established is False
    assert result.outcome.action == "challenge"
    assert result.outcome.vetoed_by is None


def test_a_preventive_action_demotes_below_its_prevent_threshold(conn, ctx):
    """Demotion is demonstrated by raising a threshold inside a rolled-back
    transaction rather than by seeding one that breaks a signed-off demo
    outcome — seeding L-203 at 70 against its 64 would silently turn the mule
    ring from hold into alert."""
    request = request_for(conn, ctx, "inline_sync", "transaction", "TXN-48291")
    assert evaluate(conn, request, ctx).outcome.action == "challenge"

    with conn.cursor() as cur:
        cur.execute("UPDATE rule_definitions SET prevent_threshold = 95 WHERE rule_id='R-114'")
    demoted = evaluate(conn, request, EngineContext.load(conn))
    assert demoted.outcome.action == "alert", "87 no longer clears 95, so it must not prevent"
    assert demoted.outcome.prevent_threshold_met is False
    assert demoted.outcome.action_source_rule == "R-114", (
        "demotion changes what we DO, not who decided it"
    )


def test_a_degraded_mitigator_blocks_prevention(conn, ctx):
    """§5's rule reaching §7: the score rose because a deduction vanished, so
    the preventive rung is not available even though the number clears the
    threshold."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO rule_conditions
                (rule_id, condition_group, feature_key, operator, threshold_text,
                 contribution_points, reason_code, signal_template, is_required)
            VALUES ('R-114', 9, 'recent_travel_purchase', '=', 'true', -1,
                    'TRAVEL_EXPLAINED', 'Travel on file', FALSE)
            """
        )
        with_mitigator = EngineContext.load(conn)
        request = request_for(conn, with_mitigator, "inline_sync", "transaction", "TXN-48291")
        assert evaluate(conn, request, with_mitigator).outcome.action == "challenge"

        cur.execute("DELETE FROM feature_values WHERE feature_key='recent_travel_purchase'")

    blocked = evaluate(conn, request, EngineContext.load(conn))
    r114 = next(rs for rs in blocked.rule_scores if rs.rule_id == "R-114")
    assert r114.evaluation.preventive_authority is False
    assert blocked.outcome.prevent_threshold_met is False
    assert not ctx.severity[blocked.outcome.action][1], "the action must not be preventive"

    # Two mechanisms stacked here: prevention was blocked (challenge -> alert)
    # AND the same missing feature made T-021's veto indeterminate (alert ->
    # monitor). Isolate the first by taking the veto out of play.
    assert blocked.outcome.action == "monitor"
    with conn.cursor() as cur:
        cur.execute("UPDATE rule_definitions SET is_veto = FALSE WHERE rule_id='T-021'")
    isolated = evaluate(conn, request, EngineContext.load(conn))
    assert isolated.outcome.prevent_threshold_met is False
    assert isolated.outcome.action == "alert", (
        "with no veto in play, a blocked prevention demotes to the most severe "
        "non-preventive rung rather than failing open into allow"
    )


def test_severity_ties_resolve_identically_across_shuffles(conn, ctx):
    """Two rules at the same severity must resolve the same way every time.
    Ties break by rule_id ascending — deterministic, per §7's own criterion."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO rule_definitions (rule_id, name, subject_type, execution_mode,
                action, review_threshold, prevent_threshold, created_by)
            VALUES ('A-001', 'Tie-breaker twin of R-114', 'transaction', 'inline_sync',
                    'challenge', 70, 80, 'test')
            """
        )
        cur.execute(
            """
            INSERT INTO rule_conditions (rule_id, condition_group, feature_key, operator,
                threshold_num, contribution_points, reason_code, signal_template)
            SELECT 'A-001', condition_group, feature_key, operator, threshold_num,
                   contribution_points, reason_code, signal_template
              FROM rule_conditions WHERE rule_id = 'R-114' AND threshold_num IS NOT NULL
            """
        )
        cur.execute(
            """
            INSERT INTO rule_conditions (rule_id, condition_group, feature_key, operator,
                threshold_text, contribution_points, reason_code, signal_template)
            SELECT 'A-001', condition_group, feature_key, operator, threshold_text,
                   contribution_points, reason_code, signal_template
              FROM rule_conditions WHERE rule_id = 'R-114' AND threshold_text IS NOT NULL
            """
        )

    tied = EngineContext.load(conn)
    request = request_for(conn, tied, "inline_sync", "transaction", "TXN-48291")
    baseline = evaluate(conn, request, tied)
    assert {"A-001", "R-114"} <= set(baseline.outcome.authorised_rules)
    assert baseline.outcome.action_source_rule == "A-001", "ascending rule_id wins the tie"

    for seed in range(20):
        shuffled = list(baseline.rule_scores)
        random.Random(seed).shuffle(shuffled)
        outcome = decide(shuffled, tied.rules, tied.severity)
        assert outcome.action == baseline.outcome.action
        assert outcome.action_source_rule == baseline.outcome.action_source_rule


def test_the_most_severe_authorised_action_wins(conn, ctx):
    """L-203 holds (severity 4) rather than merely alerting."""
    result = evaluate(conn, request_for(conn, ctx, "async", "network", "RING-1187"), ctx)
    assert result.outcome.action == "hold"
    assert result.outcome.action_source_rule == "L-203"
    assert result.outcome.prevent_threshold_met is True


def test_no_authority_means_allow(conn, ctx):
    """T-021 scores 31 against a review line of 70. An alert is raised iff a
    rule had AUTHORITY — not because a score landed in a band, which would put
    this case in the queue and contradict its entire purpose."""
    result = evaluate(conn,
                      request_for(conn, ctx, "inline_sync", "transaction", "TXN-48251"),
                      ctx)
    assert result.pool.subject_score == 31
    assert result.outcome.authorised_rules == []
    assert result.outcome.action == "allow"
    assert fetch_one(conn, "SELECT 1 AS x FROM alerts WHERE subject_id='TXN-48251'") is None
