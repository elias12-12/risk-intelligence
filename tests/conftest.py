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
import re
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
from glassbox.engine.outcomes import settle                      # noqa: E402

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
        # §8 is part of the pipeline now: issuing happens inside run_lane, and
        # settling the issued actions is what makes prevention measurable. One
        # pass, exactly as bootstrap.ps1 does it.
        #
        # Note for anyone extending this: settle() dispositions every open case,
        # and §9 suppression only applies to UNDISPOSITIONED cases — so a test
        # that needs to observe suppression has to reopen one. test_hygiene.py
        # does exactly that, and says so.
        settle(conn)
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


DDL = re.compile(r"\b(ALTER|CREATE|DROP|TRUNCATE)\b", re.IGNORECASE)


@pytest.fixture
def no_ddl(monkeypatch):
    """§14's hook: record every statement, and refuse to let DDL pass unnoticed.

    The README claims a new detection pattern costs rows, not code. A paragraph
    cannot establish that, so both extension tests run their whole body under
    this fixture and fail if any statement is DDL.

    It lives here rather than in one of the two test modules because there are
    now two of them, and a second copy of the regex is a second definition of
    "schema change" — the same argument the repo makes about the fire verdict
    being computed in exactly one place.
    """
    seen: list[str] = []
    original = psycopg.Cursor.execute

    def recording(self, query, params=None, **kwargs):
        seen.append(query if isinstance(query, str) else query.decode())
        return original(self, query, params, **kwargs)

    monkeypatch.setattr(psycopg.Cursor, "execute", recording)
    yield seen
    offenders = [s.strip()[:120] for s in seen if DDL.search(s)]
    assert not offenders, f"schema was modified: {offenders}"


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
