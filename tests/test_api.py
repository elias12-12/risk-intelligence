"""§12 — the two endpoints serve schema-valid payloads."""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from glassbox.contract.models import AlertDetail, AlertSummary


@pytest.fixture(scope="module")
def client(built_database):
    # The API opens its own connections, so it has to point at the test database.
    os.environ["GLASSBOX_DSN"] = built_database
    from glassbox.api.app import app
    with TestClient(app) as c:
        yield c


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_list_alerts_returns_schema_valid_summaries(client):
    response = client.get("/alerts")
    assert response.status_code == 200
    payload = response.json()
    assert payload
    for item in payload:
        AlertSummary.model_validate(item)     # extra='forbid' — no silent growth
    scores = [item["score"] for item in payload]
    assert scores == sorted(scores, reverse=True)


def test_filters_narrow_the_queue(client):
    network = client.get("/alerts", params={"subject_type": "network"}).json()
    assert network and all(a["subject"]["type"] == "network" for a in network)
    high = client.get("/alerts", params={"band": "high"}).json()
    assert all(a["band"] == "high" for a in high)


def test_alert_detail_is_schema_valid(client):
    alert_id = client.get("/alerts").json()[0]["alert_id"]
    response = client.get(f"/alerts/{alert_id}")
    assert response.status_code == 200
    detail = AlertDetail.model_validate(response.json())
    assert sum(s.contribution for s in detail.signals) == detail.score


def test_missing_alert_is_a_404(client):
    assert client.get("/alerts/999999").status_code == 404


def test_the_served_payload_carries_the_explanation(client):
    detail = next(
        client.get(f"/alerts/{a['alert_id']}").json()
        for a in client.get("/alerts").json() if a["subject"]["id"] == "RING-1187"
    )
    assert detail["action"]["taken"] == "hold"
    assert detail["action"]["recommended_text"]
    assert detail["action"]["clear_text"]
    assert len(detail["signals"]) == 4
    assert len(detail["subjects"]) == 5
