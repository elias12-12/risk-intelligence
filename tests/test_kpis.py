"""§11 — the nine tiles, and the four rules they are held to.

§11's argument is that a KPI shipped before its prerequisite is a number wrong in
a way nobody can see. So these tests are mostly not about arithmetic. They are
about the four properties that make a tile readable at all: it names its window,
it publishes its denominator, it only claims a delta it can support, and it says
when its number is synthetic.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from glassbox import config
from glassbox.contract.kpis import DEFAULT_WINDOW, KpiSet, _delta, _rate, read_kpis
from glassbox.db import fetch_all, fetch_one, fetch_value
from glassbox.engine.evaluation import EngineContext, run_lane


@pytest.fixture
def kpis(conn) -> KpiSet:
    return read_kpis(conn, as_of=config.reference_now())


def _tile(kpis: KpiSet, key: str):
    return next(t for t in kpis.tiles if t.key == key)


# ---------------------------------------------------------------- the four rules

def test_all_nine_tiles_are_present(kpis):
    assert [t.key for t in kpis.tiles] == [
        "alert_volume", "score_distribution", "false_positive_rate",
        "false_negative_rate", "validation_outcomes", "median_triage_time",
        "action_rates", "rule_precision", "emerging_trends",
    ]


def test_every_tile_names_its_window_and_its_prerequisite(kpis):
    """A tile that travels without its window is a number somebody will compare
    against a different one."""
    for tile in kpis.tiles:
        assert tile.window_start == kpis.window_start
        assert tile.window_end == kpis.window_end
        assert tile.window_end > tile.window_start
        assert tile.basis and tile.requires, (
            f"{tile.key} does not say how it was derived or what it needed")


def test_every_rate_publishes_its_denominator(kpis):
    """§8's denominators are single digits here. A bare percentage would read as
    a result when it is a sample size."""
    for tile in kpis.tiles:
        if tile.unit == "percent" and tile.value is not None:
            assert tile.denominator, f"{tile.key} is a rate with no denominator"
        for part in tile.parts:
            if part.unit == "percent" and part.value is not None:
                assert part.denominator, f"{tile.key}/{part.label} has no denominator"


def test_an_empty_denominator_is_none_and_never_zero():
    """0% and "no measurement" are different claims, and rendering the second as
    the first is how an absence becomes a result."""
    assert _rate(0, 0) is None
    assert _rate(3, 0) is None
    assert _rate(0, 10) == Decimal("0.00")


def test_a_delta_exists_only_against_a_real_prior_window(conn):
    """§11: "a delta with no baseline is the one kind of KPI that is worse than
    no KPI."

    The dataset spans 30 days. A 7-day window has a real 7-day predecessor; a
    25-day window does not, and every delta in that set must be null rather than
    computed against a partial window that would look like a trend.
    """
    short = read_kpis(conn, as_of=config.reference_now(), window=timedelta(days=7))
    assert short.baseline_available
    assert short.baseline_start and short.baseline_end == short.window_start
    assert any(t.delta_pct is not None for t in short.tiles)

    long = read_kpis(conn, as_of=config.reference_now(), window=timedelta(days=25))
    assert not long.baseline_available
    assert long.baseline_absent_reason and "delta" in long.baseline_absent_reason
    assert all(t.delta_pct is None for t in long.tiles)
    assert all(p.delta_pct is None for t in long.tiles for p in t.parts)


def test_a_change_from_zero_is_not_a_delta():
    """Every increase from nothing is infinite. Rendering it as +100% would be a
    claim the data does not make."""
    assert _delta(Decimal(5), Decimal(0)) is None
    assert _delta(Decimal(5), None) is None
    assert _delta(None, Decimal(5)) is None
    assert _delta(Decimal(6), Decimal(4)) == Decimal("50.0")


def test_the_synthetic_tiles_say_so(kpis):
    """§11's own objection to console copy that outruns the system, applied to
    the numbers rather than to the strings around them."""
    fn = _tile(kpis, "false_negative_rate")
    assert fn.synthetic and fn.caveat and "synthetic_label" in fn.caveat

    actions = _tile(kpis, "action_rates")
    assert actions.synthetic, "every challenge outcome here was settled by a script"
    assert "not by a customer" in actions.caveat
    assert "not an observed failure" in actions.caveat, (
        "fail_mode holds the lane's policy; a tile must not read as a measured "
        "resilience number")

    assert not _tile(kpis, "alert_volume").synthetic, (
        "the routing record is observed, not synthesised — over-flagging is its "
        "own kind of dishonesty")


# ---------------------------------------------------------------- the numbers

def test_alert_volume_counts_cases_not_evaluation_cycles(conn, ctx, kpis):
    """The tile §9 had to exist before. Re-run a lane: the decisions rise, the
    cases do not, because a repeat evaluation folds."""
    before = _tile(kpis, "alert_volume")
    decisions_before = fetch_value(conn, "SELECT count(*) FROM decisions")

    run_lane(conn, "async", config.reference_now(), run_id="kpirerun", ctx=ctx)

    after = _tile(read_kpis(conn, as_of=config.reference_now()), "alert_volume")
    assert fetch_value(conn, "SELECT count(*) FROM decisions") > decisions_before
    assert after.value == before.value, (
        "a folded evaluation is the same case seen again; counting it would put "
        "alert volume back to counting cycles")
    folded = next(p for p in after.parts if p.label == "folded")
    assert folded.value > 0, "the re-run did fold, so the test is not vacuous"


def test_the_denominator_is_every_decision_including_the_silent_ones(conn, kpis):
    volume = _tile(kpis, "alert_volume")
    counted = sum(int(p.value) for p in volume.parts)
    assert counted == volume.denominator, (
        "every decision must be accounted for by exactly one routing value — "
        "that is what makes the denominator a denominator")


def test_the_false_negative_rate_is_measured_against_planted_fraud(conn, kpis):
    fn = _tile(kpis, "false_negative_rate")
    fraud = fetch_value(
        conn,
        """
        SELECT count(*) FROM v_kpi_decisions
         WHERE is_fraud AND event_at > %s AND event_at <= %s
        """, (fn.window_start, fn.window_end))
    assert fn.denominator == fraud > 0
    assert fn.numerator == fraud - fetch_value(
        conn,
        """
        SELECT count(*) FROM v_kpi_decisions
         WHERE is_fraud AND became_case AND event_at > %s AND event_at <= %s
        """, (fn.window_start, fn.window_end))
    assert fn.value > 50, (
        "the labelled cohort was deliberately sized so most clusters fall below "
        "R-114's line; a cohort the rules caught entirely would make this tile "
        "read 0% and prove nothing")


def test_per_rule_precision_survives_consolidation(conn, kpis):
    """§6's promise: attribution is recoverable after dedup because
    asserted_by_rules was recorded. T-021 is the proof — it never carries an
    action, so without the column it would be invisible here."""
    tile = _tile(kpis, "rule_precision")
    labels = {p.label for p in tile.parts}
    assert "T-021" in labels

    carried = fetch_value(
        conn, "SELECT count(*) FROM v_kpi_rule_attribution "
              "WHERE rule_id='T-021' AND carried_the_action")
    asserted = fetch_value(
        conn, "SELECT count(*) FROM v_kpi_rule_attribution WHERE rule_id='T-021'")
    assert carried == 0 and asserted > 0, (
        "a veto rule asserts evidence and never carries the action; the tile "
        "publishes the first and v_kpi_rule_attribution keeps both")


def test_action_rates_are_counted_off_executions_not_decisions(conn, kpis):
    """§8: off decisions, "block rate" counts intentions."""
    tile = _tile(kpis, "action_rates")
    executions = fetch_value(
        conn,
        """
        SELECT count(*) FROM v_kpi_executions
         WHERE is_preventive AND event_at > %s AND event_at <= %s
        """, (tile.window_start, tile.window_end))
    assert tile.numerator == executions

    intentions = fetch_value(
        conn,
        """
        SELECT count(*) FROM decisions
         WHERE action_taken IN ('challenge','hold','block')
           AND occurred_at > %s AND occurred_at <= %s
        """, (tile.window_start, tile.window_end))
    assert intentions >= executions, (
        "there are at least as many decisions to prevent as preventions issued; "
        "if this ever inverts, something is issuing what nothing authorised")


def test_the_prevention_false_positive_count_is_a_result_not_an_absence(kpis):
    """It is zero, and §8's join is what makes zero meaningful: every preventive
    action on this dataset lands on a fraud-labelled subject, so no challenge
    passes and nothing is dispositioned legitimate."""
    parts = {p.label: p for p in _tile(kpis, "action_rates").parts}
    fp = parts["prevention false positives"]
    assert fp.value == 0
    assert fp.denominator > 0, "zero out of zero would be an absence, not a result"


# ---------------------------------------------------------------- event time

def test_windows_are_measured_on_event_time(conn):
    """The trap 0023 and §W3.6 both name. `alerts.created_at` is DEFAULT now(),
    so every fixture alert is created seconds apart and any window measured on it
    silently reports a replay of January as belonging to today."""
    mismatched = fetch_all(
        conn,
        """
        SELECT c.alert_id, c.event_at, d.occurred_at
          FROM v_kpi_cases c
          JOIN alerts a ON a.alert_id = c.alert_id
          JOIN decisions d ON d.decision_id = a.decision_id
         WHERE c.event_at <> d.occurred_at
        """)
    assert mismatched == [], "a case's clock is its earliest EVENT, not its INSERT"

    spread = fetch_one(
        conn,
        "SELECT max(created_at) - min(created_at) AS wall, "
        "       max(first_event_at) - min(first_event_at) AS event FROM alerts")
    assert spread["event"] > spread["wall"], (
        "the fixtures were all inserted within seconds of each other, which is "
        "exactly why created_at cannot carry a window")


def test_the_set_is_reproducible_for_a_fixed_as_of(conn):
    """No tile may read the wall clock. Two calls with the same as_of must agree
    exactly, including every window boundary."""
    first = read_kpis(conn, as_of=config.reference_now())
    second = read_kpis(conn, as_of=config.reference_now())
    assert first.model_dump() == second.model_dump()

    shifted = read_kpis(conn, as_of=config.reference_now() - DEFAULT_WINDOW)
    assert shifted.window_end != first.window_end
    assert shifted.model_dump() != first.model_dump(), (
        "and a different as_of must produce a different answer, or the window is "
        "not being applied at all")
