"""Week 5 — the scheduler. §15's topology, and §18's decision 6.

`architecture.md` §15 describes the prototype as "one service, one database,
**a scheduler**", and for four weeks the last third of that sentence was a
promise: `run_cycle.py` was run by hand, so nothing in this system ever reacted
to anything. §18's open decision 6 — the async cycle period — could not be
settled because nothing had a period to set.

Two properties carry this module, and they pull against each other.

**A tick must see what arrived.** Ingest a charge, turn the engine over, and a
decision exists that did not exist before. That is the whole feature.

**A tick must not re-score the population to notice one charge.**
`plan_evaluations` plans every subject of every type a lane's rules name, so the
naive version is forty seconds of work every thirty seconds, forever. The
narrowing is by SUBJECT and never by rule or feature — a subject that is
re-evaluated is re-evaluated in full against its whole history — which is the
property that makes being incremental safe rather than merely fast.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from glassbox.db import fetch_all, fetch_one, fetch_value
from glassbox.ingest import arrivals, watermark
from glassbox.ingest.cycle import affected_subjects, run_cycle
from glassbox.scheduler import Scheduler, interval_seconds

LATER = datetime(2026, 1, 15, 18, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def caught_up(conn):
    """A database whose watermarks say it has consumed everything.

    `conftest` builds the fixtures and runs both lanes, so that IS the true
    state — but `reset_db` only marks the two stages it runs itself, and the
    lanes are run separately. Setting them here is what a `bootstrap.ps1` run
    leaves behind, and every test below is about what happens AFTER that.
    """
    frontier = watermark.frontier(conn)
    for stream in watermark.STREAMS:
        watermark.advance(conn, stream, frontier)
    return frontier


def a_charge(txn_id: str, at: datetime, **overrides) -> dict:
    base = dict(txn_id=txn_id, occurred_at=at, amount=Decimal("18.40"),
                card_id="CARD-4417", account_id="ACC-4417",
                customer_id="CUST-OKAFOR", merchant_id="MER-227", mcc="5812",
                channel="pos", entry_mode="chip_pin", txn_country="US")
    base.update(overrides)
    return base


# ------------------------------------------------------------------ reacting
def test_a_cycle_decides_what_arrived_since_the_last_one(conn, caught_up):
    """The feature, in one test: a row lands, the engine turns over, a decision
    exists that did not exist before — and nobody ran a script."""
    arrivals.ingest(conn, "transactions", [a_charge("CYCLE-1", LATER)])

    assert fetch_value(conn, "SELECT count(*) AS n FROM decisions "
                             " WHERE subject_id = 'CYCLE-1'") == 0
    result = run_cycle(conn)

    assert result.ran is True
    assert result.as_of == LATER
    assert fetch_value(conn, "SELECT count(*) AS n FROM decisions "
                             " WHERE subject_id = 'CYCLE-1'") == 1


def test_a_cycle_with_nothing_new_does_nothing_and_says_why(conn, caught_up):
    result = run_cycle(conn)
    assert result.ran is False
    assert "nothing has arrived" in result.reason
    assert result.lanes == {}


def test_a_tick_evaluates_the_arrival_not_the_population(conn, caught_up):
    """The assertion that makes a 30-second interval possible at all.

    Without the narrowing this is 9,844 evaluations on the inline lane. With it,
    it is the charge and the handful of entities behind it — and the decision
    the charge gets is identical either way, because narrowing is by subject and
    a narrowed subject is still evaluated against its whole history.
    """
    arrivals.ingest(conn, "transactions", [a_charge("CYCLE-2", LATER)])
    result = run_cycle(conn)

    assert result.lanes["inline_sync"]["evaluations"] == 1
    assert result.lanes["async"]["evaluations"] <= 2, (
        "the account behind the charge, and any cluster it belongs to")
    assert fetch_value(conn, "SELECT count(*) AS n FROM transactions") > 9000, (
        "the population is still there — it was not evaluated, not absent")


def test_the_affected_set_reaches_every_entity_behind_the_charge(conn, caught_up):
    """Deliberately over-inclusive. Narrowing wrongly here would SKIP a subject
    silently; over-including only costs work, and the sets are tiny."""
    arrivals.ingest(conn, "transactions", [a_charge("CYCLE-3", LATER)])
    touched = affected_subjects(conn, caught_up, LATER)

    assert set(touched) >= {"CYCLE-3", "CARD-4417", "ACC-4417", "CUST-OKAFOR",
                            "MER-227"}


def test_an_arriving_event_moves_a_subject_with_no_new_transaction(conn, caught_up):
    """A password reset changes `min_since_password_reset` on an account that
    has no new charge at all. An affected set built from transactions alone
    would miss S-077's entire premise."""
    arrivals.ingest(conn, "events", [dict(
        occurred_at=LATER, event_type="password_reset",
        subject_type="account", subject_id="ACC-2201")])

    assert "ACC-2201" in affected_subjects(conn, caught_up, LATER)


def test_a_ring_built_from_arriving_links_is_scored_in_the_same_tick(conn, caught_up):
    """L-203's whole path, from HTTP to a network decision, in one cycle.

    A ring is discovered from `entity_links` and nowhere else, so this is the
    half that could not exist before: four `opened_on` edges arrive, the graph
    step turns them into a cluster, and the async lane scores that cluster
    without waiting for another tick.

    The inbound transfer matters and is not decoration. The network planner
    triggers a cluster off "the most recent inbound into any member" — a ring
    with no money in it is correctly not evaluable — so a links-only arrival
    builds the cluster and the planner declines to plan it, which is the engine
    being right rather than the cycle being wrong.
    """
    accounts = [r["account_id"] for r in fetch_all(
        conn, "SELECT account_id FROM accounts WHERE account_id NOT IN "
              "(SELECT subject_id FROM cluster_members WHERE subject_type='account') "
              "ORDER BY account_id LIMIT 4")]
    owner = fetch_one(conn, "SELECT customer_id FROM accounts WHERE account_id = %s",
                      (accounts[0],))

    arrivals.ingest(conn, "entity_links", [
        dict(from_type="device", from_id="DEV-500", to_type="account",
             to_id=account, link_type="opened_on", first_seen=LATER)
        for account in accounts])
    arrivals.ingest(conn, "transactions", [dict(
        txn_id="CYCLE-INBOUND", occurred_at=LATER, amount=Decimal("2400.00"),
        account_id=accounts[0], customer_id=owner["customer_id"],
        direction="inbound", txn_type="transfer", channel="wire")])

    result = run_cycle(conn)
    assert result.ran is True
    assert result.clusters >= 2, "the shipped ring, plus the one just built"

    cluster_id = fetch_value(
        conn, "SELECT cluster_id AS c FROM clusters WHERE natural_key = %s",
        ("device_fanout:DEV-500",))
    decided = fetch_one(
        conn, "SELECT score, action_taken FROM decisions "
              " WHERE subject_type = 'network' AND subject_id = %s", (cluster_id,))
    assert decided is not None, (
        "the cluster this tick created was scored in this tick, not left for "
        "the next one")


# ---------------------------------------------------------------- watermarks
def test_the_watermark_is_event_time_not_wall_clock(conn, caught_up):
    """The fixtures are pinned to GLASSBOX_NOW. A wall-clock watermark would sit
    seven months after every row it gates, so the first tick would consume
    everything and every tick after it would consume nothing."""
    arrivals.ingest(conn, "transactions", [a_charge("CYCLE-4", LATER)])
    run_cycle(conn)

    for stream in watermark.STREAMS:
        assert watermark.read(conn, stream) == LATER, stream


def test_a_watermark_never_moves_backward(conn, caught_up):
    """A replay of January must not persuade the scheduler it has not yet seen
    July. `run_cycle --as-of <historical>` and a live tick both write here."""
    watermark.advance(conn, watermark.FEATURES, LATER)
    watermark.advance(conn, watermark.FEATURES, LATER - timedelta(days=30))
    assert watermark.read(conn, watermark.FEATURES) == LATER


def test_a_cycle_that_is_forced_runs_anyway(conn, caught_up):
    result = run_cycle(conn, force=True)
    assert result.ran is True


def test_the_frontier_reads_links_as_well_as_transactions(conn, caught_up):
    """An ingested `opened_on` edge is what makes a ring knowable. A frontier
    bounded below it would build the cluster and then decline to score it."""
    arrivals.ingest(conn, "entity_links", [dict(
        from_type="device", from_id="DEV-500", to_type="account",
        to_id="ACC-4417", link_type="opened_on", first_seen=LATER)])
    assert watermark.frontier(conn) == LATER


# ----------------------------------------------------------------- scheduler
def test_the_scheduler_is_off_in_this_suite(conn):
    """`conftest` sets GLASSBOX_CYCLE_SECONDS=0 before anything imports the app.

    Not a preference: every test here runs inside a transaction that is rolled
    back, and a thread committing its own cycle into the middle of that would be
    the least debuggable failure this project could produce.
    """
    assert interval_seconds() == 0
    assert Scheduler().enabled is False
    assert Scheduler().start() is False


def test_a_scheduler_with_an_interval_is_enabled_but_not_started():
    """The object is constructible with a period; starting it is a separate act,
    and `glassbox serve` is what performs it."""
    scheduler = Scheduler(interval=30)
    assert scheduler.enabled is True
    assert scheduler.running is False
    assert scheduler.interval == 30


def test_a_tick_records_what_it_did(conn, caught_up):
    """The operator surface has to be able to show that something is running
    rather than asserting it — the same standard §11 holds for a KPI tile."""
    from glassbox.scheduler import Tick

    arrivals.ingest(conn, "transactions", [a_charge("CYCLE-5", LATER)])
    tick = Tick.of(run_cycle(conn))

    assert tick.ran is True
    assert tick.decisions >= 1
    assert tick.error is None


def test_a_failing_tick_is_recorded_rather_than_killing_the_loop(monkeypatch):
    """A scheduler that dies on the first bad row is a scheduler nobody can
    trust to be running, which is worse than not having one."""
    import glassbox.scheduler as scheduler_mod

    def explode(*_args, **_kwargs):
        raise RuntimeError("database went away")

    monkeypatch.setattr(scheduler_mod, "run_cycle", explode)
    tick = Scheduler(interval=30).tick()

    assert tick.ran is False
    assert "database went away" in tick.error
