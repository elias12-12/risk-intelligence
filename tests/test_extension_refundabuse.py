"""§14's second pattern — refund abuse, added by INSERT, with the same hook.

`architecture.md` §14 calls "a new fraud pattern costs inserts, not code" the
central claim of the whole design, and its acceptance is BOTH named patterns
detecting end to end via INSERT only. Card testing has been a real test since
Week 2; this is the other half, and it is deliberately less like the first than
it had to be:

  * the subject is a CUSTOMER — every shipped fixture is a transaction, account
    or network subject, and the card-testing extension is a merchant. This is the
    `_dimension_subject` planner Week 2 added on the argument that §14's claim is
    false if the planner cannot reach a subject type the schema already defines;
  * it needs TWO conditions in two groups with combine='AND', so it exercises
    AND-across-groups rather than one predicate;
  * and its feature had to be built out of `count` and `sum` rather than the
    ratio the pattern actually wants, because `ratio` is deliberately
    unimplemented. That is the claim's limit showing itself honestly rather than
    being engineered around — see the header of db/seeds/0028.

`no_ddl` lives in conftest.py and is shared with test_extension_cardtesting.py.
"""
from __future__ import annotations

from decimal import Decimal

from glassbox import config
from glassbox.db import fetch_all, fetch_one, fetch_value
from glassbox.engine.evaluation import EngineContext, run_lane

# The thresholds sit in the empty region between the two populations, chosen the
# same way 0027's band cutoffs were. Over the 30 days before the reference
# instant: one customer at 12 refunds / $1,404, the next at 6 / $628.
MIN_REFUNDS = 9
MIN_VALUE = 1000


def _author_the_rule(conn) -> None:
    """The entire extension: two INSERTs. No DDL, no Python, no reducer."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO rule_definitions
                (rule_id, name, description, subject_type, execution_mode, action,
                 review_threshold, prevent_threshold, combine,
                 recommended_action_text, clear_text, created_by)
            VALUES ('RF-401', 'Refund abuse',
                    'Refund volume and value abnormal against purchase history',
                    'customer', 'async', 'alert', 55, NULL, 'AND',
                    'Review the refund history before authorising further returns; '
                    'check whether the goods were received.',
                    'A normal return rate over the next 30 days clears it, as does '
                    'evidence the goods went back.',
                    'test')
            """
        )
        cur.execute(
            """
            INSERT INTO rule_conditions
                (rule_id, condition_group, feature_key, operator, threshold_num,
                 contribution_points, reason_code, signal_template)
            VALUES
             ('RF-401', 1, 'customer_refund_count_30d',  '>=', %s, 35,
              'REFUND_ABUSE', '{v} refunds against this customer in 30 days'),
             ('RF-401', 2, 'customer_refund_amount_30d', '>=', %s, 25,
              'REFUND_ABUSE', '{v} refunded to this customer in 30 days')
            """,
            (MIN_REFUNDS, MIN_VALUE),
        )


def test_refund_abuse_detects_end_to_end_via_insert_only(conn, no_ddl):
    _author_the_rule(conn)

    run_lane(conn, "async", config.reference_now(), run_id="refund",
             ctx=EngineContext.load(conn))

    decisions = fetch_all(
        conn,
        """
        SELECT subject_id, score, band, action_taken, action_source_rule
          FROM decisions
         WHERE subject_type = 'customer' AND 'RF-401' = ANY(rules_fired)
           AND evaluation_id LIKE 'ev_refund_%'
        """,
    )
    assert [d["subject_id"] for d in decisions] == ["CUST-REFUND"], (
        "the planted refund-abuse customer must be the only one that fires")
    assert decisions[0]["score"] == 60          # 35 + 25
    assert decisions[0]["action_taken"] == "alert"
    assert decisions[0]["action_source_rule"] == "RF-401"


def test_both_conditions_are_required_so_volume_alone_is_not_abuse(conn, no_ddl):
    """AND across groups, which no shipped rule exercises.

    A customer with many small refunds is a customer who shops a lot. A customer
    with one large refund bought something expensive and sent it back. It is the
    conjunction that is the pattern, and combine='AND' is what says so — this
    test raises the value line above the fixture and watches the rule go quiet
    while the volume condition still fires.
    """
    _author_the_rule(conn)
    with conn.cursor() as cur:
        cur.execute("UPDATE rule_conditions SET threshold_num = 100000 "
                    "WHERE rule_id = 'RF-401' AND condition_group = 2")

    run_lane(conn, "async", config.reference_now(), run_id="refundand",
             ctx=EngineContext.load(conn))

    row = fetch_one(
        conn,
        """
        SELECT d.score, d.alert_id,
               count(*) FILTER (WHERE dc.fired) AS fired
          FROM decisions d
          JOIN decision_conditions dc ON dc.decision_id = d.decision_id
         WHERE d.subject_id = 'CUST-REFUND' AND d.evaluation_id LIKE 'ev_refundand_%'
         GROUP BY d.decision_id, d.score, d.alert_id
        """)
    assert row["fired"] == 1, "the volume condition still fires on its own"
    assert row["score"] == Decimal(0), (
        "but an unsatisfied rule contributes nothing — no partial score")
    assert row["alert_id"] is None


def test_no_other_customer_is_anywhere_near_the_threshold(conn):
    """The fixture's discrimination, not just its detection. Same shape as the
    card-testing equivalent, and the reason the thresholds are defensible: they
    separate two populations rather than fit one subject."""
    rows = fetch_all(
        conn,
        """
        SELECT DISTINCT ON (entity_id) entity_id, value_num
          FROM feature_values
         WHERE feature_key = 'customer_refund_count_30d' AND entity_id <> 'CUST-REFUND'
         ORDER BY entity_id, as_of DESC, computed_at DESC
        """)
    worst = max(float(r["value_num"]) for r in rows)
    assert worst <= MIN_REFUNDS - 3, (
        f"another customer reached {worst} refunds; the line is {MIN_REFUNDS}")

    value_rows = fetch_all(
        conn,
        """
        SELECT DISTINCT ON (entity_id) entity_id, value_num
          FROM feature_values
         WHERE feature_key = 'customer_refund_amount_30d' AND entity_id <> 'CUST-REFUND'
         ORDER BY entity_id, as_of DESC, computed_at DESC
        """)
    assert max(float(r["value_num"]) for r in value_rows) <= MIN_VALUE * 0.7


def test_the_new_rule_produces_an_explainable_alert(conn, no_ddl):
    """§1 applies to a rule nobody wrote code for, exactly as it does to R-114."""
    _author_the_rule(conn)
    run_lane(conn, "async", config.reference_now(), run_id="refundx",
             ctx=EngineContext.load(conn))

    from glassbox.contract.read import get_alert
    alert_id = fetch_value(
        conn,
        "SELECT a.alert_id FROM alerts a JOIN decisions d ON d.decision_id = a.decision_id "
        "WHERE a.subject_id = 'CUST-REFUND' AND d.evaluation_id LIKE 'ev_refundx_%'")
    alert = get_alert(conn, alert_id)

    assert alert.score == 60
    assert sum(s.contribution for s in alert.signals) == alert.score
    assert {s.feature_key for s in alert.signals} == {
        "customer_refund_count_30d", "customer_refund_amount_30d"}
    assert all(s.reason_code == "REFUND_ABUSE" for s in alert.signals)
    assert "12 refunds" in " ".join(s.human_text for s in alert.signals), (
        "the template interpolates the value the condition actually saw")
    assert alert.action.taken == "alert"
    assert alert.action.source_rule == "RF-401"
    assert alert.action.clear_text, "an authored rule owes a counterfactual too"


def test_the_extension_needed_no_new_reducer(conn):
    """Where §14's claim is strongest, and why it has to be stated with limits.

    Both features are plain reducers that already existed. The pattern's natural
    shape — refunds as a RATIO of purchases — is not expressible, because §3.1's
    `ratio` is deliberately unimplemented. Building one would have been the
    data-engineering ticket §14 says such a pattern costs, and doing it silently
    to make this test pass would have made the claim false for the very feature
    offered as evidence for it.
    """
    specs = {r["feature_key"]: r for r in fetch_all(
        conn,
        "SELECT feature_key, aggregation, source_relation, filter_predicate, "
        "       resolution_path, entity_type "
        "  FROM feature_catalog WHERE feature_key LIKE 'customer_refund_%'")}
    assert set(specs) == {"customer_refund_count_30d", "customer_refund_amount_30d"}
    assert {s["aggregation"] for s in specs.values()} == {"count", "sum"}

    for spec in specs.values():
        assert spec["source_relation"] == "transactions"
        assert spec["entity_type"] == "customer"
        assert spec["resolution_path"] == "self", (
            "the rule's subject IS the customer — no graph hop to make")
        assert spec["filter_predicate"] == {
            "op": "eq", "col": "txn_type", "value": "refund"}

    assert fetch_value(
        conn, "SELECT count(*) FROM feature_catalog WHERE aggregation = 'ratio'") == 0


def test_the_feature_pass_is_not_ddl_either(conn, no_ddl):
    """The other half of "a feature is a catalog row plus a runner pass".

    The rule INSERTs are obviously not DDL. The runner pass is the part where a
    reader might reasonably wonder, since it compiles a stored spec into SQL —
    so it runs here, under the same hook, over the two features 0028 added.
    """
    from glassbox.features.runner import IncrementalRunner

    reports = list(IncrementalRunner(conn).run_population(
        config.reference_now(), None,
        ["customer_refund_count_30d", "customer_refund_amount_30d"]))
    assert len(reports) == 2
    assert all(not r.skipped and r.rows_written > 0 for r in reports)
