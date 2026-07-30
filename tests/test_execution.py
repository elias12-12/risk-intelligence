"""§8 — action execution and outcome capture.

Acceptance: every preventive decision has an execution row; challenge outcomes
flow to case_outcomes; block rate and challenge pass rate are computed from
executions rather than from decisions.
"""
from __future__ import annotations

from glassbox import config
from glassbox.db import fetch_all, fetch_one, fetch_value
from glassbox.engine.evaluation import run_lane
from glassbox.engine.outcomes import draw, settle


def test_every_preventive_decision_has_an_execution_row(conn):
    """Both directions. An execution with no authorising decision is as wrong as a
    preventive decision that never reached the customer."""
    missing = fetch_all(
        conn,
        """
        SELECT d.decision_id, d.action_taken
          FROM decisions d
         WHERE d.action_taken IN ('challenge','hold','block')
           AND d.alert_routing = 'raised'
           AND NOT EXISTS (SELECT 1 FROM action_executions ae
                            WHERE ae.decision_id = d.decision_id
                              AND ae.action = d.action_taken)
        """)
    assert missing == []

    orphans = fetch_value(
        conn,
        "SELECT count(*) FROM action_executions ae "
        "WHERE NOT EXISTS (SELECT 1 FROM decisions d WHERE d.decision_id = ae.decision_id)")
    assert orphans == 0

    assert fetch_value(
        conn, "SELECT count(*) FROM action_executions WHERE action <> 'notify'") > 0


def test_a_folded_evaluation_does_not_challenge_the_customer_again(conn, ctx):
    """The customer-facing half of §9.

    A ring re-evaluated every fifteen minutes would send 96 step-ups a day for one
    situation. Preventive actions are issued on a RAISED case only; a notification
    is cheap and goes out again, a step-up is not and does not.
    """
    before = fetch_value(
        conn, "SELECT count(*) FROM action_executions WHERE action <> 'notify'")
    run_lane(conn, "inline_sync", config.reference_now(), run_id="refold", ctx=ctx)
    after = fetch_value(
        conn, "SELECT count(*) FROM action_executions WHERE action <> 'notify'")
    assert after == before


def test_notify_is_severity_routed_and_needs_no_ref_action_row(conn):
    """`notify` is a channel-level execution, not a rung on the severity ladder.
    Putting it in ref_action would tie with `alert` at severity 2 and make §7.2's
    maximum-severity resolution non-deterministic — 0013 says so, and this pins
    it so a later 'tidy-up' cannot quietly break precedence."""
    assert fetch_value(conn, "SELECT count(*) FROM ref_action WHERE action='notify'") == 0

    channels = {(r["band"], r["channel"]) for r in fetch_all(
        conn,
        """
        SELECT a.band, ae.channel
          FROM action_executions ae
          JOIN alerts a ON a.alert_id = ae.alert_id
         WHERE ae.action = 'notify'
        """)}
    assert channels, "raising a case notifies somebody"
    assert ("high", "phone") in channels, "the high band pages a human"
    assert all(ch in ("phone", "queue") for _, ch in channels)


def test_challenge_outcomes_are_settled_and_flow_to_case_outcomes(conn):
    settled = fetch_all(
        conn,
        "SELECT outcome, outcome_source, resolved_at, issued_at, synthetic "
        "FROM action_executions WHERE action='challenge'")
    assert settled, "the fixtures authorise challenges"
    assert all(r["outcome"] for r in settled)
    assert all(r["synthetic"] for r in settled), (
        "an outcome nobody observed must be labelled as such on the row")
    assert all(r["resolved_at"] > r["issued_at"] for r in settled), (
        "issued_at -> resolved_at is the latency measurement; it cannot be zero")

    # Every challenged case reaches a disposition, which is what makes prevention
    # measurable at all: blocked and challenged events never entered the queue
    # before this week, so they never reached case_outcomes.
    unreached = fetch_value(
        conn,
        """
        SELECT count(*) FROM action_executions ae
          JOIN alerts a ON a.alert_id = ae.alert_id
         WHERE ae.action = 'challenge'
           AND NOT EXISTS (SELECT 1 FROM case_outcomes co WHERE co.alert_id = a.alert_id)
        """)
    assert unreached == 0


def test_a_prevention_false_positive_is_visible(conn):
    """§8's sharpest case: a customer challenged, who passed, on a case later
    dispositioned confirmed_legit. A wrong block that nobody could observe was the
    hole in §7.3's argument for a higher preventive threshold.

    On the shipped fixtures the count is ZERO — every preventive action lands on a
    fraud-labelled subject, so no challenge passes and the denominator is empty.
    That is a result about the rules, not about the query, so the query is
    exercised here on a constructed case inside the rolled-back transaction.
    """
    query = """
        SELECT count(*) FROM action_executions ae
          JOIN alerts a ON a.alert_id = ae.alert_id
          JOIN case_outcomes co ON co.alert_id = a.alert_id
         WHERE ae.action = 'challenge' AND ae.outcome = 'passed'
           AND co.disposition = 'confirmed_legit'
    """
    assert fetch_value(conn, query) == 0

    with conn.cursor() as cur:
        cur.execute("UPDATE action_executions SET outcome='passed', outcome_source='customer' "
                    "WHERE action='challenge'")
        cur.execute("UPDATE case_outcomes SET disposition='confirmed_legit' "
                    "WHERE alert_id IN (SELECT alert_id FROM action_executions "
                    "                    WHERE action='challenge')")
    assert fetch_value(conn, query) > 0, (
        "the prevention-false-positive join must work; on this dataset it simply "
        "has nothing to find")


def test_prevention_rates_come_from_executions_not_decisions(conn, ctx):
    """§8's acceptance, stated as arithmetic. A decision to block is an INTENTION;
    an execution is the event. Counting intentions and calling it a block rate is
    the thing this table exists to stop, and the two diverge the moment anything
    is evaluated twice — which, on a fifteen-minute graph cycle, is always.
    """
    def counts() -> tuple[int, int]:
        return (
            fetch_value(conn, "SELECT count(*) FROM decisions "
                              "WHERE action_taken IN ('challenge','hold','block')"),
            fetch_value(conn, "SELECT count(*) FROM action_executions "
                              "WHERE action IN ('challenge','hold','block')"),
        )

    intentions_before, issued_before = counts()
    assert intentions_before == issued_before, (
        "on a single pass every preventive intention was carried out exactly once")

    for lane in ("inline_sync", "async"):
        run_lane(conn, lane, config.reference_now(), run_id="rates", ctx=ctx)

    intentions_after, issued_after = counts()
    assert intentions_after > intentions_before, "a second pass intends the same actions again"
    assert issued_after == issued_before, (
        "and must not carry them out again — a block rate counted off decisions "
        "would now be double the number of customers actually affected")


def test_resolution_is_deterministic(conn):
    """blake2b over the identity of the thing being decided, not random.seed.

    A global seed makes each outcome depend on how many draws came before it, so
    inserting one execution would reshuffle every outcome after it and a re-run
    would silently disagree with the run it replaced.
    """
    assert draw("challenge", 7, 12) == draw("challenge", 7, 12)
    assert draw("challenge", 7, 12) != draw("challenge", 8, 12)

    before = fetch_all(conn, "SELECT execution_id, outcome, resolved_at "
                             "FROM action_executions ORDER BY execution_id")
    with conn.cursor() as cur:
        cur.execute("UPDATE action_executions SET resolved_at=NULL, outcome=NULL, "
                    "outcome_source=NULL, synthetic=FALSE")
    settle(conn)
    after = fetch_all(conn, "SELECT execution_id, outcome, resolved_at "
                            "FROM action_executions ORDER BY execution_id")
    assert after == before


def test_settling_twice_does_not_double_up(conn):
    """`resolved_at IS NULL` and a NOT EXISTS on case_outcomes are the only guards:
    case_outcomes has no uniqueness constraint, and a duplicate disposition would
    fan out every join the §10 report makes through it."""
    executions = fetch_value(conn, "SELECT count(*) FROM action_executions")
    outcomes = fetch_value(conn, "SELECT count(*) FROM case_outcomes")
    tally = settle(conn)
    assert tally["challenges"] == 0 and tally["dispositions"] == 0
    assert fetch_value(conn, "SELECT count(*) FROM action_executions") == executions
    assert fetch_value(conn, "SELECT count(*) FROM case_outcomes") == outcomes


def test_the_systems_own_actions_become_a_feature(conn):
    """§8's third payoff, end to end, in one rolled-back transaction:
    decide -> issue -> settle -> compute -> read.

    Deliberately NOT in the session fixture: test_consolidation.py pins exactly one
    inline_sync decision for TXN-48300, and a second cycle there would break it.
    """
    from glassbox.features.runner import IncrementalRunner

    key = "card_challenge_fails_30d"
    settle(conn)                       # ensure challenge_failed events exist
    failures = fetch_value(
        conn, "SELECT count(*) FROM events WHERE event_type='challenge_failed'")
    assert failures > 0, "a failed or abandoned step-up writes a behavioural event"

    report = IncrementalRunner(conn).run_feature(key, config.reference_now())
    assert not report.skipped
    assert report.rows_written > 0

    row = fetch_one(
        conn,
        "SELECT entity_type, max(value_num) AS worst FROM feature_values "
        "WHERE feature_key=%s GROUP BY entity_type", (key,))
    assert row["entity_type"] == "card", "the feature keys on the card, not the transaction"
    assert row["worst"] >= 1


def test_that_feature_needed_no_new_reducer_and_no_new_relation(conn):
    """The §14 claim, checked on the one feature offered as evidence for it.

    Reading action_executions directly would have cost three code changes and a
    denormalised card_id — for the feature presented as proof that growth costs
    rows. So it reads `events`, which §8 says it should.
    """
    from glassbox.features import predicate

    assert "action_executions" not in predicate.ALLOWED_RELATIONS

    spec = fetch_one(
        conn,
        "SELECT source_relation, aggregation, resolution_path, default_when_absent "
        "FROM feature_catalog WHERE feature_key='card_challenge_fails_30d'")
    assert spec["source_relation"] == "events"
    assert spec["aggregation"] == "count"          # an existing reducer
    assert spec["resolution_path"] == "transaction.card_id"
    # An aggravating-direction feature may default; 0 failed step-ups is knowledge,
    # not absence. Every MITIGATOR must default to NULL — test_degraded.py enforces
    # that across the catalog, and this feature is not one.
    assert spec["default_when_absent"] == 0


def test_the_delivered_outcome_divergence_is_pinned(conn):
    """§8's table lists `delivered`; 0013's CHECK allows `completed`. Notifications
    settle as completed + system. Pinned so the divergence stays a recorded
    decision rather than resurfacing as a surprise."""
    with conn.cursor() as cur:
        try:
            cur.execute("UPDATE action_executions SET outcome='delivered' "
                        "WHERE action='notify'")
            raised = False
        except Exception:       # noqa: BLE001 - psycopg raises CheckViolation
            raised = True
    assert raised, "ck_ae_outcome must still reject 'delivered'"
    conn.rollback()

    assert fetch_value(
        conn, "SELECT count(*) FROM action_executions "
              "WHERE action='notify' AND outcome='completed' AND outcome_source='system'") > 0
