"""Week 5 §4 — simulation (c): a charge that never happened.

This is the third and last simulation, and it is the one with the most to lose.
`/simulate/subject` writes nothing at all; `/simulate/rule` fabricates a rule and
rolls it back. This path fabricates an EVENT and then runs the feature runner
over it — which means it INSERTs into `feature_values`, the append-only
bitemporal store every stored decision is replayed against. A leak there is not
a stray row. It is a value some future decision reads as the truth about an
instant, indistinguishable from one the runner computed from data that actually
arrived.

So the negative criterion is stated twice over: once as row counts across every
table the path could touch, and once directly on the primitive — the pass is
watched writing inside the scope and watched being gone after it, rather than
inferred from a total that happens to match.

The positive criterion is that the scoped pass is what makes the answer NEW.
`card_cnp_count` is stored at 5 for `CARD-4417` at the burst instant; a sixth
fabricated charge has to make the engine read 6, or the endpoint is scoring a
hypothetical charge against evidence that predates it.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from glassbox.contract.simulation import (
    TransactionDraft,
    TransactionSimulation,
    to_transaction_simulation,
)
from glassbox.db import fetch_value
from glassbox.engine.simulate import (
    FabricationRefused,
    SubjectNotEvaluable,
    insert_arrival,
    prepare,
    scoped_feature_pass,
    simulate_transaction,
    simulation_scope,
)
from glassbox.features.predicate import load_allowlist

ANALYST = {"Authorization": "Bearer analyst-token"}
ADMIN = {"Authorization": "Bearer admin-token"}

# `transactions` leads the list on purpose: this is the only simulation in the
# project that writes to it, and the only one that could leave a fabricated
# event in the raw capture layer.
WRITABLE = (
    "transactions", "feature_values", "decisions", "alerts", "alert_signals",
    "alert_subjects", "decision_conditions", "action_executions", "case_outcomes",
    "rule_definitions", "rule_conditions",
)

# The instant R-114's planted burst completes. A sixth charge here lands inside
# the 90-second window with the fifth, and DEV-F1A2 is exactly six minutes old.
BURST_AT = datetime(2026, 1, 15, 14, 32, 8, tzinfo=timezone.utc)
MCC_GIFTCARD = "5815"


def _counts(conn) -> dict[str, int]:
    return {t: fetch_value(conn, f"SELECT count(*) AS n FROM {t}") for t in WRITABLE}


def sixth_cnp_charge(**overrides) -> TransactionDraft:
    """One more card-not-present charge on the card R-114 was built around."""
    base = dict(
        txn_id="SIM-BURST-6", occurred_at=BURST_AT, amount=Decimal("468.00"),
        card_id="CARD-4417", account_id="ACC-4417", customer_id="CUST-OKAFOR",
        merchant_id="MER-GIFT", mcc=MCC_GIFTCARD, channel="cnp", entry_mode="ecom",
        txn_country="US", txn_lat=Decimal("40.71"), txn_lon=Decimal("-90.78"),
        ip_address="45.83.12.9", device_id="DEV-F1A2", billing_country="US",
    )
    base.update(overrides)
    return TransactionDraft(**base)


def ordinary_charge(**overrides) -> TransactionDraft:
    """The same customer, behaving normally: chip-and-PIN, at home, at a
    restaurant they use, on a device two years old."""
    base = dict(
        txn_id="SIM-ORDINARY", occurred_at=datetime(2026, 1, 15, 14, 50, tzinfo=timezone.utc),
        amount=Decimal("42.10"), card_id="CARD-4417", account_id="ACC-4417",
        customer_id="CUST-OKAFOR", merchant_id="MER-227", mcc="5812",
        channel="pos", entry_mode="chip_pin", txn_country="US",
        txn_lat=Decimal("40.7128"), txn_lon=Decimal("-74.006"),
        device_id="DEV-500", billing_country="US",
    )
    base.update(overrides)
    return TransactionDraft(**base)


def _publish(conn, ctx, draft: TransactionDraft, **kw) -> TransactionSimulation:
    whatif = simulate_transaction(conn, draft.columns(), ctx=ctx, **kw)
    return to_transaction_simulation(whatif, ctx.rules)


# ---------------------------------------------------------------- the finding
def test_a_fabricated_cnp_burst_trips_r114(conn, ctx):
    """Session 4's acceptance criterion, and it lands on the signed-off number.

    The charge is a sixth on a card that already carries five, so the burst it
    completes is real history plus one invented row — which is the shape the
    endpoint exists for. An admin does not fabricate a whole attack; they ask
    what one more charge would do.
    """
    published = _publish(conn, ctx, sixth_cnp_charge())

    assert published.persisted is False
    assert published.decision.score == 87
    assert published.decision.band == "high"
    assert published.decision.action.taken == "challenge"
    assert published.decision.action.source_rule == "R-114"
    assert published.decision.would_alert is True
    assert [s.feature_key for s in published.decision.signals] == [
        "card_cnp_count", "device_first_seen_min", "session_geo_jump_km",
        "mcc_is_new_for_customer",
    ]


def test_the_bar_sums_to_the_score(conn, ctx):
    """§1's invariant reaches the third simulation surface unchanged — enforced
    on `SimulatedDecision`, which this payload reuses whole rather than
    flattening."""
    published = _publish(conn, ctx, sixth_cnp_charge())
    assert sum((s.contribution for s in published.decision.signals), Decimal(0)) \
        == published.decision.score


def test_the_scoped_pass_is_what_makes_the_answer_new(conn, ctx):
    """The load-bearing assertion of the whole session.

    `card_cnp_count` for CARD-4417 is STORED at 5 at this instant. If the pass
    did not run, the read would find that 5, R-114's condition would still fire
    (>= 5), the score would still be 87 — and the endpoint would be silently
    scoring the charge that already happened rather than the one being asked
    about. The signal has to carry 6.
    """
    stored = fetch_value(
        conn,
        """
        SELECT value_num AS v FROM feature_values
         WHERE feature_key = 'card_cnp_count' AND entity_id = 'CARD-4417'
           AND as_of <= %s
         ORDER BY as_of DESC, computed_at DESC LIMIT 1
        """,
        (BURST_AT,),
    )
    assert stored == 5, "the fixture this test reasons about has moved"

    published = _publish(conn, ctx, sixth_cnp_charge())
    signal = next(s for s in published.decision.signals
                  if s.feature_key == "card_cnp_count")
    assert signal.feature_value == 6
    assert "6 card-not-present charges" in signal.human_text


def test_a_feature_keyed_on_the_transaction_would_not_exist_without_the_pass(conn, ctx):
    """The other half, and the sharper one.

    `mcc_is_new_for_customer` keys on `txn_id`, so no stored value can possibly
    exist for a transaction nobody made. Without the pass it reads `absent`,
    R-114's satisfaction gate fails on a required condition, and the rule
    contributes nothing at all — the fabricated charge would score 0 for a
    reason that has nothing to do with the charge.
    """
    assert fetch_value(
        conn,
        "SELECT count(*) AS n FROM feature_values "
        " WHERE feature_key = 'mcc_is_new_for_customer' AND entity_id = %s",
        ("SIM-BURST-6",),
    ) == 0

    published = _publish(conn, ctx, sixth_cnp_charge())
    signal = next(s for s in published.decision.signals
                  if s.feature_key == "mcc_is_new_for_customer")
    assert signal.feature_value is True
    assert published.features.recomputed["mcc_is_new_for_customer"] >= 1
    assert published.decision.evidence.degraded_features == []


def test_an_ordinary_charge_by_the_same_customer_scores_nothing(conn, ctx):
    """The endpoint has to be able to say "nothing happens", or a demo that only
    ever fabricates attacks proves nothing about the engine.

    T-021 fires two mitigators and no aggravator, so the pool does not net
    positive and consolidation drops it whole — score 0 with an empty bar, which
    is the §W4.2 behaviour reached from a row that never existed.
    """
    published = _publish(conn, ctx, ordinary_charge())

    assert published.decision.score == 0
    assert published.decision.action.taken == "allow"
    assert published.decision.would_alert is False
    assert all(s.contribution == 0 for s in published.decision.signals)


# ---------------------------------------------------------------- the guarantee
def test_a_fabricated_transaction_writes_nothing(conn, ctx):
    """Every table the path could touch, `transactions` and `feature_values`
    first."""
    before = _counts(conn)
    _publish(conn, ctx, sixth_cnp_charge())
    _publish(conn, ctx, ordinary_charge())
    assert _counts(conn) == before


def test_the_feature_pass_writes_inside_the_scope_and_is_gone_after_it(conn):
    """The trap named in the plan, tested directly rather than incidentally.

    A row-count total that matches proves the end state. This proves the
    MECHANISM: the pass really does write to `feature_values` — that is not
    theoretical, it is what makes the answer correct — and the scope really is
    the only thing taking those rows away again.
    """
    allowlist = load_allowlist(conn)
    row = prepare(conn, sixth_cnp_charge().columns(), allowlist)
    before = fetch_value(conn, "SELECT count(*) AS n FROM feature_values")

    with simulation_scope(conn):
        insert_arrival(conn, row, allowlist)
        recomputed, _split, _novelty = scoped_feature_pass(conn, row["occurred_at"])
        assert sum(recomputed.values()) > 0, "the pass computed nothing"
        inside = fetch_value(conn, "SELECT count(*) AS n FROM feature_values")
        assert inside > before, (
            "the pass must actually write — a pass that wrote nothing would leave "
            "the read to find stored values that predate the fabricated charge")
        assert fetch_value(conn, "SELECT count(*) AS n FROM transactions "
                                 " WHERE txn_id = %s", (row["txn_id"],)) == 1

    assert fetch_value(conn, "SELECT count(*) AS n FROM feature_values") == before
    assert fetch_value(conn, "SELECT count(*) AS n FROM transactions "
                             " WHERE txn_id = %s", (row["txn_id"],)) == 0


def test_the_pass_is_scoped_to_the_instant_not_to_the_population(conn):
    """A full pass would recompute 21 features across ~9,800 subjects to answer a
    question about one charge. The watermark is one microsecond wide, so what it
    writes is the fabricated row's own entities and nothing else."""
    allowlist = load_allowlist(conn)
    row = prepare(conn, sixth_cnp_charge().columns(), allowlist)

    with simulation_scope(conn):
        insert_arrival(conn, row, allowlist)
        recomputed, _split, _novelty = scoped_feature_pass(conn, row["occurred_at"])

    # Two values apiece at most: the fabricated row, and the one real charge
    # that shares its instant.
    assert max(recomputed.values()) <= 2, recomputed
    assert recomputed["card_cnp_count"] == 1


# ---------------------------------------------------------------- the refusals
def test_an_unknown_reference_is_an_answer_not_an_integrity_error(conn, ctx):
    """The FKs would refuse it anyway — by aborting mid-scope, which reaches a
    caller as a database error about a constraint rather than as "there is no
    such card"."""
    with pytest.raises(FabricationRefused) as caught:
        simulate_transaction(conn, sixth_cnp_charge(card_id="CARD-NOPE").columns(),
                             ctx=ctx)
    assert any("CARD-NOPE" in r for r in caught.value.reasons)


def test_every_problem_is_reported_at_once(conn, ctx):
    """Same standard the rule validator holds: an author fixing errors one round
    trip at a time is an author who stops using the endpoint."""
    draft = sixth_cnp_charge(card_id="CARD-NOPE", device_id="DEV-NOPE",
                             merchant_id="MER-NOPE")
    with pytest.raises(FabricationRefused) as caught:
        simulate_transaction(conn, draft.columns(), ctx=ctx)
    assert len(caught.value.reasons) == 3


def test_a_fabricated_row_may_not_borrow_a_real_id(conn, ctx):
    with pytest.raises(FabricationRefused, match="really"):
        simulate_transaction(conn, sixth_cnp_charge(txn_id="TXN-48291").columns(),
                             ctx=ctx)


def test_ground_truth_cannot_be_fabricated(conn, ctx):
    """`synthetic_label` is the denominator of §11's false-negative tile and of
    every precision number the rule what-if publishes. A hypothetical charge that
    labelled itself would be answering the question it was asked.

    Two layers, because the request model is not the only caller: the draft has
    no such field at all, and the engine refuses it anyway.
    """
    with pytest.raises(Exception):
        TransactionDraft(amount=Decimal(10), synthetic_label="legit")

    row = dict(sixth_cnp_charge().columns(), synthetic_label="legit")
    with pytest.raises(FabricationRefused, match="synthetic_label"):
        simulate_transaction(conn, row, ctx=ctx)


def test_a_column_that_is_not_a_column_is_refused(conn, ctx):
    """The engine checks names against the allow-list `predicate.py` builds from
    information_schema — the same boundary every feature spec passes through —
    so a caller reaching past the request model still cannot name anything that
    is not a real column of `transactions`."""
    row = dict(sixth_cnp_charge().columns(), drop_table="oops")
    with pytest.raises(FabricationRefused, match="drop_table"):
        simulate_transaction(conn, row, ctx=ctx)


def test_a_charge_with_no_amount_is_refused_before_the_scope_opens(conn, ctx):
    row = {k: v for k, v in sixth_cnp_charge().columns().items() if k != "amount"}
    with pytest.raises(FabricationRefused, match="amount is required"):
        simulate_transaction(conn, row, ctx=ctx)


def test_the_async_lane_says_the_charge_is_not_evaluable_there(conn, ctx):
    """No async rule takes a transaction subject, and only the transaction itself
    is scored. Saying so beats returning a zero that looks like a verdict."""
    with pytest.raises(SubjectNotEvaluable, match="async"):
        simulate_transaction(conn, sixth_cnp_charge().columns(), lane="async", ctx=ctx)


# ---------------------------------------------------------------- what it says
def test_the_row_is_echoed_as_inserted_not_as_described(conn, ctx):
    """A reader comparing a score against the charge they typed rather than the
    one that was scored is the confusion this endpoint is most able to cause, so
    the defaults the engine filled in ride on the payload."""
    published = _publish(conn, ctx, sixth_cnp_charge())
    row = published.fabricated

    assert row["txn_id"] == "SIM-BURST-6"
    assert row["currency"] == "USD" and row["txn_type"] == "purchase"
    assert row["auth_result"] == "approved" and row["direction"] == "debit"
    assert row["amount_base"] == row["amount"], "unstated, derived from amount"
    assert "synthetic_label" not in row


def test_a_generated_id_says_it_is_fabricated(conn, ctx):
    published = _publish(conn, ctx, sixth_cnp_charge(txn_id=None))
    assert published.fabricated["txn_id"].startswith("SIM-")
    assert published.decision.subject.id == published.fabricated["txn_id"]


def test_the_response_names_what_it_cannot_tell_you(conn, ctx):
    """§4's acceptance: the payload names the limits rather than implying a full
    evaluation. Derived from the pass, not written down — the novelty list is
    whichever features carry a `baseline_lag`, and the not-recomputed list is
    whatever the runner reported."""
    published = _publish(conn, ctx, sixth_cnp_charge())
    limits = {limit.code: limit for limit in published.limits}

    assert {"novelty_cannot_self_establish", "not_recomputed", "no_ground_truth",
            "transaction_subject_only"} <= set(limits)
    assert set(limits["novelty_cannot_self_establish"].features) == {
        "mcc_is_new_for_customer", "country_is_new_for_customer"}
    assert "accounts_per_device" in limits["not_recomputed"].features
    assert all(limit.detail for limit in published.limits)


def test_a_graph_feature_is_named_as_not_recomputed_rather_than_assumed(conn, ctx):
    """`accounts_per_device` is driven by `entity_links`, not by `transactions`,
    so an arriving charge does not move it — and the payload says which features
    were read at a stored value rather than leaving a reader to assume a full
    pass."""
    published = _publish(conn, ctx, sixth_cnp_charge())
    assert "accounts_per_device" not in published.features.recomputed
    assert "entity_links" in published.features.not_recomputed["accounts_per_device"]
    # The sequence runner is still unbuilt, and that is a different claim from
    # "an arriving charge does not change it".
    assert "new_payee_then_drain" in published.features.not_recomputed


# ---------------------------------------------------------------- over http
@pytest.fixture(scope="module")
def client(built_database):
    os.environ["GLASSBOX_DSN"] = built_database
    from glassbox.api.app import app
    with TestClient(app) as c:
        yield c


def _body(draft: TransactionDraft) -> dict:
    return {"transaction": draft.model_dump(mode="json", exclude_none=True)}


def test_the_hypothetical_charge_is_admin_only(client):
    """WEEK5-PLAN O4, answered. It is the one simulation that fabricates an
    EVENT, and an event is what this system's whole record is made of."""
    body = _body(sixth_cnp_charge())
    assert client.post("/simulate/transaction", json=body).status_code == 401
    assert client.post("/simulate/transaction", headers=ANALYST,
                       json=body).status_code == 403
    assert client.post("/simulate/transaction", headers=ADMIN,
                       json=body).status_code == 200


def test_the_endpoint_serves_a_schema_valid_payload(client):
    response = client.post("/simulate/transaction", headers=ADMIN,
                           json=_body(sixth_cnp_charge()))
    published = TransactionSimulation.model_validate(response.json())

    assert published.persisted is False
    assert published.decision.score == 87
    assert published.decision.action.taken == "challenge"
    assert published.limits


def test_a_refusal_is_a_422_carrying_every_reason(client):
    response = client.post(
        "/simulate/transaction", headers=ADMIN,
        json=_body(sixth_cnp_charge(card_id="CARD-NOPE", device_id="DEV-NOPE")))
    assert response.status_code == 422
    assert len(response.json()["detail"]) == 2


def test_an_unknown_field_is_rejected_by_the_closed_model(client):
    body = _body(sixth_cnp_charge())
    body["transaction"]["synthetic_label"] = "legit"
    assert client.post("/simulate/transaction", headers=ADMIN,
                       json=body).status_code == 422


def test_the_endpoint_writes_nothing(client, built_database):
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(built_database, row_factory=dict_row) as c:
        before = _counts(c)
    assert client.post("/simulate/transaction", headers=ADMIN,
                       json=_body(sixth_cnp_charge())).status_code == 200
    with psycopg.connect(built_database, row_factory=dict_row) as c:
        assert _counts(c) == before
