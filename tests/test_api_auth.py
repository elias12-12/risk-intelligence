"""Week 5 §1 — the two surfaces that leave a mark, over HTTP.

Reads stay open: every GET in this API was public before Week 5 and still is,
and the suite asserts that rather than leaving it to be noticed. What is
authenticated is the disposition write and the simulation, and the actor that
reaches `case_outcomes.analyst_id` is the authenticated principal rather than
anything the caller put in a body.

These tests commit — the API opens its own connections and a write endpoint that
did not commit would be a write endpoint that does nothing. Whatever they create
is removed afterwards by `clean_outcomes`, because the session database is shared
with every other module and `read_kpis` counts what it finds.
"""
from __future__ import annotations

import os

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from glassbox.api.auth import Principal
from glassbox.contract.dispositions import CaseVerdict
from glassbox.contract.simulation import SimulatedDecision

ANALYST = {"Authorization": "Bearer analyst-token"}
ADMIN = {"Authorization": "Bearer admin-token"}


@pytest.fixture(scope="module")
def client(built_database):
    # The API opens its own connections, so it has to point at the test database.
    os.environ["GLASSBOX_DSN"] = built_database
    from glassbox.api.app import app
    with TestClient(app) as c:
        yield c


@pytest.fixture
def clean_outcomes(built_database):
    """Delete every disposition a test wrote through the API.

    Watermarked on `outcome_id` rather than on the actor, so a test that writes
    as somebody unexpected is still cleaned up.
    """
    with psycopg.connect(built_database, row_factory=dict_row) as c:
        top = c.execute(
            "SELECT COALESCE(max(outcome_id), 0) AS m FROM case_outcomes"
        ).fetchone()["m"]
    yield
    with psycopg.connect(built_database, row_factory=dict_row, autocommit=True) as c:
        c.execute("DELETE FROM case_outcomes WHERE outcome_id > %s", (top,))


def _an_alert_id(client) -> int:
    return client.get("/alerts").json()[0]["alert_id"]


# ---------------------------------------------------------------- identity
def test_roles_are_ordered_not_a_set():
    """Two demo users. `admin` can do everything `analyst` can, and a set-valued
    permission model would be modelling a problem this does not have."""
    analyst = Principal(actor="nadia.analyst", role="analyst")
    admin = Principal(actor="omar.admin", role="admin")
    assert analyst.can("analyst") and not analyst.can("admin")
    assert admin.can("analyst") and admin.can("admin")


def test_me_names_the_actor_and_the_role(client):
    """The console needs this before it can decide whether to render the admin
    surfaces at all."""
    assert client.get("/me", headers=ANALYST).json() == {
        "actor": "nadia.analyst", "role": "analyst"}
    assert client.get("/me", headers=ADMIN).json() == {
        "actor": "omar.admin", "role": "admin"}


@pytest.mark.parametrize("headers", [
    {},
    {"Authorization": "Bearer not-a-real-token"},
    {"Authorization": "Basic YWRtaW46YWRtaW4="},
    {"Authorization": "Bearer"},
])
def test_absent_or_unusable_credentials_are_401(client, headers):
    assert client.get("/me", headers=headers).status_code == 401


def test_reads_stay_open(client):
    """Gating the read surfaces would buy nothing but friction: they publish
    decisions about synthetic subjects, and bootstrap.ps1 calls them plainly."""
    for path in ("/alerts", "/queue", "/kpis", "/health"):
        assert client.get(path).status_code == 200, path


# ---------------------------------------------------------------- dispositions
def test_a_disposition_requires_a_token(client):
    alert_id = _an_alert_id(client)
    response = client.post(f"/alerts/{alert_id}/outcome",
                           json={"disposition": "false_positive"})
    assert response.status_code == 401


def test_a_disposition_records_the_authenticated_actor(client, clean_outcomes):
    alert_id = _an_alert_id(client)
    response = client.post(f"/alerts/{alert_id}/outcome", headers=ANALYST,
                           json={"disposition": "false_positive",
                                 "notes": "card recovered, customer confirmed"})
    assert response.status_code == 201

    verdict = CaseVerdict.model_validate(response.json())
    assert verdict.verdict == "false_positive"
    assert verdict.verdict_source == "analyst"
    assert verdict.worked_by_analyst is True
    assert verdict.history[0].analyst_id == "nadia.analyst"

    # And it is readable without a token, like everything else that is a read.
    fetched = CaseVerdict.model_validate(
        client.get(f"/alerts/{alert_id}/outcome").json())
    assert fetched.verdict == "false_positive"


def test_a_worked_case_leaves_the_queue_over_http(client, clean_outcomes):
    alert_id = client.get("/queue").json()[0]["alert_id"]
    client.post(f"/alerts/{alert_id}/outcome", headers=ANALYST,
                json={"disposition": "confirmed_fraud"})

    assert alert_id not in [e["alert_id"] for e in client.get("/queue").json()]
    worked = client.get("/queue", params={"include_worked": True}).json()
    assert alert_id in [e["alert_id"] for e in worked]


def test_an_admin_may_also_disposition(client, clean_outcomes):
    """Roles are ordered: admin is analyst plus rule authoring, not a different
    person with a different job."""
    alert_id = _an_alert_id(client)
    response = client.post(f"/alerts/{alert_id}/outcome", headers=ADMIN,
                           json={"disposition": "inconclusive"})
    assert response.status_code == 201
    assert response.json()["history"][0]["analyst_id"] == "omar.admin"


def test_a_disposition_outside_the_vocabulary_is_422(client):
    alert_id = _an_alert_id(client)
    response = client.post(f"/alerts/{alert_id}/outcome", headers=ANALYST,
                           json={"disposition": "probably_fine"})
    assert response.status_code == 422


def test_a_disposition_on_a_missing_alert_is_404(client):
    response = client.post("/alerts/999999/outcome", headers=ANALYST,
                           json={"disposition": "false_positive"})
    assert response.status_code == 404


def test_the_body_cannot_smuggle_an_actor(client):
    """`analyst_id` is not a field on the request model, and extra='forbid' turns
    an attempt to set it into a 422 rather than a silently ignored key."""
    alert_id = _an_alert_id(client)
    response = client.post(f"/alerts/{alert_id}/outcome", headers=ANALYST,
                           json={"disposition": "false_positive",
                                 "analyst_id": "somebody.else"})
    assert response.status_code == 422


# ---------------------------------------------------------------- simulation
def test_simulation_requires_a_token(client):
    response = client.post("/simulate/subject", json={
        "subject_type": "transaction", "subject_id": "TXN-48291",
        "lane": "inline_sync"})
    assert response.status_code == 401


def test_simulation_serves_a_schema_valid_decision(client):
    response = client.post("/simulate/subject", headers=ANALYST, json={
        "subject_type": "transaction", "subject_id": "TXN-48291",
        "lane": "inline_sync"})
    assert response.status_code == 200

    published = SimulatedDecision.model_validate(response.json())
    assert published.score == 87
    assert published.action.taken == "challenge"
    assert published.persisted is False
    assert sum(s.contribution for s in published.signals) == published.score


def test_simulation_over_http_writes_nothing(client, built_database):
    """The endpoint commits nothing and the scope rolls back — two independent
    reasons, asserted through the surface a caller actually uses."""
    with psycopg.connect(built_database, row_factory=dict_row) as c:
        before = c.execute("SELECT count(*) AS n FROM decisions").fetchone()["n"]

    client.post("/simulate/subject", headers=ANALYST, json={
        "subject_type": "transaction", "subject_id": "TXN-48291",
        "lane": "inline_sync"})

    with psycopg.connect(built_database, row_factory=dict_row) as c:
        after = c.execute("SELECT count(*) AS n FROM decisions").fetchone()["n"]
    assert after == before


def test_simulating_an_unevaluable_subject_is_404(client):
    response = client.post("/simulate/subject", headers=ANALYST, json={
        "subject_type": "transaction", "subject_id": "TXN-NOPE",
        "lane": "inline_sync"})
    assert response.status_code == 404
