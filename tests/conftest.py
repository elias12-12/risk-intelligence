"""Test harness.

A session fixture drops and recreates GLASSBOX_TEST_DSN, migrates, seeds, loads
the fixtures, runs the feature layer and runs both lanes — once, in ~30s. State
is never inherited from a previous run.

Every test then gets a connection whose transaction is ROLLED BACK at teardown,
so tests are order-independent and a test that mutates rules, catalog rows or
feature values cannot leak into the next one. That is what lets the §4 and §7
tests demonstrate demotion and staleness against the real fixtures instead of
against a second, parallel set of them.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

from glassbox import config                                      # noqa: E402
from glassbox.engine.evaluation import EngineContext, run_lane   # noqa: E402

import reset_db                                                  # noqa: E402


@pytest.fixture(scope="session")
def test_dsn() -> str:
    dsn = config.test_dsn()
    assert dsn != config.dsn(), (
        "GLASSBOX_TEST_DSN must not be the dev database — the session fixture "
        "DROPs it"
    )
    return dsn


@pytest.fixture(scope="session", autouse=True)
def built_database(test_dsn: str) -> str:
    reset_db.build(test_dsn, verbose=False)
    with psycopg.connect(test_dsn, row_factory=dict_row) as conn:
        ctx = EngineContext.load(conn)
        for lane in ("inline_sync", "async"):
            run_lane(conn, lane, config.reference_now(), run_id="testrun", ctx=ctx)
        conn.commit()
    return test_dsn


@pytest.fixture
def conn(built_database: str):
    connection = psycopg.connect(built_database, row_factory=dict_row)
    try:
        yield connection
    finally:
        connection.rollback()
        connection.close()


@pytest.fixture
def ctx(conn) -> EngineContext:
    return EngineContext.load(conn)


@pytest.fixture(scope="session")
def expected() -> dict:
    return json.loads((config.FIXTURES_DIR / "expected_scores.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def cases(expected) -> dict[str, dict]:
    return {c["subject_id"]: c for c in expected["cases"]}


# ---------------------------------------------------------------- helpers
def decision_for(conn, subject_type: str, subject_id: str) -> dict | None:
    from glassbox.db import fetch_one
    return fetch_one(
        conn,
        "SELECT * FROM decisions WHERE subject_type=%s AND subject_id=%s "
        "ORDER BY decision_id DESC LIMIT 1",
        (subject_type, subject_id),
    )


def request_for(conn, ctx: EngineContext, lane: str, subject_type: str,
                subject_id: str, run_id: str = "probe"):
    """Rebuild the exact EvaluationRequest the cycle would have produced."""
    from glassbox.engine.evaluation import plan_evaluations
    plans = plan_evaluations(conn, ctx, lane, config.reference_now(), run_id, [subject_id])
    matching = [p for p in plans if p.subject.type == subject_type and p.subject.id == subject_id]
    assert matching, f"no {lane} evaluation planned for {subject_type}:{subject_id}"
    return matching[0]
