"""§10's other half — band cutoffs per subject type, and the honesty of `basis`.

Two things are under test and they are different. One is the arithmetic: the
maximum-gap rule, which is pure and testable without a database. The other is
the claim `score_bands.basis` makes about itself — because that column is the
only place a reader learns whether a cutoff was derived or inherited, and a
plausible-looking uncalibrated number is exactly the failure §10 is about.
"""
from __future__ import annotations

import calibrate_bands

from glassbox.db import fetch_all, fetch_one, fetch_value


# --------------------------------------------------------------------------
# The rule itself
# --------------------------------------------------------------------------

def test_the_cutoff_lands_in_the_widest_empty_region():
    """The shipped transaction ladder, as a pure function.

    Scores cluster at 2/3/8/12 and then at 68/81/87 with nothing between. The
    two widest gaps are 12->68 and 68->81; the midpoints round to 40 and 75.
    """
    ladder = [2, 3, 8, 12, 68, 81, 87]
    assert calibrate_bands.gaps(ladder)[0] == (56, 12, 68)
    assert calibrate_bands.propose(ladder) == {"elevated": 40, "high": 75}


def test_a_percentile_would_have_chosen_differently():
    """Recorded because it is the reason the method is maximum-gap.

    p95 of the scoring population is 12 — every transaction where one condition
    fired would band `elevated`. p99 is 68, which is the veto fixture, and using
    it as the `high` line promotes a case that was signed off as `elevated`.
    """
    scoring = [2] * 153 + [3] * 5 + [8] * 217 + [12] * 22 + [68] + [81] + [87] * 3
    scoring.sort()
    p95 = scoring[int(0.95 * len(scoring))]
    p99 = scoring[int(0.99 * len(scoring))]
    proposal = calibrate_bands.propose(sorted(set(scoring)))
    assert p95 == 12 and p99 == 68
    assert proposal["elevated"] > p95, "a p95 cutoff would band single firings elevated"
    assert proposal["high"] > p99, "a p99 cutoff would promote the veto fixture"


def test_no_proposal_without_two_gaps():
    assert calibrate_bands.propose([58]) is None
    assert calibrate_bands.propose([58, 64]) is None


# --------------------------------------------------------------------------
# What the seeded table says, and whether it is true
# --------------------------------------------------------------------------

def test_the_calibrated_cutoffs_are_the_ones_the_script_recommended(conn):
    bands = {r["band"]: r for r in fetch_all(
        conn, "SELECT band, min_score, basis FROM score_bands "
              "WHERE subject_type = 'transaction'")}
    assert bands["high"]["min_score"] == 75
    assert bands["elevated"]["min_score"] == 40
    assert "maximum-gap" in bands["high"]["basis"]
    assert "UNCALIBRATED" not in bands["high"]["basis"]


def test_every_uncalibrated_cutoff_says_so(conn):
    """The point of the column. A cutoff nobody derived must not read like one
    somebody did — `engine/bands.py` reads this table on every decision, and the
    number looks identical either way."""
    rows = fetch_all(
        conn,
        "SELECT subject_type, band, basis FROM score_bands "
        " WHERE subject_type <> 'transaction' AND band <> 'low'")
    assert rows
    for r in rows:
        assert r["basis"] and "UNCALIBRATED" in r["basis"], (
            f"{r['subject_type']}.{r['band']} carries a cutoff with no honest basis")

    stale = fetch_value(
        conn, "SELECT count(*) FROM score_bands WHERE basis LIKE '%is Week 4%'")
    assert stale == 0, "0018's 'calibration is Week 4' text must not outlive Week 4"


def test_the_basis_is_a_record_not_a_live_query(conn):
    """Seeds run before any cycle has produced a decision.

    An earlier draft derived the scoring counts in `basis` with a subquery over
    `decisions`. On a fresh build that reads zero for every subject type, so the
    column would claim there was no population where the truth is that there was
    not one yet. The counts are literals for that reason.
    """
    account = fetch_one(
        conn, "SELECT basis FROM score_bands "
              "WHERE subject_type='account' AND band='high'")
    assert "1 scoring subject" in account["basis"]
    assert "ACC-2201" in account["basis"], (
        "the basis names the subject it counted, so the claim is checkable")


def test_calibration_moves_no_case_that_already_alerted(conn):
    """A cutoff that reclassifies a signed-off case is a finding to surface, not
    something to absorb. On this dataset the partition is unchanged: the lines
    moved away from the data, not across it."""
    alerted = fetch_all(
        conn,
        """
        SELECT d.subject_id, d.subject_type, d.score, d.band
          FROM decisions d
         WHERE d.alert_id IS NOT NULL
        """)
    assert alerted
    for row in alerted:
        expected = fetch_value(
            conn,
            """
            SELECT band FROM score_bands
             WHERE subject_type = %s AND min_score <= %s
             ORDER BY min_score DESC LIMIT 1
            """, (row["subject_type"], row["score"]))
        assert row["band"] == expected, (
            f"{row['subject_id']} at {row['score']} banded {row['band']} but the "
            f"calibrated table says {expected}")


def test_no_calibrated_cutoff_sits_next_to_an_observed_score(conn):
    """What maximum-gap buys, stated as an assertion rather than a claim."""
    cuts = [r["min_score"] for r in fetch_all(
        conn, "SELECT min_score FROM score_bands "
              "WHERE subject_type='transaction' AND band <> 'low'")]
    scores = [r["score"] for r in fetch_all(
        conn, "SELECT DISTINCT score FROM decisions "
              "WHERE subject_type='transaction' AND score > 0")]
    for cut in cuts:
        assert min(abs(s - cut) for s in scores) >= 5, (
            f"a cutoff at {cut} sits within 5 points of an observed score; the "
            f"partition is then sensitive to a single repricing")


def test_the_calibration_script_never_writes(conn):
    """§10, the same rule condition_report.py follows and for the same reason."""
    before = fetch_all(conn, "SELECT subject_type, band, min_score "
                             "FROM score_bands ORDER BY 1, 2")
    source = __import__("inspect").getsource(calibrate_bands).upper()
    for forbidden in ("UPDATE ", "INSERT INTO", "DELETE FROM", "ALTER TABLE"):
        assert forbidden not in source, (
            f"the calibrator contains {forbidden.strip()}; §10 says it recommends "
            f"only — a human writes the seed")
    after = fetch_all(conn, "SELECT subject_type, band, min_score "
                            "FROM score_bands ORDER BY 1, 2")
    assert before == after
