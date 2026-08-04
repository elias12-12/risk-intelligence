"""Week 5 — rows arriving after the fact, and the three shapes they come in.

The other door. `/authorize` asks the engine for a decision; this reports one
somebody else already made. Two of the four shipped rules are unreachable
without it, and that is the reason it takes three shapes rather than one:

  * **L-203** discovers a ring from `entity_links`. A hundred ingested transfers
    between four accounts produce no cluster — the `opened_on` edges are the
    pattern, and money is not.
  * **S-077** reads `min_since_password_reset` from `events`. Without the
    behavioural log there is no credential signal at all.

The assertions worth reading are the refusals. A link to an account nobody
opened has no foreign key to stop it — `entity_links.to_id` is polymorphic, the
type lives in a neighbouring column — so a phantom edge would build a cluster
out of nothing and the ring that came out of it would look exactly like a real
one.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from glassbox.contract.ingest import IngestReceipt
from glassbox.db import fetch_all, fetch_one, fetch_value
from glassbox.ingest import arrivals

ADMIN = {"Authorization": "Bearer admin-token"}
ANALYST = {"Authorization": "Bearer analyst-token"}

LATER = datetime(2026, 1, 15, 16, 0, 0, tzinfo=timezone.utc)


def settled(n: int, **overrides) -> dict:
    base = dict(
        txn_id=f"INGEST-{n}", occurred_at=LATER + timedelta(seconds=20 * n),
        amount=Decimal("2.50"), card_id="CARD-4417", account_id="ACC-4417",
        customer_id="CUST-OKAFOR", merchant_id="MER-TEST", mcc="5999",
        channel="cnp", entry_mode="ecom", auth_result="declined",
        decline_reason="do_not_honor", txn_country="US")
    base.update(overrides)
    return base


# ---------------------------------------------------------------- transactions
def test_settled_transactions_are_written_with_their_provenance(conn):
    receipt = arrivals.ingest(conn, "transactions", [settled(n) for n in range(3)])

    assert receipt.written == 3
    assert receipt.duplicates == [] and receipt.rejected == []
    assert receipt.max_occurred_at == LATER + timedelta(seconds=40)

    rows = fetch_all(conn, "SELECT txn_id, source, auth_result FROM transactions "
                           " WHERE txn_id LIKE 'INGEST-%' ORDER BY txn_id")
    assert [r["source"] for r in rows] == ["ingested"] * 3
    assert [r["auth_result"] for r in rows] == ["declined"] * 3


def test_a_settled_row_may_state_what_authorize_may_not(conn):
    """The two doors, mirrored. A settled row reports a decision that was already
    made, so `auth_result` and `decline_reason` are its to state — and
    `merchant_decline_burst` counts declines, so this is the only way to plant a
    card-testing pattern. `/authorize` refuses all three."""
    arrivals.ingest(conn, "transactions", [settled(0, synthetic_label="fraud")])
    row = fetch_one(conn, "SELECT auth_result, decline_reason, synthetic_label "
                          "  FROM transactions WHERE txn_id = 'INGEST-0'")
    assert row["auth_result"] == "declined"
    assert row["decline_reason"] == "do_not_honor"
    assert row["synthetic_label"] == "fraud", (
        "deliberately planted demo data SHOULD be labelled, or it is invisible "
        "to every precision number in the system")


def test_re_sending_a_batch_is_a_retry_not_an_error(conn):
    """Raw capture is append-only, so a duplicate is DROPPED rather than merged.
    But a client re-POSTing after a timeout is retrying, and a receipt that
    counted that as a failure would push a caller toward doing something worse."""
    rows = [settled(n) for n in range(3)]
    first = arrivals.ingest(conn, "transactions", rows)
    second = arrivals.ingest(conn, "transactions", rows)

    assert first.written == 3 and first.duplicates == []
    assert second.written == 0
    assert sorted(second.duplicates) == ["INGEST-0", "INGEST-1", "INGEST-2"]
    assert second.rejected == []
    assert fetch_value(conn, "SELECT count(*) AS n FROM transactions "
                             " WHERE txn_id LIKE 'INGEST-%'") == 3


def test_one_bad_row_does_not_refuse_the_batch(conn):
    """Partial acceptance, deliberately. Refusing two hundred rows because one
    names an unknown card makes a caller re-send everything to fix one — and
    re-sending is exactly what the duplicate handling exists to make safe."""
    receipt = arrivals.ingest(conn, "transactions", [
        settled(0), settled(1, card_id="CARD-NOPE"), settled(2)])

    assert receipt.written == 2
    assert [i for i, _ in receipt.rejected] == [1]
    assert "CARD-NOPE" in receipt.rejected[0][1][0]


def test_an_unseen_device_on_a_settled_row_is_registered_too(conn):
    """Both doors agree about what an unknown fingerprint means. They have to:
    a device that existed when a charge was authorized and not when the same
    charge was ingested would make `device_first_seen_min` depend on which
    endpoint the row came through."""
    receipt = arrivals.ingest(conn, "transactions",
                              [settled(0, device_id="DEV-INGEST-NEW")])
    assert receipt.devices_registered == ["DEV-INGEST-NEW"]
    assert fetch_value(conn, "SELECT source AS s FROM devices WHERE device_id = %s",
                       ("DEV-INGEST-NEW",)) == "ingested"


# --------------------------------------------------------------------- events
def test_an_event_reaches_the_behavioural_log(conn):
    receipt = arrivals.ingest(conn, "events", [dict(
        occurred_at=LATER, event_type="password_reset",
        subject_type="account", subject_id="ACC-4417")])

    assert receipt.written == 1
    assert receipt.idempotent is False, (
        "events have no natural key and this says so rather than implying "
        "otherwise — see arrivals.NON_IDEMPOTENT_NOTE")
    assert fetch_value(
        conn, "SELECT source AS s FROM events WHERE subject_id = %s "
              "ORDER BY event_id DESC LIMIT 1", ("ACC-4417",)) == "ingested"


def test_an_event_type_outside_the_vocabulary_is_refused(conn):
    """Vocabularies are ROWS, so a new value is an INSERT into `ref_event_type`
    and never a code change — which is exactly why the check reads the rows
    rather than a literal in Python."""
    receipt = arrivals.ingest(conn, "events", [dict(
        occurred_at=LATER, event_type="quantum_reset",
        subject_type="account", subject_id="ACC-4417")])

    assert receipt.written == 0
    assert "ref_event_type" in receipt.rejected[0][1][0]


def test_an_event_about_a_subject_that_does_not_exist_is_refused(conn):
    receipt = arrivals.ingest(conn, "events", [dict(
        occurred_at=LATER, event_type="password_reset",
        subject_type="account", subject_id="ACC-PHANTOM")])
    assert receipt.written == 0
    assert "not a known account" in receipt.rejected[0][1][0]


# ---------------------------------------------------------------------- links
def test_a_link_reaches_the_layer_the_graph_builder_reads(conn):
    receipt = arrivals.ingest(conn, "entity_links", [dict(
        from_type="device", from_id="DEV-500", to_type="account",
        to_id="ACC-4417", link_type="opened_on", first_seen=LATER)])

    assert receipt.written == 1
    assert fetch_value(
        conn, "SELECT source AS s FROM entity_links WHERE link_id = "
              "(SELECT max(link_id) FROM entity_links)") == "ingested"


def test_the_same_edge_twice_is_one_edge(conn):
    """`entity_links` has a surrogate key, so identity has to come from what the
    edge MEANS. The cluster builder counts DISTINCT accounts per device, so a
    duplicate is harmless to the ring and misleading to anyone reading the link
    layer — which is the kind of wrong this project fixes rather than tolerates."""
    edge = dict(from_type="device", from_id="DEV-500", to_type="account",
                to_id="ACC-4417", link_type="opened_on", first_seen=LATER)
    assert arrivals.ingest(conn, "entity_links", [edge]).written == 1
    second = arrivals.ingest(conn, "entity_links", [edge])

    assert second.written == 0
    assert second.duplicates == ["device:DEV-500:account:ACC-4417:opened_on"]


def test_a_link_to_an_account_nobody_opened_is_refused(conn):
    """Nothing else would catch this. `to_id` is polymorphic — the type lives in
    `to_type` — so there is no foreign key, and a phantom edge builds a cluster
    out of nothing whose ring looks exactly like a real one."""
    receipt = arrivals.ingest(conn, "entity_links", [dict(
        from_type="device", from_id="DEV-500", to_type="account",
        to_id="ACC-PHANTOM", link_type="opened_on")])

    assert receipt.written == 0
    assert "ACC-PHANTOM" in receipt.rejected[0][1][0]
    assert "no foreign key" in receipt.rejected[0][1][0]


def test_ingested_links_reach_the_cluster_builder(conn):
    """The path L-203 depends on, end to end: edges in, cluster out.

    The rule's own conditions need a pass-through money pattern this test does
    not build — what it establishes is that the ARRIVAL path reaches the graph,
    which is the half that did not exist.
    """
    from glassbox.graph.builder import build

    accounts = [r["account_id"] for r in fetch_all(
        conn, "SELECT account_id FROM accounts WHERE account_id NOT IN "
              "(SELECT subject_id FROM cluster_members WHERE subject_type='account') "
              "ORDER BY account_id LIMIT 4")]
    assert len(accounts) == 4, "need four unclustered accounts for this to mean anything"

    before = fetch_value(conn, "SELECT count(*) AS n FROM clusters")
    arrivals.ingest(conn, "entity_links", [
        dict(from_type="device", from_id="DEV-500", to_type="account",
             to_id=account, link_type="opened_on", first_seen=LATER)
        for account in accounts])
    build(conn)

    assert fetch_value(conn, "SELECT count(*) AS n FROM clusters") == before + 1
    members = fetch_all(
        conn,
        """
        SELECT cm.subject_id FROM cluster_members cm
          JOIN clusters c ON c.cluster_id = cm.cluster_id
         WHERE c.natural_key = %s AND cm.subject_type = 'account'
         ORDER BY cm.subject_id
        """,
        ("device_fanout:DEV-500",))
    assert sorted(m["subject_id"] for m in members) == sorted(accounts)


def test_a_second_cluster_does_not_take_the_first_ones_id(conn):
    """WEEK5-PLAN **D10**, found by this path and fixed.

    `_stable_id` used to allocate `RING-{1187 + seq}` from the candidate's index,
    and candidates are ordered by device id — so the first device sorting before
    `DEV-F90D2` was handed `RING-1187` and collided with the shipped ring. Four
    weeks unreachable, because the fixtures build exactly one cluster. Reachable
    the moment links can arrive over HTTP.
    """
    from glassbox.graph.builder import build

    accounts = [r["account_id"] for r in fetch_all(
        conn, "SELECT account_id FROM accounts WHERE account_id NOT IN "
              "(SELECT subject_id FROM cluster_members WHERE subject_type='account') "
              "ORDER BY account_id LIMIT 4")]
    # 'DEV-500' sorts BEFORE 'DEV-F90D2', so this is exactly the collision.
    arrivals.ingest(conn, "entity_links", [
        dict(from_type="device", from_id="DEV-500", to_type="account",
             to_id=account, link_type="opened_on", first_seen=LATER)
        for account in accounts])
    build(conn)

    ids = {r["natural_key"]: r["cluster_id"] for r in fetch_all(
        conn, "SELECT natural_key, cluster_id FROM clusters")}
    assert ids["device_fanout:DEV-F90D2"] == "RING-1187", "the signed-off id is kept"
    assert ids["device_fanout:DEV-500"] != "RING-1187"
    assert len(set(ids.values())) == len(ids), "no two clusters share an id"


# ------------------------------------------------------------------- over http
@pytest.fixture(scope="module")
def client(built_database):
    os.environ["GLASSBOX_DSN"] = built_database
    from glassbox.api.app import app
    with TestClient(app) as c:
        yield c


@pytest.fixture
def clean_arrivals(built_database):
    """Take back out whatever an HTTP test committed. See test_authorize.py."""
    yield
    with psycopg.connect(built_database, row_factory=dict_row, autocommit=True) as c:
        c.execute("DELETE FROM transactions WHERE source <> 'generated'")
        c.execute("DELETE FROM events WHERE source <> 'generated'")
        c.execute("DELETE FROM entity_links WHERE source <> 'generated'")
        c.execute("DELETE FROM devices WHERE source <> 'generated'")


def test_ingesting_is_admin_only(client, clean_arrivals):
    body = {"transactions": [{"txn_id": "HTTP-ING-ROLE", "amount": "5.00",
                              "card_id": "CARD-4417", "occurred_at": LATER.isoformat()}]}
    assert client.post("/ingest/transactions", json=body).status_code == 401
    assert client.post("/ingest/transactions", headers=ANALYST,
                       json=body).status_code == 403
    assert client.post("/ingest/transactions", headers=ADMIN,
                       json=body).status_code == 200


def test_the_endpoint_serves_a_schema_valid_receipt(client, clean_arrivals):
    response = client.post("/ingest/transactions", headers=ADMIN, json={
        "transactions": [
            {"txn_id": "HTTP-ING-1", "amount": "5.00", "card_id": "CARD-4417",
             "occurred_at": LATER.isoformat()},
            {"txn_id": "HTTP-ING-2", "amount": "6.00", "card_id": "CARD-NOPE",
             "occurred_at": LATER.isoformat()},
        ]})
    receipt = IngestReceipt.model_validate(response.json())

    assert receipt.relation == "transactions"
    assert receipt.submitted == 2 and receipt.written == 1
    assert [r.index for r in receipt.rejected] == [1]
    assert "next cycle" in receipt.basis, (
        "the receipt must say that nothing here decided anything")


def test_an_empty_batch_is_refused_by_the_model(client):
    assert client.post("/ingest/transactions", headers=ADMIN,
                       json={"transactions": []}).status_code == 422
