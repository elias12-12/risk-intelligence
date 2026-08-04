"""Week 5 §3 — D2: what a shadow rule does, and what it must never do.

Before session 3 this file could not have been written. `catalog.load_rules`
selected `status IN ('active','shadow')` and `Rule.status` was never read again,
so a rule authored as shadow scored, alerted and issued preventive executions
exactly like a live one — a rule that starts challenging customers the moment it
is saved, under a label that says it is only watching.

The gate is asserted from both sides, because only one of them is the safety
property and only the other one makes shadow mode worth having:

  * a shadow rule contributes NOTHING — no signal, no score, no band change, no
    authority, no alert, no execution, and no veto;
  * a shadow rule RECORDS everything — every condition it looked at, flagged, and
    the action it would have taken, so its precision is measurable before an
    admin promotes it.

`R-114` on `TXN-48291` is the fixture, because it is the case with the largest
signed-off consequence: 87, `high`, `challenge`. Shadowed, all three have to
disappear from the decision and reappear in the shadow columns.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from glassbox.db import fetch_all, fetch_one, fetch_value
from glassbox.engine.evaluation import EngineContext, evaluate
from glassbox.engine.persist import write_batch

from conftest import request_for

BURST = "TXN-48291"


def _shadowed(conn, rule_id: str = "R-114"):
    """R-114 in shadow, and a context that has seen it that way.

    `EngineContext.load` snapshots the rules, so the update has to come first —
    the same ordering trap `simulate_rule` documents.
    """
    with conn.cursor() as cur:
        cur.execute("UPDATE rule_definitions SET status = 'shadow' "
                    " WHERE rule_id = %s", (rule_id,))
    return EngineContext.load(conn)


def _evaluate(conn, ctx, subject_id: str = BURST, lane: str = "inline_sync"):
    request = request_for(conn, ctx, lane, "transaction", subject_id)
    return evaluate(conn, request, ctx=ctx)


# ---------------------------------------------------------------- the gate
def test_a_shadow_rule_puts_nothing_on_the_published_score(conn, ctx, cases):
    """87 -> 0, and the bar is empty rather than shorter.

    Not a smaller number: R-114's four conditions are the whole of this
    subject's pool, and a shadow rule may not contribute one of them.
    """
    live = _evaluate(conn, ctx)
    assert live.pool.subject_score == Decimal(cases[BURST]["score"]) == 87

    result = _evaluate(conn, _shadowed(conn))
    assert result.pool.subject_score == 0
    assert result.pool.signals == []
    assert result.outcome.authorised_rules == []
    assert result.outcome.action == "allow"


def test_a_shadow_rule_is_still_scored_and_still_says_what_it_would_have_done(conn):
    """The half that makes shadow mode worth having.

    The rule is evaluated in full — it fires, it is satisfied, it scores its 87 —
    and the decision records the action it would have carried. Without this,
    promotion is a leap of faith and `shadow` is a spelling of `inactive`.
    """
    result = _evaluate(conn, _shadowed(conn))

    shadowed = next(rs for rs in result.shadow_scores if rs.rule_id == "R-114")
    assert shadowed.score == 87
    assert shadowed.evaluation.satisfied is True
    assert not any(rs.rule_id == "R-114" for rs in result.rule_scores)

    assert result.shadow is not None
    assert result.shadow.rules == ["R-114"]
    assert result.shadow.score == 87
    assert result.shadow.action == "challenge"


def test_the_shadow_answer_is_the_whole_decision_not_the_rule_alone(conn):
    """TXN-48300 carries R-114 and T-021's veto together.

    Shadowing R-114 there must report `monitor` — what the decision would be —
    rather than the `challenge` R-114 wants on its own. Promoting a rule does
    not move it into an empty room: the veto still caps it, and consolidation
    still deduplicates it against the live rules (§6, §7).
    """
    result = _evaluate(conn, _shadowed(conn), subject_id="TXN-48300")
    assert result.shadow is not None
    assert result.shadow.action == "monitor"
    assert result.outcome.action == "allow", (
        "with R-114 in shadow nothing is left with authority, so the live "
        "decision is an allow — the veto had nothing to cap")


def test_a_shadow_veto_cannot_cap_a_live_action(conn):
    """The gate runs on the veto pass too.

    A veto is the one rule shape that changes an outcome without scoring, so a
    gate applied only to authority would leave shadow rules able to soften live
    decisions — the same class of accident in the opposite direction.
    """
    ctx = _shadowed(conn, "T-021")
    result = _evaluate(conn, ctx, subject_id="TXN-48300")
    assert result.outcome.vetoed_by is None
    assert result.outcome.action == "challenge", "R-114 is live and unopposed"
    assert result.shadow is not None
    assert result.shadow.action == "monitor", "promoting T-021 would cap it again"


# ---------------------------------------------------------------- what is stored
@pytest.fixture
def persisted_shadow(conn):
    """One shadowed evaluation of TXN-48291, written and read back."""
    ctx = _shadowed(conn)
    result = _evaluate(conn, ctx)
    written = write_batch(conn, [result])
    decision = fetch_one(
        conn, "SELECT * FROM decisions WHERE decision_id = %s", (result.decision_id,))
    return result, written, decision


def test_the_decision_records_the_shadow_answer_beside_the_real_one(persisted_shadow):
    _, _, decision = persisted_shadow
    assert decision["score"] == 0
    assert decision["action_taken"] == "allow"
    assert decision["alert_routing"] == "no_authority"
    assert decision["shadow_score"] == 87
    assert decision["shadow_action"] == "challenge"
    assert decision["shadow_rules"] == ["R-114"]


def test_no_alert_and_no_execution_come_out_of_a_shadow_rule(persisted_shadow):
    """Asserted directly, because before the gate this produced all three."""
    _, written, decision = persisted_shadow
    assert written["alerts"] == 0
    assert written["signals"] == 0
    assert written.get("executions", 0) == 0
    assert decision["alert_id"] is None


def test_the_ledger_keeps_the_shadow_firings_and_marks_them(conn, persisted_shadow):
    """§10's instrument, pointed at a rule that has not been promoted yet.

    The rows are there — that is how an admin measures a shadow rule's fire rate
    and precision on the population it will run on — and `contributed` is 0,
    which is the true statement that the firing moved no published score. The
    CHECK in 0030 is what makes that true rather than intended.
    """
    result, _, _ = persisted_shadow
    rows = fetch_all(
        conn,
        "SELECT rule_id, is_shadow, fired, rule_satisfied, priced_points, contributed "
        "  FROM decision_conditions WHERE decision_id = %s ORDER BY condition_id",
        (result.decision_id,))
    shadow_rows = [r for r in rows if r["rule_id"] == "R-114"]

    assert len(shadow_rows) == 4, "every condition R-114 looked at is still recorded"
    assert all(r["is_shadow"] for r in shadow_rows)
    assert any(r["fired"] for r in shadow_rows)
    assert all(r["contributed"] == 0 for r in shadow_rows)
    assert any(r["priced_points"] > 0 for r in shadow_rows), (
        "the price is kept beside the zero — that gap is what §10 reads")


def test_the_ledger_check_refuses_a_shadow_row_that_contributed(conn, persisted_shadow):
    """Three layers, as everywhere else here: the engine writes 0, and the
    database refuses anything else."""
    result, _, _ = persisted_shadow
    condition_id = fetch_value(
        conn,
        "SELECT condition_id AS c FROM rule_conditions WHERE rule_id = 'R-114' "
        "ORDER BY condition_id LIMIT 1")
    with conn.cursor() as cur:
        try:
            cur.execute(
                "UPDATE decision_conditions SET contributed = priced_points "
                " WHERE decision_id = %s AND condition_id = %s",
                (result.decision_id, condition_id))
            rejected = False
        except Exception:                      # noqa: BLE001 - CheckViolation
            rejected = True
    assert rejected, "ck_dc_contributed must reject a shadow row that scored"
    conn.rollback()


def test_nothing_on_the_shipped_fixtures_is_in_shadow(conn):
    """The columns are NULL across the whole population, which is what makes
    this migration a no-op on every stored number."""
    assert fetch_value(
        conn, "SELECT count(*) AS n FROM decisions WHERE shadow_action IS NOT NULL") == 0
    assert fetch_value(
        conn, "SELECT count(*) AS n FROM decision_conditions WHERE is_shadow") == 0
    assert fetch_value(
        conn, "SELECT count(*) AS n FROM rule_definitions WHERE status = 'shadow'") == 0
