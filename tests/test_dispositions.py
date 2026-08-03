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

ANALYST = "nadia.analyst"


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
