"""Week 5 §2 — catalog.v1, the control plane read.

The admin surface is the screen where a number gets changed, so the thing worth
testing hardest is not that the rows come back — it is that the *measurement*
next to each condition is the same measurement §10 made, and that a condition
with nothing behind it says so instead of publishing zeroes.
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from glassbox.contract.catalog import (
    FeatureView,
    ReferenceVocabulary,
    RuleDetail,
    RuleSummary,
    read_features,
    read_reference,
    read_rule,
    read_rules,
)
from glassbox.db import fetch_all, fetch_value
from glassbox.engine.conditions import SUPPORTED_OPERATORS


@pytest.fixture(scope="module")
def client(built_database):
    os.environ["GLASSBOX_DSN"] = built_database
    from glassbox.api.app import app
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------- rules
def test_every_shipped_rule_is_listed(conn):
    rules = {r.rule_id: r for r in read_rules(conn)}
    assert set(rules) >= {"R-114", "L-203", "S-077", "T-021"}
    assert rules["T-021"].is_veto is True
    assert rules["R-114"].prevent_threshold == 85
    assert rules["L-203"].evaluation_lag_seconds == 900   # §4's fifteen minutes


def test_an_inactive_rule_is_still_listed(conn):
    """A retired rule is exactly what an admin needs in order to un-retire it —
    and it cannot be deleted anyway, because decisions reference it."""
    with conn.cursor() as cur:
        cur.execute("UPDATE rule_definitions SET status = 'inactive' WHERE rule_id = 'S-077'")

    listed = {r.rule_id: r for r in read_rules(conn)}
    assert "S-077" in listed
    assert listed["S-077"].evaluated is False
    assert listed["S-077"].takes_action is False
    assert "cannot be deleted" in (listed["S-077"].status_caveat or "")


def test_a_shadow_rule_says_that_shadow_does_not_yet_mean_anything(conn):
    """D2, published rather than buried.

    A rule authored `shadow` scores, alerts and issues preventive executions
    exactly like an active one, because `Rule.status` is loaded and never read
    again. Session 3 builds the gate; until it does, the surface says so instead
    of implying a guarantee that is not there.
    """
    with conn.cursor() as cur:
        cur.execute("UPDATE rule_definitions SET status = 'shadow' WHERE rule_id = 'R-114'")

    shadowed = next(r for r in read_rules(conn) if r.rule_id == "R-114")
    assert shadowed.evaluated is True
    assert shadowed.takes_action is True, (
        "if this flips to False, the shadow gate has landed — update the caveat "
        "and this test with it")
    assert "never read again" in (shadowed.status_caveat or "")


def test_rule_detail_carries_the_measurement_beside_the_price(conn):
    """§10's finding, on the screen where the price is edited."""
    detail = read_rule(conn, "T-021")
    assert detail is not None
    priced = {c.feature_key: c for c in detail.condition_set}

    country = priced["country_is_new_for_customer"]
    assert country.direction == "aggravating"
    assert country.contribution_points == 12, "seed 0026's reprice"
    assert country.performance is not None
    assert country.performance.fired > 0
    assert country.performance.precision_pct is not None
    assert country.performance.points_per_precision_point is not None

    # Direction-aware, exactly as v_condition_performance measures it: the
    # mitigator that fires on 9,562 legitimate transactions is the BEST condition
    # in the catalog, not the worst.
    chip = priced["entry_mode_chip_pin"]
    assert chip.direction == "mitigating"
    assert chip.performance.precision_pct == 100


def test_a_condition_that_was_never_evaluated_says_so_rather_than_publishing_zero(conn):
    """A newly authored rule has no ledger behind it. `fire_rate: 0%` would read
    as a measurement of a bad condition rather than as an absence of evidence."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO rule_definitions (rule_id, name, subject_type, "
            "execution_mode, action, review_threshold, created_by) "
            "VALUES ('Z-999', 'Never evaluated', 'transaction', 'inline_sync', "
            "'alert', 50, 'test')")
        cur.execute(
            "INSERT INTO rule_conditions (rule_id, condition_group, feature_key, "
            "operator, threshold_num, contribution_points, reason_code) "
            "VALUES ('Z-999', 1, 'card_txn_count_24h', '>=', 99, 40, 'VELOCITY_SPIKE')")

    detail = read_rule(conn, "Z-999")
    assert len(detail.condition_set) == 1
    condition = detail.condition_set[0]
    assert condition.performance is None
    assert "absence of evidence" in (condition.performance_absent_because or "")


def test_versions_do_not_resolve_yet_and_the_surface_admits_it(conn):
    """`rule_versions` is empty for every shipped rule, so a stored
    rule_version_set names a number with no definition behind it. Session 3 is
    where this flips; until then the payload states the gap."""
    assert fetch_value(conn, "SELECT count(*) AS n FROM rule_versions") == 0
    detail = read_rule(conn, "R-114")
    assert detail.versions == []
    assert detail.versions_resolve is False


def test_an_unknown_rule_is_a_404(client):
    assert client.get("/rules/NOPE-1").status_code == 404


# ---------------------------------------------------------------- features
def test_the_catalog_publishes_its_computation_spec(conn):
    features = {f.feature_key: f for f in read_features(conn)}
    burst = features["merchant_decline_burst"]
    assert burst.aggregation == "count"
    assert burst.source_relation == "transactions"
    assert burst.window_spec == "10m"
    assert burst.inline_capable is True


def test_absence_and_a_default_of_zero_stay_distinguishable(conn):
    """§5's whole distinction, on the wire. JSON alone cannot carry it — `null`
    and "no default" render identically — so `has_default` does."""
    features = {f.feature_key: f for f in read_features(conn)}

    geo = features["session_geo_jump_km"]      # seed 0022 gave it a real 0
    assert geo.has_default is True and geo.default_when_absent == 0

    travel = features["recent_travel_purchase"]  # a mitigator: no default, ever
    assert travel.has_default is False and travel.default_when_absent is None


def test_the_inline_filter_is_a_constraint_not_a_decoration(conn):
    """§2.1: a rule is inline only if every feature it reads is inline-capable,
    and `rules/validate.py` rejects one that is not. The filter exists so an
    authoring UI can offer only the features that will pass."""
    inline = read_features(conn, inline_capable=True)
    assert inline and all(f.inline_capable for f in inline)
    assert len(inline) < len(read_features(conn))


# ---------------------------------------------------------------- reference
def test_the_vocabulary_comes_from_the_rows_that_enforce_it(conn):
    reference = read_reference(conn)

    assert [a.action for a in reference.actions] == [
        r["action"] for r in fetch_all(
            conn, "SELECT action FROM ref_action ORDER BY severity")]
    assert {a.action for a in reference.actions if a.is_preventive} == {
        "challenge", "hold", "block"}
    assert {s.value for s in reference.subject_types} == {
        r["subject_type"] for r in fetch_all(
            conn, "SELECT subject_type FROM ref_subject_type")}


def test_the_published_operators_are_the_ones_the_engine_implements(conn):
    """The reason this endpoint exists. A dropdown built from anything else can
    offer an operator the validator refuses — and worse, one `fires()` silently
    returns False for."""
    reference = read_reference(conn)
    assert [o.operator for o in reference.operators] == list(SUPPORTED_OPERATORS)
    assert all(o.takes and o.description for o in reference.operators)


def test_the_reducers_are_published_from_the_implementation(conn):
    """§3.1 names seven; the catalogued features need seventeen. Publishing the
    list from `aggregations.REDUCERS` is what keeps "which reducers exist" a fact
    rather than a claim in a README."""
    from glassbox.features.aggregations import REDUCERS
    assert read_reference(conn).aggregations == sorted(REDUCERS)
    assert "ratio" not in REDUCERS, (
        "§3.1 names ratio and nothing implements it, deliberately — a spec asking "
        "for it fails loudly rather than returning a number nobody defined")


# ---------------------------------------------------------------- over http
def test_every_catalog_endpoint_serves_a_schema_valid_payload(client):
    for item in client.get("/rules").json():
        RuleSummary.model_validate(item)          # extra='forbid'
    RuleDetail.model_validate(client.get("/rules/R-114").json())
    for item in client.get("/features").json():
        FeatureView.model_validate(item)
    ReferenceVocabulary.model_validate(client.get("/reference").json())


def test_the_control_plane_reads_stay_open(client):
    """Consistent with every other GET here. What is gated is what leaves a mark."""
    for path in ("/rules", "/rules/R-114", "/features", "/reference"):
        assert client.get(path).status_code == 200, path
