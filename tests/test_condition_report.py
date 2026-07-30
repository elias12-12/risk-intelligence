"""§10 — the condition-level report, and the misprice it found and closed.

§10's acceptance named one condition: `country_is_new_for_customer` was +50, sized
backwards so that T-021's points would sum to its displayed 31. Against a
population that single condition put every genuine first trip abroad at 50 —
mid-elevated — unless a mitigator happened to fire. A false-positive engine sitting
inside the rule whose stated purpose is demonstrating false-positive avoidance.

Seed `0026` repriced it to +12 on this report's own evidence, so the tests below
have two jobs rather than one: the misprice is gone, AND the instrument that found
it still works. The second is the one that matters, because a report that can only
detect a finding already applied is not a report. It is re-established by putting
the +50 back inside a rolled-back transaction and asserting it resurfaces.

Every number is re-derived from the view rather than transcribed, except the
seeded price.
"""
from __future__ import annotations

import condition_report

from glassbox.db import fetch_all, fetch_one, fetch_value


def _by_feature(conn) -> dict[str, dict]:
    return {r["feature_key"]: r for r in fetch_all(
        conn, "SELECT * FROM v_condition_performance")}


def _rows(conn) -> list[dict]:
    return fetch_all(conn, "SELECT * FROM v_condition_performance")


def test_the_misprice_the_report_found_has_been_closed(conn):
    """0026, checked against the view rather than against the seed file."""
    country = _by_feature(conn)["country_is_new_for_customer"]
    assert country["priced_points"] == 12, (
        "0026 repriced this condition; if it reads 50 the seed did not apply")
    assert country["fired"] > 100, "still fires at population scale — that is why "\
                                   "its price mattered"
    assert float(country["precision_pct"]) < 15.0, (
        "and still earns single-digit precision; the reprice corrected the PRICE, "
        "not the signal, which is the honest limit of what calibration can do")

    worst, benchmark, anchor, ratio = condition_report.cost_anchor(_rows(conn))
    assert ratio is not None and anchor
    assert ratio < condition_report.MATERIAL, (
        f"{worst['feature_key']} costs {ratio:.1f}x the catalog median per unit of "
        f"measured precision; §10 says that is a finding to act on, not to pin")


def test_the_report_would_still_find_a_misprice(conn):
    """The instrument, not the finding.

    Restore the +50 inside the rolled-back test transaction. It must climb back
    to the top of the ranking and cross the materiality threshold — which is what
    makes the passing state above a measurement rather than a tautology.
    """
    before_worst, _, _, before_ratio = condition_report.cost_anchor(_rows(conn))
    assert before_ratio < condition_report.MATERIAL

    with conn.cursor() as cur:
        cur.execute("UPDATE rule_conditions SET contribution_points = 50 "
                    "WHERE rule_id = 'T-021' "
                    "  AND feature_key = 'country_is_new_for_customer'")

    worst, _, _, ratio = condition_report.cost_anchor(_rows(conn))
    assert worst["feature_key"] == "country_is_new_for_customer"
    assert ratio >= condition_report.MATERIAL, (
        "at +50 this condition must read as materially mispriced; if it does not, "
        "the report can no longer detect the finding it was built to produce")
    assert ratio > before_ratio


def test_the_anchor_is_the_median_not_the_cheapest_condition(conn):
    """Anchoring on the cheapest condition anchors on a fixture.

    Four conditions fire only on the planted cases, where 100% precision is a
    property of the fixture and not a measurement. The median of the
    comparably-sampled aggravators is the price this catalog actually charges,
    and it is what a repricing has to be argued against.
    """
    rows = _rows(conn)
    _, benchmark, anchor, _ = condition_report.cost_anchor(rows)
    costs = sorted(float(r["points_per_precision_point"]) for r in benchmark)
    assert len(benchmark) >= 2
    assert all(r["fired"] >= condition_report.MIN_SAMPLE for r in benchmark), (
        "a precision over a handful of firings is an anecdote, not a benchmark")
    assert anchor > costs[0], "the anchor must not collapse onto the cheapest peer"
    assert costs[0] <= anchor <= costs[-1]


def test_precision_is_measured_per_direction(conn):
    """A mitigator is right when it fires on LEGITIMATE traffic — that is its job.

    Scoring it by its fraud rate inverts it: entry_mode_chip_pin fired thousands of
    times and never once on fraud, which is perfect mitigator behaviour, and a
    direction-blind precision ranks it as the worst condition in the catalog. Same
    inversion §5 objects to when an absent mitigator is treated as a non-firing
    one, same cause — reading a deduction as if it were an accusation.
    """
    rows = _by_feature(conn)
    chip_pin = rows["entry_mode_chip_pin"]
    assert chip_pin["direction"] == "mitigating"
    assert chip_pin["fired"] > 1000
    assert chip_pin["fired_on_fraud"] == 0
    assert float(chip_pin["precision_pct"]) == 100.0, (
        "a mitigator that never fired on fraud is perfectly specific, not useless")

    cnp = rows["card_cnp_count"]
    assert cnp["direction"] == "aggravating"
    assert float(cnp["precision_pct"]) > 90.0


def test_a_veto_rules_conditions_are_still_measurable(conn):
    """T-021 is is_veto, so precedence.decide never lets it authorise a case and it
    can never produce a case_outcomes row of its own. Its conditions still get a
    LABEL precision, which is the only reason the +50 misprice was findable at all
    — and the only reason a future one on a veto rule would be.

    Its alert precision, where it exists, is the disposition of a case some OTHER
    rule raised on a decision T-021 was evaluated on. Recorded here so the column
    is not misread as the veto rule's own hit rate.
    """
    assert fetch_value(
        conn, "SELECT count(*) FROM decisions WHERE vetoed_by='T-021' AND alert_id IS NULL"
    ) is not None
    country = fetch_one(
        conn, "SELECT * FROM v_condition_performance WHERE feature_key=%s",
        ("country_is_new_for_customer",))
    assert country["rule_id"] == "T-021"
    assert country["precision_pct"] is not None, (
        "label precision must survive for a rule that never raises a case")


def test_the_report_never_writes(conn):
    """§10: calibration output is a recommendation to a human, never an automatic
    write. Silently retuned weights break the audit story — an analyst could not
    explain why last week's identical transaction scored differently.

    0026 applied the report's finding — by hand, as a reviewed seed file. That is
    the distinction this test exists to keep: the report may recommend +12, and a
    human writes the UPDATE.
    """
    before = fetch_all(conn, "SELECT condition_id, contribution_points "
                             "FROM rule_conditions ORDER BY condition_id")
    source = __import__("inspect").getsource(condition_report).upper()
    # SQL-shaped, not bare keywords: `sys.path.insert` is not a write.
    for forbidden in ("UPDATE ", "INSERT INTO", "DELETE FROM", "ALTER TABLE"):
        assert forbidden not in source, (
            f"the report contains {forbidden.strip()}; §10 says it recommends only")
    after = fetch_all(conn, "SELECT condition_id, contribution_points "
                            "FROM rule_conditions ORDER BY condition_id")
    assert before == after


def test_case_outcomes_are_preaggregated_before_joining(conn):
    """case_outcomes has no uniqueness constraint. Joining it raw would fan the
    fire-rate DENOMINATOR out by the number of dispositions and deflate every rate
    in the view — quietly, and in the direction that flatters the rules."""
    evaluated_before = {r["condition_id"]: r["evaluated"] for r in fetch_all(
        conn, "SELECT condition_id, evaluated FROM v_condition_performance")}

    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO case_outcomes (alert_id, disposition, analyst_id, notes) "
            "SELECT alert_id, 'inconclusive', 'second-analyst', 'duplicate row' "
            "FROM alerts")

    evaluated_after = {r["condition_id"]: r["evaluated"] for r in fetch_all(
        conn, "SELECT condition_id, evaluated FROM v_condition_performance")}
    assert evaluated_after == evaluated_before
