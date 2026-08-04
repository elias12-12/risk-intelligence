"""Week 5 §3 — D1: saving a rule produces an audit trail rather than an overwrite.

The question this closes is the one Part II was written for and the one the
project could not answer until now: *"why was this customer declined on 14
January?"* Every decision has always recorded a `rule_version_set`; nothing had
ever written a definition behind those numbers, and nothing had ever bumped the
counter — seed `0026` repriced T-021 and left it at 1, so decisions from before
and after a reprice that moved a signed-off score are indistinguishable.

The tests are grouped by what would go wrong without them:

  * **the counter** — a bump on a real edit, and NO bump on a save that changed
    nothing, because a counter that moves on every keystroke makes the version
    set meaningless in the other direction;
  * **the store** — the previous definition retrievable AS IT WAS, which is the
    whole acceptance criterion;
  * **the transitions** — publish lands in shadow, promotion is its own call
    with its own actor, deletion is retirement, and a purge is refused for
    anything ever published;
  * **the endpoints** — admin only, sharing session 2's validator, and never
    committing a rule without its snapshot.
"""
from __future__ import annotations

import os
from decimal import Decimal

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from glassbox.contract.catalog import ConditionDraft, RuleDraft, read_rule
from glassbox.db import fetch_all, fetch_one, fetch_value
from glassbox.rules.publish import (
    PublishRefused,
    delete_rule,
    promote_rule,
    publish_feature,
    publish_rule,
    retire_rule,
)

ANALYST = {"Authorization": "Bearer analyst-token"}
ADMIN = {"Authorization": "Bearer admin-token"}

ADMIN_ACTOR = "omar.admin"


def draft(rule_id: str = "P-901", **overrides) -> RuleDraft:
    """A valid candidate on a subject the planner can reach, with a real feature."""
    base = dict(
        rule_id=rule_id, name="Card-testing burst at a merchant",
        description="Small-value declines probing card validity",
        subject_type="merchant", execution_mode="async", action="alert",
        review_threshold=Decimal(55),
        conditions=[ConditionDraft(
            condition_group=1, feature_key="merchant_decline_burst",
            operator=">=", threshold_num=Decimal(10),
            contribution_points=Decimal(60), reason_code="CARD_TESTING",
            signal_template="{n} declines at this merchant in the window")],
    )
    base.update(overrides)
    return RuleDraft(**base)


def _versions(conn, rule_id: str) -> list[dict]:
    return fetch_all(
        conn,
        "SELECT version, status, published_by, definition, conditions "
        "  FROM rule_versions WHERE rule_id = %s ORDER BY version",
        (rule_id,))


# ---------------------------------------------------------------- the counter
def test_publishing_a_new_rule_stores_its_definition_at_version_one(conn):
    published = publish_rule(conn, draft(), actor=ADMIN_ACTOR)
    assert published.created is True
    assert published.version == 1
    assert published.status == "shadow", "a new rule lands in shadow, always"

    versions = _versions(conn, "P-901")
    assert len(versions) == 1
    assert versions[0]["published_by"] == ADMIN_ACTOR
    assert versions[0]["status"] == "shadow"
    assert len(versions[0]["conditions"]) == 1

    detail = read_rule(conn, "P-901")
    assert detail.versions_resolve is True


def test_publishing_an_unchanged_rule_does_not_bump_anything(conn):
    """`decisions.rule_version_set` records the version an evaluation READ. A
    console that saves on every keystroke would otherwise produce a hundred
    versions, ninety-nine of them identical, and no way to tell which change was
    the one that mattered."""
    publish_rule(conn, draft(), actor=ADMIN_ACTOR)
    again = publish_rule(conn, draft(), actor="someone.else")

    assert again.version == 1
    assert again.version_bumped is False
    assert len(_versions(conn, "P-901")) == 1
    assert _versions(conn, "P-901")[0]["published_by"] == ADMIN_ACTOR, (
        "a no-op publish must not rewrite who published the definition")


def test_editing_a_rule_bumps_it_and_keeps_the_previous_definition(conn):
    """The acceptance criterion, in one test."""
    publish_rule(conn, draft(), actor=ADMIN_ACTOR)
    edited = publish_rule(
        conn, draft(review_threshold=Decimal(40)), actor="someone.else")

    assert edited.version == 2 and edited.version_bumped is True
    assert fetch_value(conn, "SELECT version AS v FROM rule_definitions "
                             "WHERE rule_id = 'P-901'") == 2

    stored = {v["version"]: v for v in _versions(conn, "P-901")}
    assert stored[1]["definition"]["review_threshold"] == 55, "as it WAS"
    assert stored[2]["definition"]["review_threshold"] == 40
    assert stored[2]["published_by"] == "someone.else"


def test_a_condition_reprice_is_a_definition_change(conn):
    """The change §10 is about, and the one 0026 made without a bump.

    Only the conditions moved here; every column on `rule_definitions` is
    identical. A comparison that looked at the rule row alone would call this
    unchanged, which is exactly how a repricing disappears from the record.
    """
    publish_rule(conn, draft(), actor=ADMIN_ACTOR)
    repriced = draft()
    repriced.conditions[0].contribution_points = Decimal(12)
    published = publish_rule(conn, repriced, actor=ADMIN_ACTOR)

    assert published.version == 2
    stored = {v["version"]: v for v in _versions(conn, "P-901")}
    assert stored[1]["conditions"][0]["contribution_points"] == 60
    assert stored[2]["conditions"][0]["contribution_points"] == 12


def test_a_stored_decision_resolves_to_the_definition_it_was_made_under(conn):
    """D1, on a real decision rather than a constructed one.

    `TXN-48291`'s stored decision records `{"R-114": 1}`. Repricing R-114 now
    produces version 2 — and the decision keeps naming 1, which still resolves,
    to the definition that actually scored it. Before this session both versions
    of R-114 were called 1 and neither resolved to anything.
    """
    decision = fetch_one(
        conn,
        "SELECT decision_id, rule_version_set FROM decisions "
        " WHERE subject_id = 'TXN-48291' ORDER BY decision_id DESC LIMIT 1")
    assert decision["rule_version_set"]["R-114"] == 1

    live = read_rule(conn, "R-114")
    reprice = RuleDraft(
        **{k: v for k, v in live.model_dump().items()
           if k in RuleDraft.model_fields and k != "conditions"},
        conditions=[ConditionDraft(
            condition_group=c.condition_group, feature_key=c.feature_key,
            operator=c.operator, threshold_num=c.threshold_num,
            threshold_text=c.threshold_text,
            contribution_points=(Decimal(5) if c.feature_key == "session_geo_jump_km"
                                 else c.contribution_points),
            reason_code=c.reason_code, signal_template=c.signal_template,
            is_required=c.is_required)
            for c in live.condition_set])
    published = publish_rule(conn, reprice, actor=ADMIN_ACTOR)
    assert published.version == 2

    stored = {v["version"]: v for v in _versions(conn, "R-114")}
    was = {c["feature_key"]: c["contribution_points"] for c in stored[1]["conditions"]}
    now = {c["feature_key"]: c["contribution_points"] for c in stored[2]["conditions"]}
    assert was["session_geo_jump_km"] == 18
    assert now["session_geo_jump_km"] == 5

    unchanged = fetch_one(
        conn, "SELECT rule_version_set FROM decisions WHERE decision_id = %s",
        (decision["decision_id"],))
    assert unchanged["rule_version_set"]["R-114"] == 1


# ---------------------------------------------------------------- transitions
def test_a_new_rule_may_not_be_published_active(conn):
    """Refused, not quietly downgraded. An author who believes they published a
    live rule and did not is in the worst of the three possible states."""
    with pytest.raises(PublishRefused, match="promote"):
        publish_rule(conn, draft(status="active"), actor=ADMIN_ACTOR)
    assert read_rule(conn, "P-901") is None


def test_an_edit_may_not_change_status(conn):
    """Fixing a typo must not be able to start acting on customers."""
    publish_rule(conn, draft(), actor=ADMIN_ACTOR)
    with pytest.raises(PublishRefused, match="status change"):
        publish_rule(conn, draft(status="active", name="Renamed"), actor=ADMIN_ACTOR)
    assert fetch_value(conn, "SELECT status AS s FROM rule_definitions "
                             "WHERE rule_id = 'P-901'") == "shadow"


def test_promotion_records_who_and_publishes_a_version(conn):
    """`status` is part of the definition, so going live IS a definition change.

    A decision made by an acting rule is not replayable against a stored
    definition that says the rule was only watching, so the bump is not
    ceremony — it is what keeps the two decisions distinguishable.
    """
    publish_rule(conn, draft(), actor=ADMIN_ACTOR)
    promoted = promote_rule(conn, "P-901", actor="second.admin")

    assert promoted.status == "active"
    assert promoted.version == 2 and promoted.version_bumped is True
    stored = {v["version"]: v for v in _versions(conn, "P-901")}
    assert stored[1]["status"] == "shadow"
    assert stored[2]["status"] == "active"
    assert stored[2]["published_by"] == "second.admin"


def test_only_a_shadow_rule_can_be_promoted(conn):
    with pytest.raises(PublishRefused, match="only a shadow rule"):
        promote_rule(conn, "R-114", actor=ADMIN_ACTOR)


def test_retiring_is_what_deleting_means(conn):
    """Decision 7. `decisions.action_source_rule` references this table with no
    ON DELETE, so Postgres already refuses to remove a rule that ever acted."""
    retired = retire_rule(conn, "R-114", actor=ADMIN_ACTOR)
    assert retired.status == "inactive"
    assert retired.version == 2, "the status change is published like any other"
    assert read_rule(conn, "R-114").takes_action is False

    with conn.cursor() as cur:
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            cur.execute("DELETE FROM rule_definitions WHERE rule_id = 'R-114'")
    conn.rollback()


def test_a_published_rule_cannot_be_purged(conn):
    publish_rule(conn, draft(), actor=ADMIN_ACTOR)
    with pytest.raises(PublishRefused, match="published version"):
        delete_rule(conn, "P-901")


def test_a_draft_that_never_published_can_be_purged(conn):
    """The one honest hard-delete: a row somebody typed straight into the table
    and no stored decision has ever pointed at."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO rule_definitions (rule_id, name, subject_type, "
            "execution_mode, action, review_threshold, status, created_by) "
            "VALUES ('P-902', 'Never published', 'transaction', 'inline_sync', "
            "'alert', 50, 'shadow', 'test')")
        cur.execute(
            "INSERT INTO rule_conditions (rule_id, feature_key, operator, "
            "threshold_num, contribution_points) "
            "VALUES ('P-902', 'card_txn_count_24h', '>=', 99, 40)")

    delete_rule(conn, "P-902")
    assert read_rule(conn, "P-902") is None
    assert fetch_value(conn, "SELECT count(*) AS n FROM rule_conditions "
                             "WHERE rule_id = 'P-902'") == 0, "conditions cascade"


# ---------------------------------------------------------------- features
def test_publishing_a_feature_spec_is_idempotent_until_the_spec_moves(conn):
    """The other half of D1. `feature_version_set` names spec versions, and a
    catalog row edited by seed moved without anything recording that it did."""
    first = publish_feature(conn, "merchant_decline_burst", actor=ADMIN_ACTOR)
    again = publish_feature(conn, "merchant_decline_burst", actor=ADMIN_ACTOR)
    assert first == again == 1
    assert fetch_value(conn, "SELECT count(*) AS n FROM feature_catalog_versions "
                             "WHERE feature_key = 'merchant_decline_burst'") == 1

    with conn.cursor() as cur:
        cur.execute("UPDATE feature_catalog SET window_spec = '45m' "
                    " WHERE feature_key = 'merchant_decline_burst'")
    bumped = publish_feature(conn, "merchant_decline_burst", actor=ADMIN_ACTOR)

    assert bumped == 2
    stored = {r["spec_version"]: r["definition"] for r in fetch_all(
        conn, "SELECT spec_version, definition FROM feature_catalog_versions "
              "WHERE feature_key = 'merchant_decline_burst'")}
    assert stored[1]["window_spec"] != stored[2]["window_spec"] == "45m"


def test_every_seeded_feature_was_backfilled(conn):
    """Seed 0031. A spec version with no definition behind it is the same gap on
    the feature side, and `feature_version_set` is on every decision."""
    missing = fetch_value(
        conn,
        "SELECT count(*) AS n FROM feature_catalog f "
        "  LEFT JOIN feature_catalog_versions v "
        "         ON v.feature_key = f.feature_key AND v.spec_version = f.spec_version "
        " WHERE v.feature_key IS NULL")
    assert missing == 0


# ---------------------------------------------------------------- the endpoints
@pytest.fixture(scope="module")
def client(built_database):
    os.environ["GLASSBOX_DSN"] = built_database
    from glassbox.api.app import app
    with TestClient(app) as c:
        yield c


@pytest.fixture
def clean_rules(built_database):
    """Remove every rule these HTTP tests published.

    They commit — a write endpoint that did not would be a write endpoint that
    does nothing — and the session database is shared with every other module,
    so a rule left behind would be loaded by the next engine run and change
    somebody else's numbers.
    """
    yield
    with psycopg.connect(built_database, row_factory=dict_row, autocommit=True) as c:
        c.execute("DELETE FROM rule_versions WHERE rule_id LIKE 'P-9%'")
        c.execute("DELETE FROM rule_definitions WHERE rule_id LIKE 'P-9%'")


def _body(**overrides) -> dict:
    return draft(**overrides).model_dump(mode="json")


def test_authoring_a_rule_requires_the_admin_role(client, clean_rules):
    assert client.post("/rules", json=_body()).status_code == 401
    assert client.post("/rules", json=_body(), headers=ANALYST).status_code == 403
    assert client.post("/rules", json=_body(), headers=ADMIN).status_code == 201


def test_the_endpoint_returns_a_rule_that_resolves_and_does_not_act(client, clean_rules):
    created = client.post("/rules", json=_body(), headers=ADMIN).json()
    assert created["status"] == "shadow"
    assert created["takes_action"] is False
    assert created["evaluated"] is True
    assert created["versions_resolve"] is True
    assert created["versions"][0]["published_by"] == ADMIN_ACTOR


def test_the_write_path_shares_session_twos_validator(client, clean_rules):
    """A typo'd operator is the case D4 was written about: it produces a rule
    that never fires, never errors, and looks like a rule finding nothing. The
    same `ensure_valid` the what-if calls refuses it here, with every rejection
    rather than the first."""
    body = _body()
    body["conditions"][0]["operator"] = ">=="
    response = client.post("/rules", json=body, headers=ADMIN)
    assert response.status_code == 422
    assert any("operator" in r["field"] for r in response.json()["detail"])
    assert client.get("/rules/P-901").status_code == 404, "nothing was written"


def test_creating_a_rule_that_exists_is_a_conflict(client, clean_rules):
    client.post("/rules", json=_body(), headers=ADMIN)
    conflict = client.post("/rules", json=_body(), headers=ADMIN)
    assert conflict.status_code == 409
    assert "PUT" in conflict.json()["detail"]


def test_editing_over_http_bumps_the_version(client, clean_rules):
    client.post("/rules", json=_body(), headers=ADMIN)
    edited = client.put("/rules/P-901", json=_body(name="Renamed"), headers=ADMIN)
    assert edited.status_code == 200
    assert edited.json()["version"] == 2
    assert [v["version"] for v in edited.json()["versions"]] == [2, 1]


def test_a_path_and_body_that_disagree_are_refused(client, clean_rules):
    client.post("/rules", json=_body(), headers=ADMIN)
    mismatched = client.put("/rules/P-901", json=_body(rule_id="P-903"), headers=ADMIN)
    assert mismatched.status_code == 422


def test_editing_an_unknown_rule_is_a_404(client, clean_rules):
    assert client.put("/rules/P-909", json=_body(rule_id="P-909"),
                      headers=ADMIN).status_code == 404


def test_promotion_over_http_makes_the_rule_act(client, clean_rules):
    client.post("/rules", json=_body(), headers=ADMIN)
    assert client.post("/rules/P-901/promote", headers=ANALYST).status_code == 403

    promoted = client.post("/rules/P-901/promote", headers=ADMIN)
    assert promoted.status_code == 200
    assert promoted.json()["status"] == "active"
    assert promoted.json()["takes_action"] is True

    twice = client.post("/rules/P-901/promote", headers=ADMIN)
    assert twice.status_code == 409, "an active rule is already acting"


def test_deleting_retires_and_purging_is_refused_once_published(client, clean_rules):
    client.post("/rules", json=_body(), headers=ADMIN)
    retired = client.delete("/rules/P-901", headers=ADMIN)
    assert retired.status_code == 200
    assert retired.json()["status"] == "inactive"
    assert retired.json()["evaluated"] is False

    purge = client.delete("/rules/P-901?purge=true", headers=ADMIN)
    assert purge.status_code == 409
    assert client.get("/rules/P-901").status_code == 200, "still there, and retired"


def test_publishing_a_feature_spec_needs_the_admin_role(client):
    key = "merchant_decline_burst"
    assert client.post(f"/features/{key}/publish").status_code == 401
    assert client.post(f"/features/{key}/publish", headers=ANALYST).status_code == 403

    published = client.post(f"/features/{key}/publish", headers=ADMIN)
    assert published.status_code == 200
    assert published.json()["feature_key"] == key
    assert published.json()["spec_version"] == 1, (
        "idempotent: the spec has not moved since 0031 backfilled it")

    assert client.post("/features/no-such-feature/publish",
                       headers=ADMIN).status_code == 404
