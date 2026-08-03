"""Week 5 §2 — simulation (b): a candidate rule against history, writing nothing.

Two acceptance criteria carry this endpoint, and they pull in opposite
directions. The positive one: a draft of `C-301` must find the planted
card-testing burst, because a what-if that cannot find a pattern the real engine
finds is a what-if nobody should trust. The negative one: **nothing survives the
call** — and here that includes `rule_definitions` and `rule_conditions`, which
this path writes to for the first time in the project's history.

The replacement case is the one worth reading. Repricing a condition on R-114
inside the sandbox moves `TXN-48291` off its signed-off 87, which is exactly
what an admin needs to see BEFORE the reprice — Week 4's `0026` moved a
signed-off score and the blast radius had to be worked out afterwards.
"""
from __future__ import annotations

import os
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from glassbox import config
from glassbox.contract.catalog import ConditionDraft, RuleDraft
from glassbox.contract.simulation import RuleSimulation, to_rule_simulation
from glassbox.db import fetch_all, fetch_value
from glassbox.engine.simulate import DEFAULT_SAMPLE_CAP, simulate_rule

ANALYST = {"Authorization": "Bearer analyst-token"}
ADMIN = {"Authorization": "Bearer admin-token"}

# Everything the persisting path writes, plus the two tables this path writes to
# that no other simulation touches.
WRITABLE = (
    "rule_definitions", "rule_conditions", "decisions", "alerts",
    "alert_signals", "alert_subjects", "decision_conditions",
    "action_executions", "case_outcomes", "feature_values",
)


@pytest.fixture(scope="module")
def client(built_database):
    os.environ["GLASSBOX_DSN"] = built_database
    from glassbox.api.app import app
    with TestClient(app) as c:
        yield c


def _counts(conn) -> dict[str, int]:
    return {t: fetch_value(conn, f"SELECT count(*) AS n FROM {t}") for t in WRITABLE}


def card_testing_draft(**overrides) -> RuleDraft:
    base = dict(
        rule_id="C-301", name="Card-testing burst at a merchant",
        subject_type="merchant", execution_mode="async", action="alert",
        review_threshold=Decimal(55),
        conditions=[ConditionDraft(
            condition_group=1, feature_key="merchant_decline_burst",
            operator=">=", threshold_num=Decimal(10),
            contribution_points=Decimal(60), reason_code="CARD_TESTING",
            signal_template="{v} declined authorizations at this merchant in 10 minutes")],
    )
    base.update(overrides)
    return RuleDraft(**base)


def repriced_r114(conn, feature_key: str, points: Decimal) -> RuleDraft:
    """R-114 as it is stored, with one condition repriced. The edit case."""
    conditions = [
        ConditionDraft(
            condition_group=c["condition_group"], feature_key=c["feature_key"],
            operator=c["operator"], threshold_num=c["threshold_num"],
            threshold_text=c["threshold_text"],
            contribution_points=(points if c["feature_key"] == feature_key
                                 else c["contribution_points"]),
            reason_code=c["reason_code"], signal_template=c["signal_template"],
            is_required=c["is_required"])
        for c in fetch_all(conn, "SELECT * FROM rule_conditions WHERE rule_id = 'R-114' "
                                 "ORDER BY condition_id")
    ]
    return RuleDraft(
        rule_id="R-114", name="Card-not-present burst", subject_type="transaction",
        execution_mode="inline_sync", action="challenge",
        review_threshold=Decimal(70), prevent_threshold=Decimal(85),
        status="active", conditions=conditions)


# ---------------------------------------------------------------- the finding
def test_a_draft_finds_the_planted_burst(conn):
    """§14's card-testing pattern, detected before it exists as a row."""
    published = to_rule_simulation(simulate_rule(conn, card_testing_draft()))

    assert published.persisted is False
    assert published.mode == "draft"
    assert published.would_fire == 1
    assert published.would_authorise == 1
    assert published.would_carry_action == 1
    assert [e.subject_id for e in published.examples] == ["MER-TEST"]
    assert published.examples[0].score == 60
    assert "declined authorizations" in published.examples[0].signals[0].human_text


def test_the_example_bar_sums_to_its_score(conn):
    """The §1 invariant travels onto this surface too: an example renders a bar,
    and a bar that does not add up is not an explanation."""
    published = to_rule_simulation(simulate_rule(conn, card_testing_draft()))
    for example in published.examples:
        assert sum((s.contribution for s in example.signals), Decimal(0)) == example.score


def test_the_candidate_is_measured_against_planted_ground_truth(conn):
    published = to_rule_simulation(simulate_rule(conn, card_testing_draft()))
    assert published.ground_truth.flagged == 1
    assert published.ground_truth.flagged_on_fraud == 1
    assert published.ground_truth.precision_pct == 100
    assert "meaningless outside it" in published.ground_truth.caveat


def test_per_condition_performance_is_direction_aware(conn):
    """Same measurement §10 makes, over the sample rather than over the ledger:
    an aggravator is scored by its fraud rate, a mitigator by its legitimate
    rate. Scoring a deduction as an accusation ranks the best condition in the
    catalog as the worst."""
    draft = repriced_r114(conn, "session_geo_jump_km", Decimal(18))   # unchanged
    published = to_rule_simulation(simulate_rule(conn, draft, sample_cap=400))

    by_key = {c.feature_key: c for c in published.conditions}
    assert by_key["card_cnp_count"].direction == "aggravating"
    assert all(c.evaluated == published.population.subjects_evaluated
               for c in published.conditions)
    assert all(c.fire_rate_pct is not None for c in published.conditions)


def test_a_condition_id_is_not_published(conn):
    """A draft's conditions are inserted inside the rolled-back scope, so their
    ids are real for one transaction and mean nothing afterwards."""
    published = to_rule_simulation(simulate_rule(conn, card_testing_draft()))
    assert not any(hasattr(c, "condition_id") for c in published.conditions)


# ---------------------------------------------------------------- the edit case
def test_repricing_an_existing_rule_shows_what_would_move(conn):
    """The diff, on the case that matters: Week 4's reprice moved a signed-off
    score and the blast radius was worked out afterwards. This is that answer,
    before the change."""
    draft = repriced_r114(conn, "session_geo_jump_km", Decimal(5))
    published = to_rule_simulation(simulate_rule(conn, draft, sample_cap=400))

    assert published.mode == "replacement"
    moved = {d.subject_id: d for d in published.diff}
    assert "TXN-48291" in moved, "the fixture the reprice actually touches"

    detail = moved["TXN-48291"]
    assert detail.stored_score == 87 and detail.simulated_score == 74
    assert detail.stored_action == "challenge" and detail.simulated_action == "alert"
    assert "score" in detail.change and "action" in detail.change
    assert published.diff_score_changed >= 1 and published.diff_action_changed >= 1


def test_a_rule_that_changes_nothing_produces_an_empty_diff(conn):
    """A diff listing everything that stayed the same is a diff nobody reads."""
    draft = repriced_r114(conn, "session_geo_jump_km", Decimal(18))   # its own price
    published = to_rule_simulation(simulate_rule(conn, draft, sample_cap=400))
    assert published.diff == [] and published.diff_total == 0


# ---------------------------------------------------------------- the sample
def test_the_cap_is_published_as_the_denominator(conn):
    published = to_rule_simulation(
        simulate_rule(conn, repriced_r114(conn, "session_geo_jump_km", Decimal(18)),
                      sample_cap=50))
    population = published.population

    assert population.sample_cap == 50
    assert population.subjects_evaluated == 50
    assert population.subjects_available > 50
    assert population.truncated is True
    assert "most recent" in population.basis
    assert DEFAULT_SAMPLE_CAP == 2000, "WEEK5-PLAN O1, answered"


def test_the_sample_is_the_most_recent_and_is_deterministic(conn):
    """Taking the planner's own prefix would mean the OLDEST subjects for the
    transaction lane and the lowest ids everywhere else — and would exclude every
    planted fixture, which all sit near the reference instant."""
    draft = repriced_r114(conn, "session_geo_jump_km", Decimal(18))
    first = to_rule_simulation(simulate_rule(conn, draft, sample_cap=40))
    second = to_rule_simulation(simulate_rule(conn, draft, sample_cap=40))

    assert first.population.subjects_evaluated == second.population.subjects_evaluated
    assert [d.subject_id for d in first.diff] == [d.subject_id for d in second.diff]
    assert first.distribution.bands == second.distribution.bands


def test_an_untruncated_run_says_so(conn):
    published = to_rule_simulation(simulate_rule(conn, card_testing_draft()))
    assert published.population.truncated is False
    assert published.population.subjects_evaluated == published.population.subjects_available


def test_would_alert_declares_that_hygiene_has_not_run(conn):
    """Folding, restatement and suppression all happen at persist time, and
    nothing is persisted here — so this is an upper bound on new cases and says
    so rather than being read as one."""
    published = to_rule_simulation(simulate_rule(conn, card_testing_draft()))
    assert "upper bound" in published.hygiene_caveat


# ---------------------------------------------------------------- the guarantee
def test_a_rule_what_if_writes_nothing(conn):
    """Including the two tables no other simulation touches."""
    before = _counts(conn)
    to_rule_simulation(simulate_rule(conn, card_testing_draft()))
    to_rule_simulation(simulate_rule(conn, repriced_r114(conn, "session_geo_jump_km",
                                                         Decimal(5)), sample_cap=200))
    assert _counts(conn) == before


def test_the_draft_is_gone_from_the_control_plane_afterwards(conn):
    """Stated separately from the row counts because it is the specific fear:
    `POST /simulate/rule` and `POST /rules` take the same body, and the only
    difference is that one of them rolls back."""
    to_rule_simulation(simulate_rule(conn, card_testing_draft()))
    assert fetch_value(
        conn, "SELECT count(*) AS n FROM rule_definitions WHERE rule_id = 'C-301'") == 0


def test_the_existing_rule_survives_a_replacement_what_if(conn):
    """Replacement mode UPDATEs the definition and DELETEs its conditions, which
    cascades to the ledger. All of it rolls back — but it is the loudest possible
    reason the scope manager is not optional."""
    before = fetch_all(conn, "SELECT * FROM rule_conditions WHERE rule_id = 'R-114' "
                             "ORDER BY condition_id")
    to_rule_simulation(simulate_rule(conn, repriced_r114(conn, "session_geo_jump_km",
                                                         Decimal(5)), sample_cap=100))
    assert fetch_all(conn, "SELECT * FROM rule_conditions WHERE rule_id = 'R-114' "
                           "ORDER BY condition_id") == before


def test_the_engine_still_sees_the_original_rules_after_a_what_if(conn, ctx):
    """The stored decision, re-derived after the sandbox closed, is unmoved."""
    to_rule_simulation(simulate_rule(conn, repriced_r114(conn, "session_geo_jump_km",
                                                         Decimal(5)), sample_cap=100))
    from glassbox.contract.simulation import to_simulation
    from glassbox.engine.simulate import simulate_subject
    after = to_simulation(
        simulate_subject(conn, subject_type="transaction", subject_id="TXN-48291",
                         lane="inline_sync"),
        ctx.rules, as_of=config.reference_now())
    assert after.score == 87


# ---------------------------------------------------------------- over http
def test_the_rule_what_if_is_admin_only(client):
    body = {"rule": card_testing_draft().model_dump(mode="json")}
    assert client.post("/simulate/rule", json=body).status_code == 401
    assert client.post("/simulate/rule", headers=ANALYST, json=body).status_code == 403
    assert client.post("/simulate/rule", headers=ADMIN, json=body).status_code == 200


def test_the_endpoint_serves_a_schema_valid_what_if(client):
    response = client.post("/simulate/rule", headers=ADMIN,
                           json={"rule": card_testing_draft().model_dump(mode="json")})
    published = RuleSimulation.model_validate(response.json())
    assert published.persisted is False
    assert published.would_authorise == 1
    assert published.examples[0].subject_id == "MER-TEST"


def test_the_endpoint_writes_nothing(client, built_database):
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(built_database, row_factory=dict_row) as c:
        before = _counts(c)
    client.post("/simulate/rule", headers=ADMIN,
                json={"rule": card_testing_draft().model_dump(mode="json"),
                      "sample_cap": 100})
    with psycopg.connect(built_database, row_factory=dict_row) as c:
        assert _counts(c) == before
