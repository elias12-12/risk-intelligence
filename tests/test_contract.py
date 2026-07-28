"""§12 — the read contract, frozen, and its two invariants."""
from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from glassbox.contract.models import (
    Action,
    AlertDetail,
    ContractViolation,
    Evidence,
    Signal,
    Subject,
    is_contract_violation,
)
from glassbox.contract.read import all_alert_ids, get_alert, list_alerts
from glassbox.db import fetch_all

import export_contract_schema


def test_the_committed_schema_matches_the_models_byte_for_byte():
    """THE freeze.

    Regenerate in memory and compare. Any model change breaks the build, so
    unfreezing the contract is always a visible diff in a pull request rather
    than something that happens by accident on a Tuesday.
    """
    committed = export_contract_schema.SCHEMA_PATH.read_text(encoding="utf-8")
    assert committed == export_contract_schema.render(), (
        "the models and the published schema have diverged. If the change is "
        "deliberate, it is a NEW version: write alert.v2.schema.json and keep "
        "serving v1. Never edit alert.v1.schema.json to match."
    )


def test_the_schema_forbids_unknown_fields():
    """extra='forbid' is what stops the contract growing silently."""
    schema = export_contract_schema.render()
    assert '"additionalProperties": false' in schema


def test_invariants_hold_for_every_alert_in_the_dataset(conn):
    """§12's acceptance says "on every alert in the dataset". This is that test,
    literally: the database view AND a full round-trip through get_alert so the
    server-side validator sees all of them."""
    view_failures = fetch_all(
        conn, "SELECT * FROM v_alert_invariants WHERE NOT sum_ok OR NOT source_rule_ok")
    assert view_failures == []

    ids = all_alert_ids(conn)
    assert ids, "the fixtures must produce alerts for this test to mean anything"
    for alert_id in ids:
        alert = get_alert(conn, alert_id)          # raises ContractViolation if it lies
        assert alert is not None
        assert sum((s.contribution for s in alert.signals), Decimal(0)) == alert.score
        if alert.action.taken != "allow":
            assert alert.action.source_rule


def test_the_validator_rejects_a_score_bar_that_does_not_add_up():
    # Pydantic wraps whatever a model_validator raises, so the assertion is on
    # is_contract_violation rather than on the exception class alone.
    with pytest.raises(ValidationError, match="would not add up") as caught:
        AlertDetail(
            alert_id=1, decision_id=1, subject=Subject(type="transaction", id="X"),
            title="t", score=Decimal(10), band="low", status="open",
            action=Action(taken="allow"), evidence=Evidence(),
            signals=[Signal(rank=1, feature_key="f", contribution=Decimal(3),
                            direction="aggravating", human_text="h")],
        )
    assert is_contract_violation(caught.value)


def test_the_validator_rejects_an_action_nobody_claims():
    with pytest.raises(ValidationError, match="no source_rule") as caught:
        AlertDetail(
            alert_id=1, decision_id=1, subject=Subject(type="transaction", id="X"),
            title="t", score=Decimal(0), band="low", status="open",
            action=Action(taken="block"), evidence=Evidence(), signals=[],
        )
    assert is_contract_violation(caught.value)


def test_the_models_are_frozen_and_closed():
    with pytest.raises(Exception):
        Subject(type="transaction", id="X", unexpected_field=1)
    subject = Subject(type="transaction", id="X")
    with pytest.raises(Exception):
        subject.id = "Y"


def test_the_ring_alert_carries_its_derived_members(conn):
    alert_id = next(
        a.alert_id for a in list_alerts(conn, subject_type="network", limit=10))
    alert = get_alert(conn, alert_id)
    assert {s.id for s in alert.subjects} >= {"ACC-8830", "ACC-7771", "ACC-7702", "ACC-7745"}
    assert any(s.role == "collector" for s in alert.subjects)


def test_the_veto_reaches_the_payload(conn):
    alert_id = next(a.alert_id for a in list_alerts(conn, limit=200)
                    if a.subject.id == "TXN-48300")
    alert = get_alert(conn, alert_id)
    assert alert.action.taken == "monitor"
    assert alert.action.vetoed_by == "T-021"
    assert alert.action.source_rule == "R-114"
    assert any(s.direction == "veto" for s in alert.signals)


def test_evidence_records_what_the_decision_could_see(conn):
    alert_id = next(a.alert_id for a in list_alerts(conn, limit=200)
                    if a.subject.id == "TXN-48291")
    evidence = get_alert(conn, alert_id).evidence
    assert evidence.evaluation_id and evidence.trigger_id == "TXN-48291"
    assert evidence.pit_bound_at is not None
    assert evidence.rule_version_set and evidence.feature_version_set, (
        "feature_version_set must be reconstructible from the store, not merely "
        "asserted by the engine"
    )


def test_dedup_key_matches_the_documented_shape(conn):
    summaries = {a.subject.id: a for a in list_alerts(conn, limit=200)}
    assert summaries["RING-1187"].dedup_key == "network:RING-1187:L-203"
