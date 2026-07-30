"""Every value the deleted generator code used to derive, now computed by the runner.

These numbers were transcribed from generate_synthetic.py's derivation helpers
BEFORE they were deleted — cnp_count_last_90s, the haversine call, the z-score
block, the minutes-between-events arithmetic. Keeping them here is how the
runner-vs-generator agreement §14 wants is obtained without keeping two
implementations of the feature layer alive to drift apart.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from glassbox.db import fetch_all, fetch_one

REF = "2026-01-15T15:00:00+00:00"


def value_at(conn, feature_key, entity_type, entity_id, bound):
    return fetch_one(
        conn,
        """
        SELECT value_num, value_bool, as_of, spec_version FROM feature_values
         WHERE feature_key=%s AND entity_type=%s AND entity_id=%s AND as_of <= %s
         ORDER BY as_of DESC, computed_at DESC LIMIT 1
        """,
        (feature_key, entity_type, entity_id, bound),
    )


BURST = "2026-01-15 14:32:08+00"
TRANSFER = "2026-01-15 13:24:12+00"
RESTAURANT = "2026-01-15 12:48:55+00"
RING_BOUND = "2026-01-15 14:11:00+00"

NUMERIC_CASES = [
    # (feature, entity_type, entity_id, bound, expected, tolerance, what the generator called it)
    ("card_cnp_count", "card", "CARD-4417", BURST, 5, 0, "A_cnp"),
    ("device_first_seen_min", "device", "DEV-F1A2", BURST, 6.0, 0.01, "A_dev_min"),
    ("session_geo_jump_km", "card", "CARD-4417", BURST, 1412, 1.0, "A_geo"),
    ("accounts_per_device", "device", "DEV-F90D2", RING_BOUND, 4, 0, "B_accounts_per_device"),
    ("min_since_password_reset", "account", "ACC-2201", TRANSFER, 11.0, 0.01, "C_reset_min"),
    ("amount_over_avail_balance_pct", "account", "ACC-2201", TRANSFER, 100, 0.01, "C_pct"),
]

BOOLEAN_CASES = [
    ("mcc_is_new_for_customer", "transaction", "TXN-48291", BURST, True, "A_mcc_new"),
    ("structuring_flag", "account", "ACC-8830", RING_BOUND, True, "B_structuring"),
    ("activity_is_passthrough_only", "account", "ACC-7702", RING_BOUND, True, "B_passthrough_only"),
    ("ip_is_datacenter", "transaction", "TXN-48260", TRANSFER, True, "C_ip_dc"),
    ("country_is_new_for_customer", "transaction", "TXN-48251", RESTAURANT, True, "D_country_new"),
    ("recent_travel_purchase", "customer", "CUST-MENSAH", RESTAURANT, True, "D_recent_travel"),
    ("entry_mode_chip_pin", "transaction", "TXN-48251", RESTAURANT, True, "D_chip"),
]


@pytest.mark.parametrize("fk,et,eid,bound,expected,tol,legacy", NUMERIC_CASES,
                         ids=[c[6] for c in NUMERIC_CASES])
def test_numeric_features_match_the_deleted_derivations(conn, fk, et, eid, bound,
                                                        expected, tol, legacy):
    row = value_at(conn, fk, et, eid, bound)
    assert row is not None, f"{fk} has no value for {eid} at {bound}"
    assert abs(row["value_num"] - Decimal(str(expected))) <= Decimal(str(tol))


@pytest.mark.parametrize("fk,et,eid,bound,expected,legacy", BOOLEAN_CASES,
                         ids=[c[5] for c in BOOLEAN_CASES])
def test_boolean_features_match_the_deleted_derivations(conn, fk, et, eid, bound,
                                                        expected, legacy):
    row = value_at(conn, fk, et, eid, bound)
    assert row is not None, f"{fk} has no value for {eid} at {bound}"
    assert row["value_bool"] is expected


def test_pass_through_ratio_is_1_on_every_ring_account(conn):
    """B_pass_ratio = 1.0. fanout_policy='min' is what makes the seeded text
    'on all 4 accounts' literally true when the condition fires."""
    members = fetch_all(
        conn,
        "SELECT subject_id FROM cluster_members WHERE cluster_id='RING-1187' "
        "AND subject_type='account' ORDER BY subject_id",
    )
    assert len(members) == 4
    ratios = [value_at(conn, "pass_through_ratio", "account", m["subject_id"],
                       RING_BOUND)["value_num"] for m in members]
    assert min(ratios) >= Decimal("1.0")


def test_amount_z_score_stays_inside_one_deviation(conn):
    """D_amount_z was ~0.3; T-021's condition is < 1.0."""
    row = value_at(conn, "amount_vs_baseline_z", "transaction", "TXN-48251", RESTAURANT)
    assert row["value_num"] < Decimal("1.0")


def test_the_sequence_feature_is_the_only_hand_seeded_value(conn):
    """One hand-seeded value out of 21 is honest; nineteen was the situation
    the generator rewrite exists to end."""
    rows = fetch_all(
        conn,
        "SELECT DISTINCT feature_key FROM feature_values WHERE spec_version = 1 "
        "AND feature_key IN (SELECT feature_key FROM feature_catalog "
        "WHERE source_kind = 'sequence')",
    )
    assert [r["feature_key"] for r in rows] == ["new_payee_then_drain"]


def test_every_active_spec_is_computable_or_explicitly_deferred(conn):
    from glassbox.features.predicate import load_allowlist
    from glassbox.features.runner import IncrementalRunner

    runner = IncrementalRunner(conn)
    deferred = []
    for key, spec in runner.specs.items():
        if spec.source_kind == "sequence":
            deferred.append(key)
            continue
        runner.compiled(key)             # raises if the spec cannot compile
    assert deferred == ["new_payee_then_drain"]
