"""§9 — dedup folding, restatement, suppression, and the routing record.

The acceptance criterion §9 states is "running the async cycle N times over a
static dataset produces the same alert count for every N". Before this week that
was false by construction: every run inserted a fresh alert row.
"""
from __future__ import annotations

from conftest import request_for
from glassbox import config
from glassbox.db import fetch_all, fetch_one, fetch_value
from glassbox.engine.evaluation import EngineContext, run_lane


def _alerts(conn) -> tuple[int, int]:
    row = fetch_one(conn, "SELECT count(*) AS n, "
                          "COALESCE(sum(triggering_events), 0) AS events FROM alerts")
    return row["n"], row["events"]


def test_n_runs_over_a_static_dataset_produce_a_constant_alert_count(conn, ctx):
    """§9's acceptance criterion, run literally.

    A ring re-evaluated every fifteen minutes would otherwise raise the same case
    96 times a day, and alert volume would be a count of cycles that rises when
    nothing changed.
    """
    before, events_before = _alerts(conn)
    assert before, "the fixtures must produce alerts for this test to mean anything"

    # Two more full passes. The session fixture already ran each lane once, so
    # this observes N = 1, 2, 3 — enough for a constant, without paying for a
    # third population pass in every test run.
    for i in range(2):
        for lane in ("inline_sync", "async"):
            run_lane(conn, lane, config.reference_now(), run_id=f"nrun{i}", ctx=ctx)

    after, events_after = _alerts(conn)
    assert after == before, "a re-run created a new case instead of folding onto the open one"
    # Each iteration folds every alert exactly ONCE — not twice, despite running
    # two lanes, because the seeded rules partition subject types by lane
    # (transaction -> inline_sync, account/network -> async) so no subject is ever
    # evaluated in both. The case count holds while the evidence count rises: that
    # is the distinction alert volume needs.
    assert events_after == events_before + 2 * before


def test_every_folded_evaluation_still_writes_a_decision(conn, ctx):
    """Folding must not lose the evaluation. Suppression is "recorded, not
    silent" in §9, and the same applies to a fold: the decision is the
    denominator of alert volume."""
    before = fetch_value(conn, "SELECT count(*) FROM decisions")
    run_lane(conn, "async", config.reference_now(), run_id="folddec", ctx=ctx)
    after = fetch_value(conn, "SELECT count(*) FROM decisions")
    assert after > before

    folded = fetch_all(
        conn,
        "SELECT alert_id, alert_routing FROM decisions "
        "WHERE evaluation_id LIKE 'ev_folddec_%' AND alert_routing <> 'no_authority'")
    assert folded, "the async lane alerts on ACC-2201 and RING-1187"
    assert all(r["alert_routing"] == "folded" for r in folded)
    assert all(r["alert_id"] is not None for r in folded)


def test_every_decision_says_what_happened_to_it(conn):
    """The alert-volume denominator. 9,916 of 9,923 decisions raise nothing, and
    each one has to say so — otherwise "alert volume" is a numerator alone."""
    assert fetch_value(conn, "SELECT count(*) FROM decisions WHERE alert_routing IS NULL") == 0
    assert fetch_all(conn, "SELECT * FROM v_decision_routing") == []

    spread = {r["alert_routing"]: r["n"] for r in fetch_all(
        conn, "SELECT alert_routing, count(*) AS n FROM decisions GROUP BY 1")}
    assert spread["no_authority"] > spread.get("raised", 0)


def test_an_equal_rescore_does_not_churn_the_signal_rows(conn, ctx):
    """Restatement is gated on STRICTLY greater.

    With >=, re-running a lane over unchanged data would delete and re-insert
    every alert's signals on every run, forever — churning the exact rows the sum
    invariant is checked against.
    """
    high_water = fetch_value(conn, "SELECT max(signal_id) FROM alert_signals")
    run_lane(conn, "async", config.reference_now(), run_id="nochurn", ctx=ctx)
    assert fetch_value(conn, "SELECT max(signal_id) FROM alert_signals") == high_water


def test_a_worse_evaluation_restates_the_case_and_the_bar_still_adds_up(conn, ctx):
    """The trap this whole slice is arranged around.

    An alert's signal set must always be exactly ONE decision's pool. Updating
    the score while leaving the old signals would leave an alert whose bar does
    not sum to its own number — which the contract refuses to serve, the view
    reports as a failure, and the test suite fails on. So restatement replaces
    the numbers and the evidence together, or neither.
    """
    before = fetch_one(
        conn,
        "SELECT alert_id, decision_id, score, triggering_events FROM alerts "
        "WHERE subject_id='RING-1187'")
    assert before is not None

    # Make the next evaluation score higher, from inside the rolled-back
    # transaction: +9 on a condition L-203 already fires.
    with conn.cursor() as cur:
        cur.execute("UPDATE rule_conditions SET contribution_points = 31 "
                    "WHERE rule_id='L-203' AND feature_key='accounts_per_device'")

    run_lane(conn, "async", config.reference_now(), run_id="restate",
             subject_ids=["RING-1187"], ctx=EngineContext.load(conn))

    after = fetch_one(
        conn,
        "SELECT alert_id, decision_id, score, triggering_events FROM alerts "
        "WHERE subject_id='RING-1187'")
    assert after["alert_id"] == before["alert_id"], "restatement must reuse the case"
    assert after["score"] == before["score"] + 9
    assert after["decision_id"] != before["decision_id"], (
        "a restated case must point at the evaluation whose numbers it now shows")
    assert after["triggering_events"] == before["triggering_events"] + 1

    signal_sum = fetch_value(
        conn, "SELECT sum(contribution) FROM alert_signals WHERE alert_id=%s",
        (after["alert_id"],))
    assert signal_sum == after["score"]
    assert fetch_all(conn, "SELECT * FROM v_alert_invariants "
                           "WHERE NOT sum_ok OR NOT source_rule_ok") == []

    decision = fetch_one(conn, "SELECT alert_routing FROM decisions WHERE decision_id=%s",
                         (after["decision_id"],))
    assert decision["alert_routing"] == "restated"


def test_alert_subjects_survive_a_restatement(conn, ctx):
    """alert_subjects is upserted and never deleted. A member the cluster builder
    has since retired was still part of the case an analyst has been reading, and
    rebuilding the coverage would silently rewrite that history."""
    alert_id = fetch_value(conn, "SELECT alert_id FROM alerts WHERE subject_id='RING-1187'")
    before = fetch_value(conn, "SELECT count(*) FROM alert_subjects WHERE alert_id=%s",
                         (alert_id,))
    assert before == 5

    with conn.cursor() as cur:
        cur.execute("UPDATE rule_conditions SET contribution_points = 40 "
                    "WHERE rule_id='L-203' AND feature_key='accounts_per_device'")
        cur.execute("DELETE FROM cluster_members "
                    "WHERE cluster_id='RING-1187' AND subject_id='ACC-7745'")

    run_lane(conn, "async", config.reference_now(), run_id="subj",
             subject_ids=["RING-1187"], ctx=EngineContext.load(conn))

    assert fetch_value(conn, "SELECT count(*) FROM alert_subjects WHERE alert_id=%s",
                       (alert_id,)) == before


def test_the_fold_window_is_measured_on_event_time(conn, ctx):
    """alerts.created_at is DEFAULT now() — wall clock. All seven fixture alerts
    are created seconds apart, so a window measured on created_at would appear to
    work while being wrong for every historical replay.

    Age the open case's EVENT clock past the network policy's 7-day window and
    leave created_at untouched. A window read on event time now falls outside and
    the evaluation raises its own case; a window read on created_at would still
    fold. Only one of those two can pass this test.

    It also pins the deliberate choice to anchor the window on first_event_at
    rather than slide it along last_event_at: a case has a bounded life, so a ring
    active for a month is four weekly cases rather than one immortal one.
    """
    with conn.cursor() as cur:
        cur.execute("UPDATE alert_policy SET suppress_while_open = FALSE "
                    "WHERE subject_type='network'")
        cur.execute("UPDATE alerts SET first_event_at = first_event_at - INTERVAL '60 days', "
                    "last_event_at = last_event_at - INTERVAL '60 days' "
                    "WHERE subject_id='RING-1187'")

    before, _ = _alerts(conn)
    run_lane(conn, "async", config.reference_now(), run_id="aged",
             subject_ids=["RING-1187"], ctx=ctx)
    after, _ = _alerts(conn)
    assert after == before + 1, (
        "the evaluation is 60 days outside the open case's window and must raise "
        "its own — if it still folded, the window is being read on wall clock")

    routing = fetch_value(
        conn, "SELECT alert_routing FROM decisions "
              "WHERE evaluation_id LIKE 'ev_aged_%' AND subject_id='RING-1187'")
    assert routing == "raised"


def test_a_suppressed_reevaluation_is_a_decision_and_not_a_queue_entry(conn):
    """§9: subjects under an open case do not re-alert, or an analyst's own
    investigation generates noise that lands back in their queue.

    Unreachable on the shipped fixtures — every alerting subject has exactly one
    dedup_key — so the second rule set is constructed here, which is also the
    honest way to say that suppression is untested by the dataset itself.
    """
    from glassbox.contract.queue import read_queue

    with conn.cursor() as cur:
        # A second async rule on `account`, so ACC-2201 produces a DIFFERENT
        # dedup_key while its S-077 case is still open and undispositioned.
        cur.execute(
            """
            INSERT INTO rule_definitions
                (rule_id, name, subject_type, execution_mode, action,
                 review_threshold, prevent_threshold, combine, status, version)
            VALUES ('X-900','Second look at the same account','account','async',
                    'alert', 5, 999, 'AND', 'active', 1)
            """)
        cur.execute(
            """
            INSERT INTO rule_conditions
                (rule_id, condition_group, feature_key, operator, threshold_num,
                 contribution_points, reason_code, signal_template, is_required)
            VALUES ('X-900', 1, 'amount_over_avail_balance_pct', '>=', 1, 6,
                    'PAYEE_DRAIN', 'Moved {v}% of the available balance', TRUE)
            """)
        cur.execute("DELETE FROM case_outcomes")   # keep the S-077 case open

    queue_before = len(read_queue(conn, limit=500))
    run_lane(conn, "async", config.reference_now(), run_id="suppress",
             subject_ids=["ACC-2201"], ctx=EngineContext.load(conn))

    decision = fetch_one(
        conn,
        "SELECT alert_routing, alert_id FROM decisions "
        "WHERE evaluation_id LIKE 'ev_suppress_%' AND subject_id='ACC-2201'")
    assert decision["alert_routing"] == "suppressed"
    assert decision["alert_id"] is not None, "a suppressed decision must name its suppressor"
    assert len(read_queue(conn, limit=500)) == queue_before


def test_exposure_is_recorded_with_the_derivation_that_produced_it(conn):
    """A money number that reorders an analyst's queue has to explain itself.
    There is no accounts.available_balance in this schema, so account and network
    exposure are derived, and the row says which derivation."""
    rows = fetch_all(conn, "SELECT subject_type, exposure_amount, exposure_basis "
                           "FROM alerts ORDER BY alert_id")
    assert rows
    assert all(r["exposure_basis"] for r in rows), "an unexplained exposure is not publishable"
    assert all(r["exposure_amount"] is not None for r in rows)

    by_type = {r["subject_type"]: r["exposure_basis"] for r in rows}
    assert by_type["transaction"] == "trigger_txn_amount_base"
    assert by_type["network"] == "cluster_inbound_7d"
    assert by_type["account"] == "account_net_flow_90d"


def test_exposure_is_bounded_at_the_decisions_point_in_time_bound(conn, ctx):
    """Bounded at pit_bound_at, never at now(). An unbounded exposure drifts on
    every recomputation, so the queue order would silently change and §9's
    N-runs invariant would depend on the wall clock."""
    from glassbox.engine.evaluation import evaluate
    from glassbox.engine.exposure import exposure_for

    request = request_for(conn, ctx, "async", "network", "RING-1187")
    result = evaluate(conn, request, ctx)
    baseline = exposure_for(conn, [result])[("network", "RING-1187")][0]

    # A large inbound AFTER the bound must not count: it had not happened yet.
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO transactions (txn_id, occurred_at, amount, amount_base, currency,
                                      direction, txn_type, account_id)
            SELECT 'TXN-FUTURE-EXPO', %s::timestamptz + INTERVAL '2 days', 999999, 999999,
                   'GBP', 'inbound', 'transfer', cm.subject_id
              FROM cluster_members cm
             WHERE cm.cluster_id='RING-1187' AND cm.subject_type='account'
             LIMIT 1
            """,
            (result.pit_bound_at,))

    assert exposure_for(conn, [result])[("network", "RING-1187")][0] == baseline
