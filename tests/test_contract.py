"""§12 — the read contract, frozen, and its two invariants.

Plus its Week-3 siblings. queue.v1 and executions.v1 publish what §8 and §9
produced WITHOUT touching a byte of alert.v1, which is why models.py was left
alone and why alert.v1's digest is pinned below.
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal

import pytest
from pydantic import ValidationError

from glassbox import config
from glassbox.contract.executions import read_executions
from glassbox.contract.models import (
    Action,
    AlertDetail,
    ContractViolation,
    Evidence,
    Signal,
    Subject,
    is_contract_violation,
)
from glassbox.contract.queue import read_queue
from glassbox.contract.read import all_alert_ids, get_alert, list_alerts
from glassbox.db import fetch_all
from glassbox.engine.evaluation import run_lane

import export_contract_schema


@pytest.mark.parametrize("name", sorted(export_contract_schema.CONTRACTS))
def test_the_committed_schema_matches_the_models_byte_for_byte(name):
    """THE freeze, for every published contract.

    Regenerate in memory and compare. Any model change breaks the build, so
    unfreezing a contract is always a visible diff in a pull request rather
    than something that happens by accident on a Tuesday.
    """
    committed = export_contract_schema.path_for(name).read_text(encoding="utf-8")
    assert committed == export_contract_schema.render(name), (
        f"the models and the published {name} schema have diverged. If the change "
        f"is deliberate, it is a NEW version: write the v2 file and keep serving "
        f"v1. Never edit a committed schema to match."
    )


def test_alert_v1_matches_its_pinned_digest():
    """Byte-equality alone is not a freeze.

    Change a model AND re-run the exporter and the test above still passes — the
    freeze was enforced by pull-request review, not by the suite. The digest is
    what makes it real: alert.v1 cannot move without this line moving too, and
    moving this line is the deliberate act.

    A breaking change is alert.v2.schema.json with its own digest. v1 keeps being
    served, and this number keeps holding.
    """
    raw = export_contract_schema.path_for("alert.v1").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == (
        "c661148984e36a4bb2caf1d2b415c53a6caf51d5c9790398123a9265c188ef4c"), (
        "alert.v1 has changed. It is frozen: publish alert.v2.schema.json instead."
    )


def test_every_contract_is_registered_and_every_registration_has_a_file():
    """A schema on disk that nothing generates cannot be verified; a registration
    with no file has never been published."""
    on_disk = {p.name.removesuffix(".schema.json")
               for p in config.CONTRACT_DIR.glob("*.schema.json")}
    assert on_disk == set(export_contract_schema.CONTRACTS)


def test_the_sibling_contracts_do_not_redefine_the_frozen_models():
    """queue.v1 and executions.v1 are SIBLINGS of alert.v1, not successors.

    Subject, Signal, Action and Evidence live inside alert.v1's $defs closure, so
    adding a field to any of them for the queue's benefit would change alert.v1's
    bytes and break the digest above. The siblings import nothing into their own
    $defs; that is why models.py was left untouched this week.
    """
    for name in ("queue.v1", "executions.v1"):
        defs = json.loads(export_contract_schema.render(name))["$defs"]
        assert not {"AlertDetail", "AlertSummary", "Signal", "Evidence"} & set(defs)


@pytest.mark.parametrize("name", sorted(export_contract_schema.CONTRACTS))
def test_the_schema_forbids_unknown_fields(name):
    """extra='forbid' is what stops a contract growing silently."""
    assert '"additionalProperties": false' in export_contract_schema.render(name)


def test_the_queue_is_ordered_by_priority_not_score(conn):
    """§9's own example: a 72 with $40,000 at risk outranks an 88 with $30.

    Score order is the wrong order the moment volume is real, and a queue that
    cannot say WHY it chose an order is the same black box §1 refuses for scores —
    so the three factors and the formula travel with every entry.
    """
    entries = read_queue(conn, limit=500)
    assert len(entries) >= 2

    assert entries == sorted(entries, key=lambda e: (-e.priority, e.alert_id))
    assert all(e.priority_basis for e in entries)
    for e in entries:
        assert e.exposure_factor >= 1.0, "the floor must damp a missing exposure, not zero it"
        # Exactly, to the published precision: a client multiplying the three
        # numbers it was given must land on the number it was given.
        assert abs(e.score_factor * e.exposure_factor * e.recency_factor
                   - e.priority) < 0.001, "the published factors must reproduce the priority"

    # Force §9's case: give the lowest-scoring alert a large exposure and the
    # highest-scoring one a trivial exposure, and the order must invert.
    lowest, highest = min(entries, key=lambda e: e.score), max(entries, key=lambda e: e.score)
    assert lowest.score < highest.score
    with conn.cursor() as cur:
        cur.execute("UPDATE alerts SET exposure_amount = 40000 WHERE alert_id = %s",
                    (lowest.alert_id,))
        cur.execute("UPDATE alerts SET exposure_amount = 30 WHERE alert_id = %s",
                    (highest.alert_id,))
        # Equal event clocks, so recency cannot be what moved it.
        cur.execute("UPDATE alerts SET last_event_at = (SELECT max(last_event_at) FROM alerts)")

    reordered = {e.alert_id: i for i, e in enumerate(read_queue(conn, limit=500))}
    assert reordered[lowest.alert_id] < reordered[highest.alert_id], (
        "exposure must be able to outrank score; otherwise the queue is still "
        "score-ordered with extra columns")


def test_the_queue_publishes_the_fold_count_alert_v1_cannot(conn, ctx):
    """§12's example payload shows triggering_events; alert.v1 has no field for it
    and is frozen, so it rides on queue.v1 instead. That is the sibling mechanism
    doing its job rather than a gap."""
    run_lane(conn, "async", config.reference_now(), run_id="qfold", ctx=ctx)
    entries = {e.subject_id: e for e in read_queue(conn, limit=500)}
    assert entries["RING-1187"].triggering_events >= 2

    detail = get_alert(conn, entries["RING-1187"].alert_id)
    assert not hasattr(detail, "triggering_events")


def test_executions_publish_their_synthetic_flag(conn):
    """A synthetic challenge pass rate presented as a measured one is exactly the
    unearned claim §11 objects to in the console copy. The flag is on the wire so
    that is not a matter of remembering."""
    alert_id = next(a.alert_id for a in list_alerts(conn, limit=200)
                    if a.subject.id == "TXN-48291")
    executions = read_executions(conn, alert_id=alert_id)
    assert executions
    challenges = [e for e in executions if e.action == "challenge"]
    assert challenges
    assert all(e.synthetic for e in challenges)
    assert all(e.latency_seconds and e.latency_seconds > 0 for e in challenges)


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
