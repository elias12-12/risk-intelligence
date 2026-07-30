"""§12 — every endpoint serves a schema-valid payload.

Six now, across four contracts: alert.v1 (/alerts), queue.v1 and executions.v1
(/queue, /alerts/{id}/executions), kpis.v1 (/kpis) and explanation.v1
(/alerts/{id}/copilot, /alerts/{id}/report). alert.v1's bytes have not moved
through any of it.
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from glassbox.contract.explanation import CaseReport, CopilotResponse
from glassbox.contract.kpis import KpiSet
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


# ---------------------------------------------------------------- kpis.v1 (§11)

def test_kpis_are_schema_valid_and_name_their_window(client):
    response = client.get("/kpis")
    assert response.status_code == 200
    kpis = KpiSet.model_validate(response.json())
    assert len(kpis.tiles) == 9
    assert all(t.window_start == kpis.window_start for t in kpis.tiles)


def test_a_window_with_no_prior_period_serves_no_deltas(client):
    """The dataset spans 30 days, so a 25-day window has no 25-day predecessor.
    §11: render the tile without a delta rather than against nothing."""
    kpis = KpiSet.model_validate(client.get("/kpis", params={"window_days": 25}).json())
    assert kpis.baseline_available is False
    assert kpis.baseline_absent_reason
    assert all(t.delta_pct is None for t in kpis.tiles)


def test_the_synthetic_tiles_carry_their_flag_over_http(client):
    kpis = KpiSet.model_validate(client.get("/kpis").json())
    fn = next(t for t in kpis.tiles if t.key == "false_negative_rate")
    assert fn.synthetic and fn.caveat


# ---------------------------------------------------------------- explanation.v1 (§13)

def _alert_id(client, subject_id: str) -> int:
    return next(a["alert_id"] for a in client.get("/alerts").json()
                if a["subject"]["id"] == subject_id)


def test_the_copilot_serves_three_chips(client):
    alert_id = _alert_id(client, "TXN-48300")
    response = client.get(f"/alerts/{alert_id}/copilot")
    assert response.status_code == 200
    payload = CopilotResponse.model_validate(response.json())
    assert [a.chip for a in payload.answers] == [
        "why_flagged", "what_would_clear_it", "what_should_i_do_first"]
    assert payload.model_backed is False


def test_the_served_copilot_answer_still_names_the_mitigators(client):
    """§13's acceptance, over HTTP. The validator that enforces it runs on the
    server, so a payload that dropped them would be a 500 and never a 200."""
    alert_id = _alert_id(client, "TXN-48300")
    payload = CopilotResponse.model_validate(
        client.get(f"/alerts/{alert_id}/copilot").json())
    why = next(a for a in payload.answers if a.chip == "why_flagged")
    assert why.mitigating_cited == why.mitigating_total == 3
    assert why.veto_cited == why.veto_total == 1


def test_the_case_report_is_a_draft_on_the_artifact(client):
    alert_id = _alert_id(client, "TXN-48291")
    response = client.get(f"/alerts/{alert_id}/report")
    assert response.status_code == 200
    report = CaseReport.model_validate(response.json())
    assert report.draft and report.draft_notice in report.markdown
    assert report.citations


def test_the_explanation_endpoints_404_on_a_missing_alert(client):
    assert client.get("/alerts/999999/copilot").status_code == 404
    assert client.get("/alerts/999999/report").status_code == 404
