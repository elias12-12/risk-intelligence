"""§14 — a new pattern is INSERTs, and a hook proves it.

The README's claim is that adding a detection pattern costs rows, not code. A
paragraph cannot establish that. This test wires a psycopg hook that records
every statement executed inside the test body and fails if any of them is DDL —
so the claim is checked rather than asserted.

The honest boundary, stated here and in the README: this covers a pattern whose
feature already exists in the catalog with a reducer that already exists. A
pattern needing a NEW reducer is a data-engineering ticket, and a pattern
needing a subject type beyond ref_subject_type's seven is a code change.

The `no_ddl` hook this uses now lives in conftest.py, shared with
test_extension_refundabuse.py — §14's acceptance is BOTH patterns, and two
copies of the DDL regex would be two definitions of "schema change".
"""
from __future__ import annotations

from glassbox import config
from glassbox.db import fetch_all, fetch_value
from glassbox.engine.evaluation import EngineContext, run_lane


def test_card_testing_detects_end_to_end_via_insert_only(conn, no_ddl):
    # ---- the entire extension: two INSERTs ------------------------------
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO rule_definitions
                (rule_id, name, description, subject_type, execution_mode, action,
                 review_threshold, prevent_threshold, recommended_action_text,
                 clear_text, created_by)
            VALUES ('C-301', 'Card-testing burst at a merchant',
                    'Small-value declines probing card validity at one acceptor',
                    'merchant', 'async', 'alert', 55, NULL,
                    'Suspend the acceptor pending review and re-score every card touched.',
                    'A normal decline rate over the next hour clears the burst.',
                    'test')
            """
        )
        cur.execute(
            """
            INSERT INTO rule_conditions
                (rule_id, condition_group, feature_key, operator, threshold_num,
                 contribution_points, reason_code, signal_template)
            VALUES ('C-301', 1, 'merchant_decline_burst', '>=', 10, 60,
                    'CARD_TESTING',
                    '{v} declined authorizations at this merchant in 10 minutes')
            """
        )
    # ---------------------------------------------------------------------

    run_lane(conn, "async", config.reference_now(), run_id="cardtest",
             ctx=EngineContext.load(conn))

    decision = fetch_all(
        conn,
        """
        SELECT subject_id, score, band, action_taken, action_source_rule
          FROM decisions
         WHERE subject_type = 'merchant' AND 'C-301' = ANY(rules_fired)
           AND evaluation_id LIKE 'ev_cardtest_%'
        """,
    )
    assert [d["subject_id"] for d in decision] == ["MER-TEST"], (
        "the planted burst must be the only merchant that fires"
    )
    assert decision[0]["score"] == 60
    assert decision[0]["action_taken"] == "alert"
    assert decision[0]["action_source_rule"] == "C-301"


def test_the_new_rule_produces_an_explainable_alert(conn, no_ddl):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO rule_definitions (rule_id, name, subject_type, execution_mode,
                action, review_threshold, created_by)
            VALUES ('C-301', 'Card-testing burst', 'merchant', 'async', 'alert', 55, 'test')
            """
        )
        cur.execute(
            """
            INSERT INTO rule_conditions (rule_id, condition_group, feature_key, operator,
                threshold_num, contribution_points, reason_code, signal_template)
            VALUES ('C-301', 1, 'merchant_decline_burst', '>=', 10, 60, 'CARD_TESTING',
                    '{v} declined authorizations at this merchant in 10 minutes')
            """
        )

    run_lane(conn, "async", config.reference_now(), run_id="cardtest2",
             ctx=EngineContext.load(conn))

    from glassbox.contract.read import get_alert
    alert_id = fetch_value(
        conn,
        "SELECT a.alert_id FROM alerts a JOIN decisions d ON d.decision_id = a.decision_id "
        "WHERE a.subject_id = 'MER-TEST' AND d.evaluation_id LIKE 'ev_cardtest2_%'",
    )
    alert = get_alert(conn, alert_id)
    assert alert.score == 60
    assert len(alert.signals) == 1
    assert "22 declined authorizations" in alert.signals[0].human_text
    assert alert.signals[0].reason_code == "CARD_TESTING"
    assert alert.evidence.feature_version_set == {"merchant_decline_burst": 1}


def test_no_other_merchant_is_anywhere_near_the_threshold(conn):
    """The fixture's discrimination, not just its detection."""
    worst = fetch_value(
        conn,
        """
        SELECT max(value_num) FROM feature_values
         WHERE feature_key = 'merchant_decline_burst' AND entity_id <> 'MER-TEST'
        """,
    )
    assert worst <= 2, f"another merchant reached {worst} declines in a 10-minute window"


def test_the_extension_needed_no_new_reducer(conn):
    """merchant_decline_burst is a plain `count` over a filtered window — the
    case where §14's claim is strongest, and the reason the claim has to be
    stated with its limits attached."""
    spec = fetch_all(
        conn,
        "SELECT aggregation, source_relation, filter_predicate FROM feature_catalog "
        "WHERE feature_key = 'merchant_decline_burst'",
    )[0]
    assert spec["aggregation"] == "count"
    assert spec["source_relation"] == "transactions"
    assert spec["filter_predicate"] == {"op": "eq", "col": "auth_result", "value": "declined"}
