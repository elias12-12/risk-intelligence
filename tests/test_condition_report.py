"""§10 — the condition-level report, and the mispriced condition it must find.

§10's acceptance names one condition: `country_is_new_for_customer` is +50, sized
backwards so that T-021's points would sum to its displayed 31. Against a
population, that single condition puts every genuine first trip abroad at 50 —
mid-elevated — unless a mitigator happens to fire. A false-positive engine sitting
inside the rule whose stated purpose is demonstrating false-positive avoidance.

The numbers below are re-derived from the view rather than transcribed, except the
price (+50, which is seeded) and the ordering claim (which is the finding).
"""
from __future__ import annotations

from glassbox.db import fetch_all, fetch_one, fetch_value


def _by_feature(conn) -> dict[str, dict]:
    return {r["feature_key"]: r for r in fetch_all(
        conn, "SELECT * FROM v_condition_performance")}


def test_the_report_finds_the_mispriced_condition(conn):
    rows = fetch_all(conn, "SELECT * FROM v_condition_performance "
                           "WHERE direction = 'aggravating' "
                           "  AND points_per_precision_point IS NOT NULL")
    assert rows, "the view orders by cost per precision point, worst first"

    worst = rows[0]
    assert worst["feature_key"] == "country_is_new_for_customer", (
        "§10 names this condition; if something else is now worse, that is a "
        "finding to report, not a test to relax")
    assert worst["priced_points"] == 50
    assert worst["fired"] > 100, "it has to fire often enough for the finding to bite"
    assert float(worst["precision_pct"]) < 15.0

    # The comparison that makes it a misprice rather than merely a weak signal:
    # the other two population-scale aggravators earn far more per point.
    others = [r for r in rows[1:] if r["fired"] >= 20]
    assert others
    assert all(float(worst["points_per_precision_point"])
               > 3 * float(r["points_per_precision_point"]) for r in others), (
        "the whole finding is that this condition costs multiples more per unit of "
        "measured precision than any comparably-sampled aggravator")


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
    LABEL precision, which is the only reason the +50 misprice is findable at all.

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
    explain why last week's identical transaction scored differently."""
    import condition_report

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
