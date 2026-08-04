"""Week 5 — the authorization path: the first thing here that can stop a charge.

Every previous week detected fraud correctly and could not prevent any of it.
Transactions arrived already stamped `auth_result='approved'`, so a `challenge`
decision was a note attached to money that had already moved. §7.3 argues that
prevention needs a higher threshold *because a wrong block costs a customer* —
and nothing in this system could ever have cost a customer anything, because
nothing in it could stop a charge.

The test that carries this module is `test_the_fifth_charge_of_a_burst_is_stopped`:
five card-not-present charges arrive one at a time, twenty seconds apart, on a
device nobody has seen before. The first four are approved. The fifth scores 87,
is **declined**, raises a case and issues a step-up — and the row committed for
it says `declined`, not `approved`. That is the difference between detection and
prevention, and it is one assertion.

The negative criterion is the mirror of session 4's: this path is *supposed* to
write. What it must not do is write a charge whose stored `auth_result` disagrees
with the decision stored beside it.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from glassbox.contract.ingest import AuthorizationOutcome, AuthorizationRequest
from glassbox.db import fetch_all, fetch_one, fetch_value
from glassbox.ingest.authorize import APPROVED, DECLINED, authorize
from glassbox.ingest.records import RecordRefused

ADMIN = {"Authorization": "Bearer admin-token"}
ANALYST = {"Authorization": "Bearer analyst-token"}

# The burst starts at the fixtures' reference instant. It has to sit near the
# dataset rather than at wall clock: every window feature (90s, 24h, 30d) is
# measured against history pinned to 2026-01-15, and a charge dated today would
# see a card with no past at all.
START = datetime(2026, 1, 15, 15, 0, 0, tzinfo=timezone.utc)
GIFT_CARD_MCC = "5815"

# ~1,412 km from CUST-OKAFOR's home in NYC, which is what makes
# session_geo_jump_km clear R-114's 1,400 line.
AWAY_LAT, AWAY_LON = Decimal("40.71"), Decimal("-90.78")


def cnp_charge(n: int, device: str = "DEV-TEST-BURST", **overrides
               ) -> AuthorizationRequest:
    """One card-not-present charge in a burst, on a device first seen at n=0."""
    base = dict(
        txn_id=f"AUTHTEST-{n}", occurred_at=START + timedelta(seconds=20 * n),
        amount=Decimal("312.00"), card_id="CARD-4417", account_id="ACC-4417",
        customer_id="CUST-OKAFOR", merchant_id="MER-GIFT", mcc=GIFT_CARD_MCC,
        channel="cnp", entry_mode="ecom", txn_country="US",
        txn_lat=AWAY_LAT, txn_lon=AWAY_LON, ip_address="45.83.12.9",
        device_id=device, billing_country="US",
    )
    base.update(overrides)
    return AuthorizationRequest(**base)


def _authorize(conn, ctx, request: AuthorizationRequest):
    return authorize(conn, request.columns(), ctx=ctx)


# ------------------------------------------------------------ the whole point
def test_the_fifth_charge_of_a_burst_is_stopped(conn, ctx):
    """Five charges arrive one at a time. Four go through; the fifth does not.

    This is the demo, and every part of it is load-bearing. The charges are
    authorized ONE AT A TIME, each seeing only what arrived before it — so the
    burst builds the way a real one does rather than being handed to the engine
    complete. `card_cnp_count` reaches 5 on the fifth charge and R-114's other
    three conditions hold, which is 87 against a prevent_threshold of 85.
    """
    outcomes = [_authorize(conn, ctx, cnp_charge(n)) for n in range(5)]
    first_four, fifth = outcomes[:4], outcomes[4]

    assert all(o.authorization == APPROVED for o in first_four), (
        "an ordinary charge must go through; a system that declines everything "
        "has not demonstrated prevention")
    assert all(o.result.pool.subject_score == 0 for o in first_four)

    assert fifth.authorization == DECLINED
    assert fifth.decline_reason == "step_up_required"
    assert fifth.result.pool.subject_score == 87
    assert fifth.result.outcome.action == "challenge"
    assert fifth.result.outcome.action_source_rule == "R-114"
    assert fifth.result.outcome.prevent_threshold_met is True


def test_the_row_committed_for_a_declined_charge_says_declined(conn, ctx):
    """The assertion that separates prevention from a note in a log.

    Raw capture is append-only and the write-back happens inside the same
    uncommitted transaction as the INSERT, so there is no moment at which a
    blocked charge existed in this database as an approved transaction.
    """
    for n in range(5):
        decided = _authorize(conn, ctx, cnp_charge(n))

    stored = fetch_one(conn, "SELECT auth_result, decline_reason, source "
                             "  FROM transactions WHERE txn_id = %s",
                       (decided.txn_id,))
    assert stored["auth_result"] == "declined"
    assert stored["decline_reason"] == "step_up_required"
    assert stored["source"] == "authorized"


def test_a_stopped_charge_raises_a_case_and_issues_the_step_up(conn, ctx):
    """§8's execution rows, from a charge that arrived a second ago.

    Until now every `action_executions` row in this project came from a fixture
    the generator planted. This one was caused by an event that did not exist
    when the request began.
    """
    for n in range(5):
        decided = _authorize(conn, ctx, cnp_charge(n))

    routing = fetch_one(conn, "SELECT alert_id, alert_routing FROM decisions "
                              " WHERE decision_id = %s", (decided.result.decision_id,))
    assert routing["alert_routing"] == "raised"
    assert routing["alert_id"] is not None

    issued = {r["action"]: r["channel"] for r in fetch_all(
        conn, "SELECT action, channel FROM action_executions WHERE decision_id = %s",
        (decided.result.decision_id,))}
    assert issued.get("challenge") == "sms_otp"
    assert "notify" in issued, "severity-routed analyst notification (§8)"


def test_the_declined_charge_stops_counting_itself_as_approved(conn, ctx):
    """The second feature pass, and why it exists.

    `card_cnp_count` filters on `auth_result = 'approved'`. The charge is
    inserted as presumed-approved so that it counts itself at DECISION time —
    which is correct, the processor was on track to approve it. Once the engine
    declines it, that is no longer a true description of history, so the pass
    runs again and `feature_values` — append-only and bitemporal — ends up
    holding both answers: the one the decision saw, and the one that is true now.
    """
    for n in range(5):
        decided = _authorize(conn, ctx, cnp_charge(n))
    at = decided.row["occurred_at"]

    signal = fetch_value(
        conn,
        "SELECT feature_value AS v FROM alert_signals s JOIN alerts a "
        "    ON a.alert_id = s.alert_id "
        " WHERE a.subject_id = %s AND s.feature_key = 'card_cnp_count'",
        (decided.txn_id,))
    assert signal == 5, "the decision counted the charge it was deciding"

    values = fetch_all(
        conn,
        """
        SELECT value_num FROM feature_values
         WHERE feature_key = 'card_cnp_count' AND entity_id = 'CARD-4417'
           AND as_of = %s
         ORDER BY computed_at
        """,
        (at,))
    assert [v["value_num"] for v in values] == [5, 4], (
        "both answers survive: 5 as the decision saw it, then 4 once the "
        "charge was declined and stopped being an approved CNP charge")


# ------------------------------------------------------------- what it decides
def test_an_ordinary_charge_is_approved_and_scores_nothing(conn, ctx):
    decided = _authorize(conn, ctx, AuthorizationRequest(
        txn_id="AUTHTEST-ORDINARY", occurred_at=START, amount=Decimal("42.10"),
        card_id="CARD-4417", account_id="ACC-4417", customer_id="CUST-OKAFOR",
        merchant_id="MER-227", mcc="5812", channel="pos", entry_mode="chip_pin",
        txn_country="US", txn_lat=Decimal("40.7128"), txn_lon=Decimal("-74.006"),
        device_id="DEV-500", billing_country="US"))

    assert decided.authorization == APPROVED
    assert decided.decline_reason is None
    assert decided.result.pool.subject_score == 0
    assert decided.result.outcome.action == "allow"
    assert not fetch_all(conn, "SELECT 1 FROM action_executions WHERE decision_id = %s",
                         (decided.result.decision_id,))


def test_the_authorization_follows_ref_action_not_a_literal(conn, ctx):
    """Which actions stop a charge is read off `ref_action.is_preventive`, the
    same ordered data §7 and `engine/execute.py` read. A second copy in Python
    would go stale the first time somebody adds a rung by INSERT."""
    from glassbox.ingest.authorize import _authorization_for

    preventive = [r["action"] for r in fetch_all(
        conn, "SELECT action FROM ref_action WHERE is_preventive ORDER BY severity")]
    assert preventive, "the ladder must have preventive rungs for this to mean anything"

    for action in preventive:
        assert _authorization_for(conn, action)[0] == DECLINED, action
    for action in ("allow", "monitor", "alert"):
        assert _authorization_for(conn, action) == (APPROVED, None), action


# ---------------------------------------------------------------- the device
def test_an_unseen_device_is_registered_at_the_instant_it_is_presented(conn, ctx):
    """A device is OBSERVED; an account is OPENED. That line is why an unknown
    fingerprint comes into existence here and an unknown card is refused — and
    it is not a convenience, because `device_first_seen_min` is measured from
    exactly this instant and is worth 21 of R-114's 87 points."""
    assert fetch_value(conn, "SELECT count(*) AS n FROM devices WHERE device_id = %s",
                       ("DEV-TEST-BURST",)) == 0

    decided = _authorize(conn, ctx, cnp_charge(0))
    assert decided.device_registered is True

    row = fetch_one(conn, "SELECT first_seen, source FROM devices WHERE device_id = %s",
                    ("DEV-TEST-BURST",))
    assert row["first_seen"] == START
    assert row["source"] == "authorized"

    # ...and only once. The second charge presents the same fingerprint.
    assert _authorize(conn, ctx, cnp_charge(1)).device_registered is False


def test_a_known_device_keeps_its_original_first_seen(conn, ctx):
    before = fetch_value(conn, "SELECT first_seen AS f FROM devices WHERE device_id = %s",
                         ("DEV-500",))
    _authorize(conn, ctx, cnp_charge(0, device="DEV-500"))
    assert fetch_value(conn, "SELECT first_seen AS f FROM devices WHERE device_id = %s",
                       ("DEV-500",)) == before


# ---------------------------------------------------------------- the refusals
def test_an_authorization_may_not_state_its_own_result(conn, ctx):
    """The whole distinction between the two doors, enforced rather than
    described: `/authorize` asks for a decision, `/ingest/transactions` reports
    one. A request that could set `auth_result` could approve itself."""
    row = dict(cnp_charge(0).columns(), auth_result="approved")
    with pytest.raises(RecordRefused, match="auth_result"):
        authorize(conn, row, ctx=ctx)

    row = dict(cnp_charge(0).columns(), synthetic_label="legit")
    with pytest.raises(RecordRefused, match="synthetic_label"):
        authorize(conn, row, ctx=ctx)


def test_an_unknown_card_is_an_answer_not_an_integrity_error(conn, ctx):
    with pytest.raises(RecordRefused, match="CARD-NOPE") as caught:
        authorize(conn, cnp_charge(0, card_id="CARD-NOPE").columns(), ctx=ctx)
    assert len(caught.value.reasons) == 1


def test_a_charge_may_not_take_the_identity_of_one_already_recorded(conn, ctx):
    with pytest.raises(RecordRefused, match="already exists"):
        authorize(conn, cnp_charge(0, txn_id="TXN-48291").columns(), ctx=ctx)


def test_an_authorization_with_no_txn_id_is_given_an_obvious_one(conn, ctx):
    decided = _authorize(conn, ctx, cnp_charge(0, txn_id=None))
    assert decided.txn_id.startswith("AUTH-")


# ------------------------------------------------------------------- over http
@pytest.fixture(scope="module")
def client(built_database):
    os.environ["GLASSBOX_DSN"] = built_database
    from glassbox.api.app import app
    with TestClient(app) as c:
        yield c


@pytest.fixture
def clean_arrivals(built_database):
    """Remove everything an HTTP test committed.

    These tests cannot roll back — the API opens its own connection and a write
    endpoint that did not commit would be a write endpoint that does nothing —
    and the session database is shared with every other module, so what they
    write has to be taken back out. Watermarked on ids and on `source`, in
    foreign-key order.
    """
    marks = _marks(built_database)
    yield
    with psycopg.connect(built_database, row_factory=dict_row, autocommit=True) as c:
        c.execute("DELETE FROM action_executions WHERE execution_id > %s",
                  (marks["execution"],))
        c.execute("DELETE FROM alerts WHERE alert_id > %s", (marks["alert"],))
        c.execute("DELETE FROM decisions WHERE decision_id > %s", (marks["decision"],))
        c.execute("DELETE FROM feature_values WHERE computed_at > %s", (marks["clock"],))
        c.execute("DELETE FROM transactions WHERE source <> 'generated'")
        c.execute("DELETE FROM events WHERE source <> 'generated'")
        c.execute("DELETE FROM entity_links WHERE source <> 'generated'")
        c.execute("DELETE FROM devices WHERE source <> 'generated'")


def _marks(dsn: str) -> dict:
    with psycopg.connect(dsn, row_factory=dict_row) as c:
        return c.execute(
            """
            SELECT COALESCE(max(execution_id), 0) AS execution,
                   (SELECT COALESCE(max(alert_id), 0)    FROM alerts)    AS alert,
                   (SELECT COALESCE(max(decision_id), 0) FROM decisions) AS decision,
                   now() AS clock
              FROM action_executions
            """).fetchone()


def _body(request: AuthorizationRequest) -> dict:
    return request.model_dump(mode="json", exclude_none=True)


def test_authorizing_is_admin_only(client, clean_arrivals):
    body = _body(cnp_charge(0, txn_id="HTTP-AUTH-ROLE"))
    assert client.post("/authorize", json=body).status_code == 401
    assert client.post("/authorize", headers=ANALYST, json=body).status_code == 403
    assert client.post("/authorize", headers=ADMIN, json=body).status_code == 200


def test_the_endpoint_serves_a_schema_valid_outcome(client, clean_arrivals):
    response = client.post("/authorize", headers=ADMIN,
                           json=_body(cnp_charge(0, txn_id="HTTP-AUTH-OK")))
    published = AuthorizationOutcome.model_validate(response.json())

    assert published.persisted is True
    assert published.authorization == "approved"
    assert published.txn_id == "HTTP-AUTH-OK"
    assert published.decision_id is not None
    assert published.latency_ms is not None and published.latency_ms > 0


def test_the_burst_is_stopped_over_http_too(client, clean_arrivals):
    """End to end, through the surface a console would call."""
    results = []
    for n in range(5):
        response = client.post(
            "/authorize", headers=ADMIN,
            json=_body(cnp_charge(n, txn_id=f"HTTP-BURST-{n}",
                                  device="DEV-HTTP-BURST")))
        assert response.status_code == 200, response.text
        results.append(AuthorizationOutcome.model_validate(response.json()))

    assert [r.authorization for r in results] == \
        ["approved"] * 4 + ["declined"]
    stopped = results[-1]
    assert stopped.score == 87
    assert stopped.action.taken == "challenge"
    assert stopped.alert_routing == "raised"
    assert {e.action for e in stopped.executions} == {"challenge", "notify"}
    assert sum(s.contribution for s in stopped.signals) == stopped.score


def test_a_refusal_over_http_is_a_422_carrying_every_reason(client, clean_arrivals):
    body = _body(cnp_charge(0, txn_id="HTTP-AUTH-BAD", card_id="CARD-NOPE"))
    body["merchant_id"] = "MER-NOPE"
    response = client.post("/authorize", headers=ADMIN, json=body)
    assert response.status_code == 422
    assert len(response.json()["detail"]) == 2


def test_the_closed_model_rejects_a_caller_supplied_auth_result(client):
    body = _body(cnp_charge(0, txn_id="HTTP-AUTH-CLOSED"))
    body["auth_result"] = "approved"
    assert client.post("/authorize", headers=ADMIN, json=body).status_code == 422
