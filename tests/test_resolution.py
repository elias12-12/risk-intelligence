"""§3.2 — subject-to-entity resolution."""
from __future__ import annotations

import pytest

from conftest import request_for
from glassbox.db import fetch_all, fetch_value
from glassbox.engine.evaluation import EngineContext, evaluate


def test_every_stored_value_keys_on_what_the_catalog_says(conn):
    """THE regression test for defect 1, and it fails on Week 1's main.

    The old generator wrote 19 of 21 features flattened onto the rule's subject
    — card_cnp_count under entity_type='transaction', the whole ring's features
    under entity_type='network'. The scorer then joined on feature_key plus
    entity_type and nothing else, so every card's value matched every
    transaction of every rule. One query catches all of it.
    """
    mismatches = fetch_all(
        conn,
        """
        SELECT DISTINCT fv.feature_key, fv.entity_type AS stored,
               fc.entity_type AS catalogued
          FROM feature_values fv
          JOIN feature_catalog fc USING (feature_key)
         WHERE fv.entity_type <> fc.entity_type
        """,
    )
    assert mismatches == []


def test_r114_fires_only_where_all_four_conditions_hold(conn, ctx):
    """Every transaction R-114 contributed to has all four of its conditions on
    the bar. Partial firing is the bug this replaces."""
    rows = fetch_all(
        conn,
        """
        SELECT d.subject_id, count(*) FILTER (WHERE s.source_rule_id = 'R-114') AS n
          FROM decisions d
          JOIN alerts a ON a.decision_id = d.decision_id
          JOIN alert_signals s ON s.alert_id = a.alert_id
         WHERE 'R-114' = ANY(d.rules_fired)
         GROUP BY d.subject_id
        """,
    )
    assert rows, "expected at least one R-114 alert"
    assert all(r["n"] == 4 for r in rows), rows


def test_a_near_miss_does_not_fire(conn, ctx):
    """The fourth charge of the burst has four CNP charges behind it, not five.
    Under the old one-group OR semantics it scored; under AND it does not."""
    near = fetch_value(
        conn,
        """
        SELECT txn_id FROM transactions
         WHERE card_id = 'CARD-4417' AND channel = 'cnp'
         ORDER BY occurred_at DESC OFFSET 1 LIMIT 1
        """,
    )
    result = evaluate(conn, request_for(conn, ctx, "inline_sync", "transaction", near), ctx)
    r114 = next(rs for rs in result.rule_scores if rs.rule_id == "R-114")
    assert not r114.evaluation.satisfied
    assert r114.score == 0, "an unsatisfied rule must contribute nothing, not a partial score"


def test_flipping_combine_to_or_changes_the_outcome(conn, ctx):
    near = fetch_value(
        conn,
        """
        SELECT txn_id FROM transactions
         WHERE card_id = 'CARD-4417' AND channel = 'cnp'
         ORDER BY occurred_at DESC OFFSET 1 LIMIT 1
        """,
    )
    request = request_for(conn, ctx, "inline_sync", "transaction", near)
    assert not _r114(evaluate(conn, request, ctx)).evaluation.satisfied

    with conn.cursor() as cur:
        cur.execute("UPDATE rule_definitions SET combine='OR' WHERE rule_id='R-114'")
    flipped = EngineContext.load(conn)
    assert _r114(evaluate(conn, request, flipped)).evaluation.satisfied


def _r114(result):
    return next(rs for rs in result.rule_scores if rs.rule_id == "R-114")


def test_ip_is_datacenter_resolves_through_the_trigger_root(conn, ctx):
    """S-077's subject is an account. An account has no IP; the transfer that
    triggered the evaluation does. Without a trigger root this condition is
    unresolvable or it fans out over the account's whole history."""
    from glassbox.engine.resolver import resolve

    request = request_for(conn, ctx, "async", "account", "ACC-2201")
    assert request.trigger is not None and request.trigger.type == "transaction"

    spec = ctx.specs["ip_is_datacenter"]
    assert spec.resolution_path == "trigger"
    resolution = resolve(conn, request, spec, ctx.graph)
    assert resolution.status == "ok"
    assert resolution.entity_ids == (request.trigger.id,)
    assert resolution.route.startswith("trigger")


def test_auto_resolution_walks_the_graph(conn, ctx):
    """R-114's subject is a transaction; three of its features key elsewhere."""
    from glassbox.engine.resolver import resolve

    request = request_for(conn, ctx, "inline_sync", "transaction", "TXN-48291")
    routes = {
        key: resolve(conn, request, ctx.specs[key], ctx.graph)
        for key in ("card_cnp_count", "device_first_seen_min", "recent_travel_purchase")
    }
    assert routes["card_cnp_count"].entity_ids == ("CARD-4417",)
    assert routes["device_first_seen_min"].entity_ids == ("DEV-F1A2",)
    assert routes["recent_travel_purchase"].entity_ids == ("CUST-OKAFOR",)
    for r in routes.values():
        assert r.status == "ok" and r.route.startswith("subject.")


def test_route_selection_is_deterministic(ctx):
    """Ties break by ascending edge_id, so a rebuild picks the same path."""
    first = ctx.graph.shortest_route("transaction", "customer")
    for _ in range(20):
        assert ctx.graph.shortest_route("transaction", "customer") == first


def test_fanout_reduces_across_the_ring(conn, ctx):
    """network -> member_account resolves to four accounts, and the policy is
    a reduction over the four STORED values, not over raw rows."""
    from glassbox.engine.resolver import resolve

    request = request_for(conn, ctx, "async", "network", "RING-1187")
    resolution = resolve(conn, request, ctx.specs["pass_through_ratio"], ctx.graph)
    assert resolution.status == "ok"
    assert len(resolution.entity_ids) == 4
    assert ctx.specs["pass_through_ratio"].fanout_policy == "min"


def test_fanout_error_policy_degrades_rather_than_guessing(conn, ctx):
    """A feature whose fan-out was never considered must degrade, not pick one.
    Today's failure mode is a partial score; the new one is a recorded
    degradation."""
    from glassbox.engine.resolver import resolve

    with conn.cursor() as cur:
        cur.execute("UPDATE feature_catalog SET fanout_policy='error' "
                    "WHERE feature_key='pass_through_ratio'")
    strict = EngineContext.load(conn)
    request = request_for(conn, strict, "async", "network", "RING-1187")
    resolution = resolve(conn, request, strict.specs["pass_through_ratio"], strict.graph)
    assert resolution.status == "unresolvable"
    assert resolution.reason.startswith("fanout_error")

    result = evaluate(conn, request, strict)
    assert "pass_through_ratio" in result.degraded_features
    l203 = next(rs for rs in result.rule_scores if rs.rule_id == "L-203")
    assert not l203.evaluation.satisfied and l203.score == 0


def test_unresolvable_is_never_a_silent_zero(conn, ctx):
    """A device with no accounts opened on it resolves to nothing, and that has
    to read as 'we do not know', not as 'zero accounts'."""
    from glassbox.engine.resolver import resolve

    request = request_for(conn, ctx, "async", "network", "RING-1187")
    resolution = resolve(conn, request, ctx.specs["accounts_per_device"], ctx.graph)
    assert resolution.status == "ok"          # this ring does have a device

    with conn.cursor() as cur:
        cur.execute("DELETE FROM cluster_members WHERE subject_type='device'")
    stripped = EngineContext.load(conn)
    gone = resolve(conn, request, stripped.specs["accounts_per_device"], stripped.graph)
    assert gone.status == "unresolvable" and gone.reason == "no_entities"
