"""§10's substrate — the pre-consolidation condition ledger.

alert_signals cannot answer §10's question twice over: it holds only the ALERTED
subjects, and only the signals that SURVIVED consolidation. What dedup discards is
exactly the per-condition attribution the report needs, and 9,916 of 9,923
decisions leave no signal row at all.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from glassbox.db import fetch_all, fetch_one, fetch_value

PERSIST = Path(__file__).resolve().parents[1] / "src" / "glassbox" / "engine" / "persist.py"


def test_every_condition_of_every_applicable_rule_is_recorded(conn):
    """Not just the ones that fired. A fire RATE needs its denominator, and the
    denominator is every condition the engine actually looked at."""
    mismatches = fetch_all(
        conn,
        """
        WITH expected AS (
            SELECT d.decision_id, COUNT(rc.condition_id) AS n
              FROM decisions d
              JOIN rule_definitions r
                ON r.subject_type = d.subject_type
               AND r.execution_mode = d.execution_mode
              JOIN rule_conditions rc ON rc.rule_id = r.rule_id
             GROUP BY d.decision_id
        ), actual AS (
            SELECT decision_id, COUNT(*) AS n FROM decision_conditions GROUP BY decision_id
        )
        SELECT e.decision_id, e.n AS expected, COALESCE(a.n, 0) AS actual
          FROM expected e
          LEFT JOIN actual a ON a.decision_id = e.decision_id
         WHERE COALESCE(a.n, 0) <> e.n
         LIMIT 5
        """)
    assert mismatches == []


def test_not_fired_and_absent_conditions_are_both_recorded(conn):
    """The three shapes that alert_signals can never carry: a condition that
    resolved and did not fire, one whose evidence never arrived, and one whose
    feature does not apply to the subject at all."""
    spread = {(r["read_status"], r["fired"]): r["n"] for r in fetch_all(
        conn, "SELECT read_status, fired, count(*) AS n "
              "FROM decision_conditions GROUP BY 1, 2")}
    assert spread[("present", True)] > 0
    assert spread[("present", False)] > 0
    assert spread[("absent", False)] > 0
    assert sum(n for (status, _), n in spread.items() if status == "unresolvable") > 0
    # A fired row must have had a value to fire on. Enforced by CHECK too; asserted
    # here because it is the property that makes fire rates trustworthy.
    assert not any(fired for (status, fired) in spread if status != "present")


def test_the_ledger_does_not_claim_to_sum_to_the_score(conn):
    """Deliberate, and pinned so it is not rediscovered as a bug.

    Three independent policies sit between a fired condition and the score, and
    the ledger records the evidence BEFORE all three. TXN-48251 is the
    satisfaction gate: R-114's conditions fire, R-114 is not satisfied, so nothing
    they priced was contributed.
    """
    row = fetch_one(
        conn,
        """
        SELECT d.score,
               COALESCE(SUM(dc.priced_points) FILTER (WHERE dc.fired), 0) AS priced_fired,
               COALESCE(SUM(dc.contributed), 0) AS contributed
          FROM decisions d
          JOIN decision_conditions dc ON dc.decision_id = d.decision_id
         WHERE d.subject_id = 'TXN-48251' AND d.execution_mode = 'inline_sync'
         GROUP BY d.decision_id, d.score
         LIMIT 1
        """)
    assert row["priced_fired"] > row["contributed"], (
        "R-114 fires conditions on TXN-48251 without being satisfied; the priced "
        "total must exceed what was contributed")
    assert row["score"] == Decimal(31)

    # The mitigator-only case, and there are thousands of them: any traveller
    # paying by chip-and-PIN trips T-021's mitigators with no aggravator in the
    # pool. The ledger records the deductions; the score is 0, because a deduction
    # from an accusation nobody made is not "safer than nothing".
    mitigated = fetch_one(
        conn,
        """
        SELECT d.score, SUM(dc.contributed) AS contributed
          FROM decisions d
          JOIN decision_conditions dc ON dc.decision_id = d.decision_id
         WHERE d.execution_mode = 'inline_sync'
         GROUP BY d.decision_id, d.score
        HAVING SUM(dc.contributed) < 0
         LIMIT 1
        """)
    assert mitigated is not None, "the population contains mitigator-only decisions"
    assert mitigated["contributed"] < 0
    assert mitigated["score"] == Decimal(0)

    # The third policy — consolidation dedup keeping max(abs()) per
    # (feature_key, direction) — is NOT observable on the shipped fixtures: R-114
    # and T-021 both score TXN-48300 and share no feature between them, so there
    # is nothing to collapse. test_consolidation.py reaches it by inserting a
    # shared condition; recorded here so its absence is not read as a bug.


def test_contributed_is_zero_unless_the_condition_earned_it(conn):
    """The CHECK in 0023, asserted from outside: contributed is the catalog price
    only when the condition fired AND its rule was satisfied."""
    wrong = fetch_value(
        conn,
        """
        SELECT count(*) FROM decision_conditions
         WHERE contributed <> CASE WHEN fired AND rule_satisfied
                                   THEN priced_points ELSE 0 END
        """)
    assert wrong == 0


def test_the_ledger_records_which_entity_each_condition_resolved_to(conn):
    """§3.2's whole point, now observable per condition across the population:
    one transaction subject, conditions resolving to several different entities.

    R-114 reaches card, device and the transaction itself; T-021 additionally
    reaches the customer. (mcc_is_new_for_customer keys on `transaction` despite
    its name — 0015 re-catalogued it, and week1-data-model.md is superseded there.)
    """
    r114 = {r["entity_type"] for r in fetch_all(
        conn,
        """
        SELECT DISTINCT dc.entity_type
          FROM decision_conditions dc
          JOIN decisions d ON d.decision_id = dc.decision_id
         WHERE dc.rule_id = 'R-114' AND d.subject_id = 'TXN-48291'
        """)}
    assert {"card", "device", "transaction"} <= r114

    all_types = {r["entity_type"] for r in fetch_all(
        conn,
        """
        SELECT DISTINCT dc.entity_type
          FROM decision_conditions dc
          JOIN decisions d ON d.decision_id = dc.decision_id
         WHERE d.subject_id = 'TXN-48251' AND d.execution_mode = 'inline_sync'
        """)}
    assert "customer" in all_types
    assert len(all_types) >= 4, (
        "a single transaction subject resolving to four entity types is the defect "
        "§3.2 existed to fix; the old scorer joined on subject_type alone")


def test_the_fire_verdict_is_computed_in_exactly_one_place(conn):
    """persist.py must not call fires().

    Re-deriving the verdict where the ledger is written would be a second
    implementation of the firing rule — the divergence §3.1 argues about at
    length, and invisible until the two disagreed. The verdict is recorded in
    conditions.py where it is computed, and carried.
    """
    source = PERSIST.read_text(encoding="utf-8")
    assert "fires(" not in source, (
        "persist.py must read the recorded verdict, never recompute it")
    assert "co.fired" in source
