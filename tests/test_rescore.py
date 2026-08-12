"""Session 6 §1 — re-scoring the population from the console.

THE DEFECT THIS CLOSES. Two rules were authored, simulated, published and
promoted through the console, and nothing happened: no alert, no KPI movement.
Both halves of the reason were correct behaviour.

  * `GET /cycle` is a status read. The console called it to render the status
    strip and reloading the page evaluated nothing, ever.
  * `POST /cycle` consumes what has ARRIVED. With every watermark at the
    frontier it reports "nothing has arrived since the last cycle" and stops —
    which is right. The cycle reacts to data. A new rule is not data.

So a newly promoted rule had no verb. `scripts/run_cycle.py --lane async` was
the operation that would have worked, from a terminal, on a machine with Python
— which is to say: not from the console, and not during a demo.

The two tests that carry this module are
`test_a_caught_up_cycle_does_nothing_and_a_rescore_still_evaluates` — the defect
itself, both endpoints called against the same caught-up database — and
`test_a_rescore_scores_a_subject_type_the_cycle_would_have_skipped`, which is
the acceptance criterion in §1: a rule targeting a subject type that has never
been scored produces decisions on the first re-score.
"""
from __future__ import annotations

import os

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from glassbox import config
from glassbox.contract.ingest import CycleReport, RescoreReport
from glassbox.ingest import watermark

ADMIN = {"Authorization": "Bearer admin-token"}
ANALYST = {"Authorization": "Bearer analyst-token"}


@pytest.fixture(scope="module")
def client(built_database):
    # The API opens its own connections, so it has to point at the test database.
    os.environ["GLASSBOX_DSN"] = built_database
    from glassbox.api.app import app
    with TestClient(app) as c:
        yield c


@pytest.fixture
def caught_up_and_cleaned(built_database):
    """A committed caught-up database, restored to exactly this state after.

    Committed rather than rolled back, because the endpoint under test opens its
    own connection and a re-score that did not commit would be a re-score that
    does nothing. `conftest` runs both lanes without advancing their watermarks,
    so the caught-up state every one of these tests reasons about has to be
    established here rather than assumed.
    """
    with psycopg.connect(built_database, row_factory=dict_row, autocommit=True) as c:
        before = {r["stream"]: r["watermark_at"] for r in
                  c.execute("SELECT stream, watermark_at FROM ingest_watermark").fetchall()}
        marks = c.execute(
            """
            SELECT (SELECT COALESCE(max(alert_id), 0)     FROM alerts)            AS alert,
                   (SELECT COALESCE(max(decision_id), 0)  FROM decisions)         AS decision,
                   (SELECT COALESCE(max(execution_id), 0) FROM action_executions) AS execution
            """).fetchone()
        # `watermark.frontier`, never a local `max(occurred_at)`: the frontier is
        # the newest row across transactions, events AND entity_links, and a
        # second definition of it here made this fixture agree with the cycle
        # only for as long as no other module committed an event.
        frontier = watermark.frontier(c)
        for stream in watermark.STREAMS:
            c.execute("UPDATE ingest_watermark SET watermark_at = %s WHERE stream = %s",
                      (frontier, stream))

    yield frontier

    with psycopg.connect(built_database, row_factory=dict_row, autocommit=True) as c:
        # Foreign-key order, then the watermarks back to what the module found.
        c.execute("DELETE FROM action_executions WHERE execution_id > %s", (marks["execution"],))
        c.execute("DELETE FROM alerts WHERE alert_id > %s", (marks["alert"],))
        c.execute("DELETE FROM decisions WHERE decision_id > %s", (marks["decision"],))
        for stream, at in before.items():
            c.execute("UPDATE ingest_watermark SET watermark_at = %s WHERE stream = %s",
                      (at, stream))


# ------------------------------------------------------------------ the defect
def test_a_caught_up_cycle_does_nothing_and_a_rescore_still_evaluates(
        client, caught_up_and_cleaned):
    """Both endpoints, same database, opposite answers — and both correct.

    This is the whole of §1 in one test. If `POST /cycle` ever starts returning
    `ran=True` here, the cycle has stopped being watermark-driven and the two
    endpoints have collapsed into one.
    """
    cycle = CycleReport.model_validate(
        client.post("/cycle", headers=ADMIN).json())
    assert cycle.ran is False
    assert "nothing has arrived" in (cycle.reason or "")

    rescore = RescoreReport.model_validate(
        client.post("/cycle/rescore", headers=ADMIN, params={"lane": "async"}).json())
    assert rescore.totals["evaluations"] > 0, (
        "a re-score consulted the watermark it is supposed to ignore")
    assert rescore.totals["decisions"] > 0


def test_a_rescore_scores_a_subject_type_the_cycle_would_have_skipped(
        client, built_database, caught_up_and_cleaned):
    """§1's acceptance criterion, minus the clicking.

    `RF-401` and `C-301` introduced `customer` and `merchant` subjects — types
    the engine had never scored, because no rule had ever named them. On a
    caught-up database the cycle plans nothing for them however many times it is
    run; a full-population pass is what reaches them.
    """
    with psycopg.connect(built_database, row_factory=dict_row) as c:
        targeted = {r["subject_type"] for r in c.execute(
            "SELECT DISTINCT subject_type FROM rule_definitions "
            "WHERE status = 'active' AND execution_mode = 'async'").fetchall()}
    assert targeted, "no active async rule to re-score"

    client.post("/cycle/rescore", headers=ADMIN, params={"lane": "async"})

    # The run id reaches the row as the evaluation_id's prefix — `ev_{run_id}_{seq}`
    # — because `decisions` has no run column. That is enough to tell this pass's
    # rows from the ones conftest's lane runs left behind.
    with psycopg.connect(built_database, row_factory=dict_row) as c:
        scored = {r["subject_type"] for r in c.execute(
            "SELECT DISTINCT subject_type FROM decisions "
            "WHERE evaluation_id LIKE 'ev_rescore%%'").fetchall()}
    assert targeted <= scored, (
        f"the re-score skipped {targeted - scored}, which is the population pass "
        f"failing to be a population pass")


# ------------------------------------------------------------------- the shape
def test_the_report_says_which_lane_and_how_long(client, caught_up_and_cleaned):
    """The numbers are the proof it did something — §1 asks for counts rather
    than a toast for exactly that reason."""
    report = RescoreReport.model_validate(
        client.post("/cycle/rescore", headers=ADMIN, params={"lane": "async"}).json())

    assert report.lane == "async"
    assert report.as_of >= config.reference_now()
    assert report.duration_ms is not None and report.duration_ms > 0
    assert {"evaluations", "decisions", "alerts"} <= set(report.totals)


def test_the_lane_defaults_to_async(client, caught_up_and_cleaned):
    """The slow lane is the one a promoted rule usually needs, and it is the
    cheap one: ~169 subjects against the inline lane's ~9,850."""
    report = RescoreReport.model_validate(
        client.post("/cycle/rescore", headers=ADMIN).json())
    assert report.lane == "async"


def test_an_unknown_lane_is_refused(client, caught_up_and_cleaned):
    assert client.post("/cycle/rescore", headers=ADMIN,
                       params={"lane": "whenever"}).status_code == 422


def test_a_rescore_advances_the_watermark(client, built_database,
                                          caught_up_and_cleaned):
    """Same state a hand-run `scripts/run_cycle.py` leaves behind, so the next
    background tick sees a consumed population rather than re-scoring it."""
    report = RescoreReport.model_validate(
        client.post("/cycle/rescore", headers=ADMIN, params={"lane": "async"}).json())
    with psycopg.connect(built_database, row_factory=dict_row) as c:
        at = c.execute("SELECT watermark_at AS w FROM ingest_watermark "
                       "WHERE stream = 'async'").fetchone()["w"]
    assert at == report.as_of


def test_a_rescore_binds_at_the_frontier_when_data_arrived_after_now(
        client, built_database, caught_up_and_cleaned):
    """`as_of` binds the point-in-time read, so it cannot sit behind the data.

    `GLASSBOX_NOW` is the dataset's reference instant, but anything that came in
    through `/authorize` afterwards is dated later than it — the demo burst runs
    from 15:00:00 and lands past it. Bound at `reference_now()` alone, a re-score
    would evaluate every subject as of an instant before those charges existed
    and the rules being re-applied would not see them, which is this endpoint's
    own bug in miniature.
    """
    frontier = caught_up_and_cleaned
    report = RescoreReport.model_validate(
        client.post("/cycle/rescore", headers=ADMIN).json())

    assert report.as_of >= frontier, (
        "the re-score bound its feature read behind the newest row in the system")
    assert report.as_of == max(config.reference_now(), frontier)


# --------------------------------------------------------------------- access
def test_rescoring_is_admin_only(client, caught_up_and_cleaned):
    """Re-scoring writes decisions and can raise cases, so it sits with the
    other write paths rather than with the read that renders the status strip."""
    assert client.post("/cycle/rescore").status_code == 401
    assert client.post("/cycle/rescore", headers=ANALYST).status_code == 403
    assert client.post("/cycle/rescore", headers=ADMIN).status_code == 200
