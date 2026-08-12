"""Week 5 §1 — the analyst's verdict, and the provenance that makes it readable.

Two things are under test and they are easy to conflate. One is the WRITE: a
disposition is appended, never updated, and it does not touch the alert
lifecycle. The other is what the write makes VISIBLE — the queue stops offering a
case a person has worked, and the KPI tiles stop claiming every verdict came from
a script the moment one did not.

The second half is the point of migration 0029. Before it, `case_outcomes` had no
provenance column and two tiles asserted their own synthetic-ness in a hardcoded
string, which is a claim a payload makes about itself and never rechecks.
"""
from __future__ import annotations

import re
from datetime import timedelta

import psycopg
import pytest

from glassbox import config
from glassbox.contract.dispositions import (
    DispositionRequest,
    read_verdict,
    write_disposition,
)
from glassbox.contract.kpis import read_kpis
from glassbox.contract.queue import read_queue
from glassbox.db import fetch_all, fetch_one, fetch_value

ANALYST = "jane.analyst"


def _any_alert(conn) -> int:
    return fetch_value(conn, "SELECT min(alert_id) AS a FROM alerts")


def _alert_in_the_kpi_window(conn) -> int:
    """An alert the default 7-day KPI window actually covers.

    The tiles filter on the CASE's event time, not on when it was dispositioned,
    so a verdict written today still lands in January's window if that is when
    the case happened — which is exactly why the caveat can flip at all.
    """
    now = config.reference_now()
    row = fetch_one(
        conn,
        """
        SELECT min(alert_id) AS a FROM v_kpi_cases
         WHERE event_at > %(start)s AND event_at <= %(end)s
        """,
        {"start": now - timedelta(days=7), "end": now},
    )
    assert row["a"] is not None, "no case in the default KPI window"
    return row["a"]


def _tile(kpis, key):
    return next(t for t in kpis.tiles if t.key == key)


# ---------------------------------------------------------------- provenance
def test_every_disposition_in_a_built_database_is_stamped_synthetic(conn):
    """The settler is the only writer until a person shows up, and 0029 makes it
    say so in a column rather than in the spelling of `analyst_id`."""
    sources = {r["source"] for r in fetch_all(
        conn, "SELECT DISTINCT source FROM case_outcomes")}
    assert sources == {"synthetic"}
    assert fetch_value(
        conn, "SELECT count(*) AS n FROM case_outcomes WHERE source = 'analyst'") == 0


def test_the_vocabulary_is_enforced_in_the_database_too(conn):
    """A Pydantic Literal guards the endpoint; the CHECK guards everything else.

    A fifth disposition value would not raise anywhere — `v_kpi_cases` classifies
    on four literals, so it would land in neither is_true_positive nor
    is_false_positive and quietly deflate every rate computed over it.
    """
    alert_id = _any_alert(conn)
    with pytest.raises(psycopg.errors.CheckViolation):
        # A savepoint, so the poisoned transaction does not take the rest of the
        # test with it.
        with conn.transaction():
            conn.execute(
                "INSERT INTO case_outcomes (alert_id, disposition) VALUES (%s, %s)",
                (alert_id, "probably_fine"),
            )


# ---------------------------------------------------------------- the write
def test_a_disposition_is_appended_never_updated(conn):
    alert_id = _any_alert(conn)
    before = fetch_value(
        conn, "SELECT count(*) AS n FROM case_outcomes WHERE alert_id = %s", (alert_id,))

    write_disposition(conn, alert_id,
                      DispositionRequest(disposition="false_positive"), actor=ANALYST)

    after = fetch_value(
        conn, "SELECT count(*) AS n FROM case_outcomes WHERE alert_id = %s", (alert_id,))
    assert after == before + 1


def test_the_actor_comes_from_the_principal_not_the_body(conn):
    alert_id = _any_alert(conn)
    verdict = write_disposition(
        conn, alert_id, DispositionRequest(disposition="confirmed_fraud"),
        actor=ANALYST)
    assert verdict.history[0].analyst_id == ANALYST
    assert verdict.history[0].source == "analyst"


def test_the_latest_verdict_wins_and_the_correction_survives(conn):
    """Append-only, latest wins — and `v_kpi_cases` agrees with the read model.

    The view used to take the FIRST disposition, which was right while the only
    writer wrote each case once. With a human writer it would publish the verdict
    most likely to be wrong while storing the right one.
    """
    alert_id = _any_alert(conn)
    write_disposition(conn, alert_id,
                      DispositionRequest(disposition="confirmed_fraud"), actor=ANALYST)
    verdict = write_disposition(
        conn, alert_id, DispositionRequest(disposition="false_positive",
                                           notes="second look: legitimate"),
        actor=ANALYST)

    assert verdict.verdict == "false_positive"
    assert verdict.verdict_source == "analyst"
    assert verdict.dispositions >= 3          # the synthetic one, plus both of ours
    assert {"confirmed_fraud", "false_positive"} <= {
        d.disposition for d in verdict.history}

    published = fetch_one(
        conn, "SELECT disposition, disposition_source FROM v_kpi_cases "
              "WHERE alert_id = %s", (alert_id,))
    assert published["disposition"] == "false_positive"
    assert published["disposition_source"] == "analyst"


def test_the_triage_clock_still_runs_to_the_first_disposition(conn):
    """A correction hours later does not mean triage took hours longer.

    The verdict is the latest row and the clock is the earliest one, on purpose.
    """
    alert_id = _any_alert(conn)
    before = fetch_value(
        conn, "SELECT decided_at AS d FROM v_kpi_cases WHERE alert_id = %s", (alert_id,))
    write_disposition(conn, alert_id,
                      DispositionRequest(disposition="inconclusive"), actor=ANALYST)
    after = fetch_value(
        conn, "SELECT decided_at AS d FROM v_kpi_cases WHERE alert_id = %s", (alert_id,))
    assert after == before


def test_a_disposition_does_not_touch_the_alert_lifecycle(conn):
    """`alerts.status` is engine-owned. Two writers on one column is how a status
    stops meaning anything."""
    alert_id = _any_alert(conn)
    before = fetch_value(
        conn, "SELECT status AS s FROM alerts WHERE alert_id = %s", (alert_id,))
    write_disposition(conn, alert_id,
                      DispositionRequest(disposition="confirmed_fraud"), actor=ANALYST)
    after = fetch_value(
        conn, "SELECT status AS s FROM alerts WHERE alert_id = %s", (alert_id,))
    assert before == after == "open"


def test_an_undispositioned_case_is_a_verdict_of_none_not_an_error(conn):
    """Every fixture case carries a synthetic verdict, so this is constructed —
    but it is the state the queue is built on and it must have a shape."""
    conn.execute("DELETE FROM case_outcomes WHERE alert_id = %s", (_any_alert(conn),))
    verdict = read_verdict(conn, _any_alert(conn))
    assert verdict.verdict is None
    assert verdict.dispositions == 0
    assert verdict.worked_by_analyst is False


# ---------------------------------------------------------------- the queue
def test_a_synthetically_closed_case_is_still_in_the_queue(conn):
    """The filter is `source = 'analyst'`, not "has any disposition".

    `scripts/resolve_actions.py` dispositions every open case in one pass, so a
    queue keyed on any disposition would be empty after a normal bootstrap. A
    fixture script closing a case is not an analyst having worked it.
    """
    assert fetch_value(conn, "SELECT count(*) AS n FROM case_outcomes") > 0
    assert read_queue(conn), "the queue emptied itself on synthetic dispositions"


def test_a_case_a_person_worked_leaves_the_queue_and_can_be_asked_for(conn):
    alert_id = read_queue(conn)[0].alert_id
    write_disposition(conn, alert_id,
                      DispositionRequest(disposition="false_positive"), actor=ANALYST)

    assert alert_id not in [e.alert_id for e in read_queue(conn)]

    reopened = [e for e in read_queue(conn, include_worked=True)
                if e.alert_id == alert_id]
    assert len(reopened) == 1
    assert reopened[0].worked_by_analyst is True


# ---------------------------------------------------------------- the tiles
def test_the_tiles_stop_claiming_every_verdict_is_synthetic(conn):
    """D3, closed and demonstrated rather than described.

    Before: two tiles carried a hardcoded sentence asserting every disposition
    here was written by a script. After one human verdict that sentence is false,
    and the payload has no way to know — which is precisely the class of claim
    `kpis.py`'s own rule 4 forbids.
    """
    before = read_kpis(conn)
    outcomes_before = _tile(before, "validation_outcomes")
    assert outcomes_before.synthetic is True
    assert "Every dispositioned case" in outcomes_before.caveat

    write_disposition(conn, _alert_in_the_kpi_window(conn),
                      DispositionRequest(disposition="confirmed_fraud"), actor=ANALYST)

    after = read_kpis(conn)
    outcomes_after = _tile(after, "validation_outcomes")
    assert "Every dispositioned case" not in outcomes_after.caveat
    assert "carry a person's" in outcomes_after.caveat
    # Still synthetic overall — most verdicts here are still the script's, and
    # the tile now says how many rather than making a claim about all of them.
    assert outcomes_after.synthetic is True


def test_the_triage_caveat_keeps_its_clock_clause(conn):
    """The provenance note is shared; the sentence about the clock belongs to
    this tile alone and must survive the derivation."""
    write_disposition(conn, _alert_in_the_kpi_window(conn),
                      DispositionRequest(disposition="confirmed_fraud"), actor=ANALYST)
    triage = _tile(read_kpis(conn), "median_triage_time")
    assert "first disposition" in triage.caveat


def test_a_fully_human_window_carries_no_synthetic_caveat_at_all(conn):
    """The other end of the derivation: when nothing in the window is synthetic,
    there is no caveat to make. A tile that always carries one is decoration."""
    conn.execute("UPDATE case_outcomes SET source = 'analyst', "
                 "analyst_id = %s WHERE source = 'synthetic'", (ANALYST,))
    outcomes = _tile(read_kpis(conn), "validation_outcomes")
    assert outcomes.caveat is None
    assert outcomes.synthetic is False


# ------------------------------------------- Session 6 §2: the views agree
#
# Week 5 moved `v_kpi_cases` to latest-wins and left the three views that had
# COPIED its verdict CTE on first-wins. For eight weeks, dispositioning a case
# moved the false-positive rate, validation outcomes and median triage while
# per-rule precision, prevention FP/TP and condition precision stayed frozen on
# whatever the synthetic settler had written — a half-responsive screen, in the
# direction that made a correction look ignored.
#
# Two tests, because the defect has two faces. One asserts the DATA agrees. The
# other asserts the SOURCE does: the mechanism here is a CTE duplicated four
# times on purpose, and duplication that drifted once will drift again the next
# time someone edits one copy.

VERDICT_VIEWS = ("v_kpi_cases", "v_kpi_rule_attribution", "v_kpi_executions")


def _alert_in_every_verdict_view(conn) -> int:
    """A case all three disposition-carrying views actually have a row for.

    `v_kpi_executions` is keyed on an issued action and `v_kpi_rule_attribution`
    on a rule that asserted evidence, so neither covers every alert. Picking
    `min(alert_id)` would let this test pass by finding nothing to disagree with.
    """
    row = fetch_one(conn, """
        SELECT c.alert_id AS a
          FROM v_kpi_cases c
         WHERE EXISTS (SELECT 1 FROM v_kpi_rule_attribution r
                        WHERE r.alert_id = c.alert_id)
           AND EXISTS (SELECT 1 FROM v_kpi_executions x
                        WHERE x.alert_id = c.alert_id)
         ORDER BY c.alert_id
         LIMIT 1
    """)
    assert row and row["a"] is not None, "no case is covered by all three views"
    return row["a"]


def _verdicts_reported_by(conn, view: str, alert_id: int) -> set[str]:
    # `view` comes from VERDICT_VIEWS, never from a caller's input.
    return {r["disposition"] for r in fetch_all(
        conn, f"SELECT DISTINCT disposition FROM {view} WHERE alert_id = %s",
        (alert_id,))}


def _alert_precision(conn, condition_ids: list[int]) -> dict[int, object]:
    return {r["condition_id"]: r["alert_precision_pct"] for r in fetch_all(
        conn,
        "SELECT condition_id, alert_precision_pct FROM v_condition_performance "
        "WHERE condition_id = ANY(%s)", (condition_ids,))}


def test_all_the_verdict_views_report_the_later_disposition(conn):
    """Two dispositions, opposite verdicts, one case — every view says the same.

    This is the assertion that was false in the shipped build: `v_kpi_cases`
    answered `false_positive` while the other two answered `confirmed_fraud`.
    """
    alert_id = _alert_in_every_verdict_view(conn)

    write_disposition(conn, alert_id,
                      DispositionRequest(disposition="confirmed_fraud"), actor=ANALYST)
    write_disposition(conn, alert_id,
                      DispositionRequest(disposition="false_positive",
                                         notes="second look: legitimate"),
                      actor=ANALYST)

    for view in VERDICT_VIEWS:
        assert _verdicts_reported_by(conn, view, alert_id) == {"false_positive"}, (
            f"{view} still reports an earlier verdict — its copy of the verdict "
            f"CTE has drifted back to first-wins")


def test_condition_precision_notices_an_analysts_correction(conn):
    """`v_condition_performance` aggregates, so it carries no `disposition`
    column to compare. The verdict reaches it through `alert_precision_pct`, and
    that is what has to move.

    Flipping confirmed_fraud → false_positive leaves `fired_on_cases` alone (both
    are verdicts) and drops `fired_on_confirmed` by one for every condition that
    fired on this case, so precision must fall. Under first-wins nothing moves at
    all, which is the failure this catches.
    """
    alert_id = _alert_in_every_verdict_view(conn)
    fired = [r["condition_id"] for r in fetch_all(conn, """
        SELECT DISTINCT dc.condition_id
          FROM decision_conditions dc
          JOIN decisions d ON d.decision_id = dc.decision_id
         WHERE d.alert_id = %s AND dc.fired
    """, (alert_id,))]
    assert fired, "the chosen case fired no conditions, so there is nothing to measure"

    write_disposition(conn, alert_id,
                      DispositionRequest(disposition="confirmed_fraud"), actor=ANALYST)
    before = _alert_precision(conn, fired)
    write_disposition(conn, alert_id,
                      DispositionRequest(disposition="false_positive"), actor=ANALYST)
    after = _alert_precision(conn, fired)

    assert before != after, (
        "v_condition_performance did not notice the correction — its verdict CTE "
        "has drifted back to first-wins")
    assert all(after[c] <= before[c] for c in before
               if before[c] is not None and after[c] is not None), (
        "a case moving from fraud to false positive cannot raise precision")


def test_every_copy_of_the_verdict_cte_orders_the_same_way():
    """The source-level half, and the one that would have caught this in Week 5.

    Four views carry their own copy of the verdict CTE — deliberately, because a
    view that DROPs cannot be replaced while another depends on it. That
    duplication is fine as long as something checks the copies still agree.
    Nothing did. This does.
    """
    pattern = re.compile(r"array_agg\(disposition ORDER BY ([^)]+)\)")
    orderings: dict[str, set[str]] = {}
    for name in VERDICT_VIEWS + ("v_condition_performance",):
        sql = (config.VIEWS_DIR / f"{name}.sql").read_text(encoding="utf-8")
        found = {" ".join(m.split()) for m in pattern.findall(sql)}
        assert found, f"{name} no longer contains a verdict CTE this test can read"
        orderings[name] = found

    distinct = set().union(*orderings.values())
    assert distinct == {"decided_at DESC, outcome_id DESC"}, (
        f"the verdict CTE has drifted between view files: {orderings}")
