"""Week 5 §2 — D4: an authored rule now has something to fail against.

Every rejection in this module describes a rule that, before the validator,
would have been accepted and then done **nothing** — no error, no log, no
firing, and a condition ledger showing it simply never matched. That is the
failure mode the whole project is built to refuse, so each one gets a test and
each one is a 422 over HTTP rather than a silent success.

The strongest test here is the first: every rule this repo ships validates
clean. A validator that rejects the working system is a validator nobody can
adopt.
"""
from __future__ import annotations

import os
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from glassbox.contract.catalog import ConditionDraft, RuleDraft
from glassbox.db import fetch_all
from glassbox.rules.validate import RuleInvalid, ensure_valid, normalised, validate_draft

ANALYST = {"Authorization": "Bearer analyst-token"}
ADMIN = {"Authorization": "Bearer admin-token"}


@pytest.fixture(scope="module")
def client(built_database):
    os.environ["GLASSBOX_DSN"] = built_database
    from glassbox.api.app import app
    with TestClient(app) as c:
        yield c


def a_draft(**overrides) -> RuleDraft:
    """A valid card-testing draft. Every test below breaks exactly one thing."""
    base = dict(
        rule_id="C-301", name="Card-testing burst at a merchant",
        subject_type="merchant", execution_mode="async", action="alert",
        review_threshold=Decimal(55),
        conditions=[ConditionDraft(
            condition_group=1, feature_key="merchant_decline_burst",
            operator=">=", threshold_num=Decimal(10),
            contribution_points=Decimal(60), reason_code="CARD_TESTING",
            signal_template="{v} declined authorizations in 10 minutes")],
    )
    conditions = overrides.pop("conditions", None)
    base.update(overrides)
    if conditions is not None:
        base["conditions"] = conditions
    return RuleDraft(**base)


def only_rejection(conn, draft) -> str:
    rejections = validate_draft(conn, draft)
    assert rejections, "this draft was supposed to be rejected"
    return " | ".join(f"{r.field}: {r.reason}" for r in rejections)


# ---------------------------------------------------------------- the baseline
def test_every_rule_this_repo_ships_validates_clean(conn):
    """The test that makes the validator adoptable.

    Reconstructed from the stored rows rather than written out here, so a seed
    that changes a threshold or a price is checked against the validator on the
    next run without anybody remembering to update this file.
    """
    conditions = {}
    for c in fetch_all(conn, "SELECT * FROM rule_conditions ORDER BY condition_id"):
        conditions.setdefault(c["rule_id"], []).append(ConditionDraft(
            condition_group=c["condition_group"], feature_key=c["feature_key"],
            operator=c["operator"], threshold_num=c["threshold_num"],
            threshold_text=c["threshold_text"],
            contribution_points=c["contribution_points"],
            reason_code=c["reason_code"], signal_template=c["signal_template"],
            is_required=c["is_required"]))

    for rule in fetch_all(conn, "SELECT * FROM rule_definitions ORDER BY rule_id"):
        draft = RuleDraft(
            rule_id=rule["rule_id"], name=rule["name"],
            description=rule["description"], subject_type=rule["subject_type"],
            execution_mode=rule["execution_mode"], action=rule["action"],
            review_threshold=rule["review_threshold"],
            prevent_threshold=rule["prevent_threshold"],
            is_veto=rule["is_veto"], combine=rule["combine"],
            status=rule["status"],
            evaluation_lag_seconds=rule["evaluation_lag"].total_seconds(),
            conditions=conditions.get(rule["rule_id"], []))
        assert validate_draft(conn, draft) == [], rule["rule_id"]


def test_the_card_testing_draft_is_accepted(conn):
    assert validate_draft(conn, a_draft()) == []


# ---------------------------------------------------------------- D4 itself
def test_a_typod_operator_is_rejected(conn):
    """THE defect. `conditions.fires()` returns False for an operator it does not
    recognise, so before this the rule was accepted, never fired, and never
    errored."""
    draft = a_draft(conditions=[ConditionDraft(
        feature_key="merchant_decline_burst", operator=">==",
        threshold_num=Decimal(10), contribution_points=Decimal(60))])
    assert "never fires" in only_rejection(conn, draft)


def test_a_numeric_operator_with_no_threshold_is_rejected(conn):
    """The same line of code, reached a different way."""
    draft = a_draft(conditions=[ConditionDraft(
        feature_key="merchant_decline_burst", operator=">=",
        threshold_num=None, contribution_points=Decimal(60))])
    assert "would never fire" in only_rejection(conn, draft)


def test_in_with_nothing_to_match_against_is_rejected(conn):
    draft = a_draft(conditions=[ConditionDraft(
        feature_key="merchant_decline_burst", operator="in",
        threshold_text="  ", contribution_points=Decimal(60))])
    assert "comma-separated" in only_rejection(conn, draft)


def test_equality_with_nothing_to_compare_is_rejected(conn):
    draft = a_draft(conditions=[ConditionDraft(
        feature_key="merchant_decline_burst", operator="=",
        contribution_points=Decimal(60))])
    assert "needs something to compare against" in only_rejection(conn, draft)


# ---------------------------------------------------------------- vocabularies
def test_an_unknown_action_is_rejected(conn):
    assert "ref_action" in only_rejection(conn, a_draft(action="quarantine"))


def test_an_unknown_subject_type_is_rejected(conn):
    assert "ref_subject_type" in only_rejection(conn, a_draft(subject_type="wallet"))


def test_an_unknown_execution_mode_is_rejected(conn):
    assert "ref_execution_mode" in only_rejection(
        conn, a_draft(execution_mode="realtime"))


def test_a_status_outside_the_lifecycle_is_rejected(conn):
    assert "must be one of" in only_rejection(conn, a_draft(status="draft"))


def test_a_combine_outside_and_or_is_rejected(conn):
    assert "must be one of" in only_rejection(conn, a_draft(combine="XOR"))


def test_combine_is_case_insensitive_and_normalised_rather_than_rejected(conn):
    """A validator that quietly repairs is a validator whose rejections cannot be
    predicted — so `normalised` does exactly one thing and says so."""
    assert validate_draft(conn, a_draft(combine="and")) == []
    assert normalised(a_draft(combine="and")).combine == "AND"


def test_an_unregistered_feature_is_rejected(conn):
    draft = a_draft(conditions=[ConditionDraft(
        feature_key="vibes_score", operator=">=", threshold_num=Decimal(1),
        contribution_points=Decimal(10))])
    assert "feature_catalog" in only_rejection(conn, draft)


def test_an_unknown_reason_code_is_rejected(conn):
    draft = a_draft(conditions=[ConditionDraft(
        feature_key="merchant_decline_burst", operator=">=",
        threshold_num=Decimal(10), contribution_points=Decimal(60),
        reason_code="SPICY")])
    assert "ref_reason_code" in only_rejection(conn, draft)


def test_a_rule_with_no_conditions_is_rejected(conn):
    assert "can never fire" in only_rejection(conn, a_draft(conditions=[]))


# ---------------------------------------------------------------- policy
def test_a_prevent_threshold_below_the_review_threshold_is_rejected(conn):
    """§7.3: prevention needs a SEPARATE, HIGHER bar. A wrong alert costs analyst
    minutes; a wrong block costs a customer."""
    draft = a_draft(action="hold", review_threshold=Decimal(70),
                    prevent_threshold=Decimal(50))
    assert "below review_threshold" in only_rejection(conn, draft)


def test_a_prevent_threshold_on_a_non_preventive_action_is_rejected(conn):
    """Inert rather than wrong — and inert settings are how an author comes to
    believe a guardrail is in place when it is not."""
    draft = a_draft(action="alert", prevent_threshold=Decimal(80))
    assert "never be consulted" in only_rejection(conn, draft)


def test_an_inline_rule_reading_a_non_inline_feature_is_rejected(conn):
    """§2.1: a rule is inline ONLY IF every feature it reads is inline-capable."""
    draft = a_draft(subject_type="account", execution_mode="inline_sync",
                    conditions=[ConditionDraft(
                        feature_key="pass_through_ratio", operator=">",
                        threshold_num=Decimal("0.9"),
                        contribution_points=Decimal(30))])
    assert "inline_capable" in only_rejection(conn, draft)


def test_a_mitigator_on_a_feature_with_a_default_is_rejected(conn):
    """The §5 trap `test_degraded.py` enforces across the shipped catalog, now
    enforced for a rule authored at runtime.

    A mitigator that defaults can never be observed ABSENT, so §5 can never strip
    preventive authority for it: the score rises on missing exonerating evidence
    and the system acts on the higher number anyway.
    """
    draft = a_draft(subject_type="transaction", execution_mode="inline_sync",
                    conditions=[ConditionDraft(
                        feature_key="session_geo_jump_km", operator="<",
                        threshold_num=Decimal(10),
                        contribution_points=Decimal(-8))])
    assert "can never be observed ABSENT" in only_rejection(conn, draft)


def test_a_veto_rule_with_no_mitigating_condition_is_rejected(conn):
    """A veto is established by EXONERATING evidence. With none to look for, the
    rule caps nothing, ever — which is the defect §7 was written to correct."""
    assert "can never establish its veto" in only_rejection(conn, a_draft(is_veto=True))


def test_a_subject_type_the_planner_cannot_reach_is_rejected(conn, monkeypatch):
    """In `ref_subject_type`, unreachable by the planner: loaded, never planned,
    never evaluated. Today the two sets are equal, so the case is constructed."""
    import glassbox.rules.validate as validate_mod
    monkeypatch.setattr(validate_mod, "PLANNABLE_SUBJECT_TYPES",
                        frozenset({"transaction"}))
    assert "planner cannot reach it" in only_rejection(conn, a_draft())


# ---------------------------------------------------------------- shape
def test_every_problem_is_reported_at_once(conn):
    """An author fixing errors one round trip at a time is an author who stops
    using the validator."""
    draft = a_draft(action="quarantine", status="draft", combine="XOR",
                    conditions=[ConditionDraft(
                        feature_key="nope", operator="~=",
                        contribution_points=Decimal(1))])
    fields = {r.field for r in validate_draft(conn, draft)}
    assert {"action", "status", "combine", "conditions[0].operator",
            "conditions[0].feature_key"} <= fields


def test_ensure_valid_raises_and_carries_the_rejections(conn):
    with pytest.raises(RuleInvalid) as caught:
        ensure_valid(conn, a_draft(action="quarantine"))
    assert caught.value.rejections
    assert caught.value.rejections[0].as_dict()["field"] == "action"


# ---------------------------------------------------------------- over http
@pytest.mark.parametrize("overrides,expected", [
    ({"action": "quarantine"}, "ref_action"),
    ({"subject_type": "wallet"}, "ref_subject_type"),
    ({"execution_mode": "realtime"}, "ref_execution_mode"),
    ({"status": "draft"}, "must be one of"),
    ({"combine": "XOR"}, "must be one of"),
    ({"action": "hold", "prevent_threshold": 1}, "below review_threshold"),
    ({"prevent_threshold": 90}, "never be consulted"),
    ({"is_veto": True}, "can never establish its veto"),
    ({"conditions": []}, "can never fire"),
    ({"conditions": [{"feature_key": "vibes_score", "operator": ">=",
                      "threshold_num": 1, "contribution_points": 10}]},
     "feature_catalog"),
    ({"subject_type": "account", "execution_mode": "inline_sync",
      "conditions": [{"feature_key": "pass_through_ratio", "operator": ">",
                      "threshold_num": 0.9, "contribution_points": 30}]},
     "inline_capable"),
    ({"subject_type": "transaction", "execution_mode": "inline_sync",
      "conditions": [{"feature_key": "session_geo_jump_km", "operator": "<",
                      "threshold_num": 10, "contribution_points": -8}]},
     "can never be observed ABSENT"),
])
def test_every_rejection_is_a_422_over_http(client, overrides, expected):
    body = a_draft().model_dump(mode="json")
    body.update(overrides)
    response = client.post("/simulate/rule", headers=ADMIN, json={"rule": body})
    assert response.status_code == 422
    assert expected in str(response.json()["detail"])


def test_a_typod_operator_is_a_422_over_http(client):
    """The one that mattered most: today it is a 200 and a rule that does
    nothing."""
    body = a_draft().model_dump(mode="json")
    body["conditions"][0]["operator"] = ">=="
    response = client.post("/simulate/rule", headers=ADMIN, json={"rule": body})
    assert response.status_code == 422
    assert "never fires" in str(response.json()["detail"])


def test_an_unknown_field_in_a_draft_is_a_422(client):
    """`extra='forbid'`, so a misspelled field is refused rather than ignored —
    the same reason a disposition cannot smuggle an analyst_id."""
    body = a_draft().model_dump(mode="json")
    body["revue_threshold"] = 40
    assert client.post("/simulate/rule", headers=ADMIN,
                       json={"rule": body}).status_code == 422
