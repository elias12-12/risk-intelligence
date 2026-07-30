"""§4 — point-in-time correctness, at write time and at read time."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from conftest import request_for
from glassbox import config
from glassbox.db import fetch_one, fetch_value
from glassbox.engine.evaluation import EngineContext, evaluate
from glassbox.features.runner import IncrementalRunner


def _score(result, rule_id: str) -> Decimal:
    return next(rs for rs in result.rule_scores if rs.rule_id == rule_id).score


def test_a_later_arrival_does_not_change_a_past_score(conn, ctx):
    """The whole reason values are stamped as_of. A charge that happens after
    the bound must be invisible to an evaluation at that bound."""
    request = request_for(conn, ctx, "inline_sync", "transaction", "TXN-48291")
    assert _score(evaluate(conn, request, ctx), "R-114") == 87

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO transactions (txn_id, occurred_at, amount, currency, amount_base,
                direction, txn_type, card_id, account_id, customer_id, merchant_id, mcc,
                channel, entry_mode, auth_result, txn_country, txn_lat, txn_lon, device_id)
            VALUES ('TXN-LATE-1', '2026-01-15 14:50:00+00', 99.0, 'USD', 99.0,
                'debit', 'purchase', 'CARD-4417', 'ACC-4417', 'CUST-OKAFOR', 'MER-GIFT',
                '6540', 'cnp', 'ecom', 'approved', 'US', 40.71, -90.78, 'DEV-F1A2')
            """
        )
    IncrementalRunner(conn).run_population(config.reference_now(),
                                           since=request.occurred_at)
    assert _score(evaluate(conn, request, ctx), "R-114") == 87


def test_the_runner_never_writes_a_value_that_saw_the_future(conn):
    """PIT correctness at WRITE time, the companion to the read bound. A runner
    that sees ahead writes a poisoned value no read bound can un-poison."""
    runner = IncrementalRunner(conn)
    row = runner.run_for_entity("card_cnp_count", "CARD-4417",
                                as_of=config.reference_now() - timedelta(days=1))
    assert row["value_num"] == 0, "the burst is on 15 Jan; a bound on 14 Jan must not see it"


def test_replay_preserves_the_stored_answer_while_live_moves_on(conn, ctx):
    """The bitemporal property 0014 exists for.

    A recomputation at an ALREADY-USED as_of is an INSERT with a newer
    computed_at. A live read takes today's best answer; a replay pinned to the
    original computed_at still gets exactly what the engine saw. Before 0014
    the recomputation overwrote the row and the audit trail quietly became a
    lie.
    """
    request = request_for(conn, ctx, "inline_sync", "transaction", "TXN-48291")
    original = fetch_one(
        conn,
        "SELECT as_of, computed_at, value_num FROM feature_values "
        "WHERE feature_key='card_cnp_count' AND entity_id='CARD-4417' "
        "AND as_of <= %s ORDER BY as_of DESC, computed_at DESC LIMIT 1",
        (request.occurred_at,),
    )
    assert original["value_num"] == 5

    # The replay ceiling is the DECISION's decided_at — "everything we knew when
    # we decided" — not one feature's computed_at. Pinning to a single feature
    # would exclude every feature the runner happened to write after it, and the
    # replay would fail for a reason that has nothing to do with the recompute.
    stored = fetch_one(
        conn,
        "SELECT decided_at FROM decisions WHERE subject_id='TXN-48291' "
        "AND execution_mode='inline_sync' ORDER BY decision_id DESC LIMIT 1",
    )
    knowledge_ceiling = stored["decided_at"]

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO feature_values (feature_key, entity_type, entity_id, as_of,
                                        value_num, computed_at, spec_version)
            VALUES ('card_cnp_count', 'card', 'CARD-4417', %s, 2, now(), 1)
            """,
            (original["as_of"],),
        )
        # Both rows coexist: the widened key is what makes that possible.
        assert fetch_value(
            conn,
            "SELECT count(*) FROM feature_values WHERE feature_key='card_cnp_count' "
            "AND entity_id='CARD-4417' AND as_of=%s", (original["as_of"],)) == 2

    live = evaluate(conn, request, ctx)
    assert _score(live, "R-114") == 0, "live must see the corrected value and stop firing"

    replayed = evaluate(conn, _pin(request, knowledge_ceiling), ctx)
    assert _score(replayed, "R-114") == 87, "replay must reproduce what was decided"


def _pin(request, replay_as_of):
    from dataclasses import replace
    return replace(request, replay_as_of=replay_as_of)


def test_evaluation_lag_is_what_lets_the_async_lane_see_the_pattern(conn, ctx):
    """L-203's trigger is the last inbound into the ring. At that instant the
    funds have not been forwarded, so the rule correctly does not fire. Fifteen
    minutes later the pattern is complete and it scores 64.

    The bound is occurred_at + lag, NEVER decided_at: on a replay decided_at is
    'now', which reintroduces exactly the lookahead as_of exists to prevent.
    """
    request = request_for(conn, ctx, "async", "network", "RING-1187")
    assert ctx.rules["L-203"].evaluation_lag == timedelta(minutes=15)
    assert _score(evaluate(conn, request, ctx), "L-203") == 64

    with conn.cursor() as cur:
        cur.execute("UPDATE rule_definitions SET evaluation_lag='0 seconds' "
                    "WHERE rule_id='L-203'")
    no_lag = EngineContext.load(conn)
    result = evaluate(conn, request, no_lag)
    l203 = next(rs for rs in result.rule_scores if rs.rule_id == "L-203")
    assert not l203.evaluation.satisfied
    assert l203.score == 0


def test_a_value_beyond_max_staleness_reads_absent(conn, ctx):
    """Staleness is judged against the bound, and a stale read is a DEGRADED
    read — not a usable one and not a silent zero."""
    request = request_for(conn, ctx, "async", "network", "RING-1187")
    assert "accounts_per_device" not in evaluate(conn, request, ctx).degraded_features

    with conn.cursor() as cur:
        cur.execute("UPDATE feature_catalog SET max_staleness='1 second' "
                    "WHERE feature_key='accounts_per_device'")
    strict = EngineContext.load(conn)
    result = evaluate(conn, request_for(conn, strict, "async", "network", "RING-1187"), strict)
    assert "accounts_per_device" in result.degraded_features
    l203 = next(rs for rs in result.rule_scores if rs.rule_id == "L-203")
    assert not l203.evaluation.satisfied


def test_staleness_is_judged_on_the_oldest_contributor(conn, ctx):
    """A reduction must not launder staleness: value_as_of records min(as_of)
    across contributors, so one stale account degrades the whole read."""
    from glassbox.engine.pit import PitRequest, read_many
    from glassbox.engine.resolver import resolve

    request = request_for(conn, ctx, "async", "network", "RING-1187")
    spec = ctx.specs["pass_through_ratio"]
    resolution = resolve(conn, request, spec, ctx.graph)
    read = read_many(conn, [PitRequest("k", spec, resolution,
                                       request.occurred_at + timedelta(minutes=15))])["k"]
    assert read.status == "present"
    assert len(read.entity_ids) == 4
    newest = fetch_value(
        conn,
        "SELECT max(as_of) FROM feature_values WHERE feature_key='pass_through_ratio' "
        "AND entity_id = ANY(%s) AND as_of <= %s",
        (list(read.entity_ids), request.occurred_at + timedelta(minutes=15)),
    )
    assert read.as_of < newest, "as_of must be the oldest contributor, not the newest"
