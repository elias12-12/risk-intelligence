"""Week 5 §1 — simulation (a): re-derive a decision, write nothing.

The acceptance criterion that matters most here is the negative one. A
simulation that produced the right number and left a row behind would be worse
than no simulation, because the row would be indistinguishable from a decision
the system actually made — so the row counts are asserted directly, on every
table an evaluation could conceivably touch.

The positive criterion is that the simulated bar is the SAME bar. Signals come
from `engine.persist.ranked_signals`, the one function that also writes a stored
alert's signal set, so a simulated bar and the alert it predicts cannot be
ordered differently or contain different rows.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from glassbox import config
from glassbox.contract.simulation import to_simulation
from glassbox.db import fetch_value
from glassbox.engine.simulate import (
    SimulationUnsafe,
    SubjectNotEvaluable,
    simulate_subject,
    simulation_scope,
)

# Every table an evaluation writes to on the persisting path, plus the two the
# feature layer owns. If a simulation can move any of these, it is not a
# simulation.
WRITABLE = (
    "decisions", "alerts", "alert_signals", "alert_subjects",
    "decision_conditions", "action_executions", "case_outcomes", "feature_values",
)


def _counts(conn) -> dict[str, int]:
    return {t: fetch_value(conn, f"SELECT count(*) AS n FROM {t}") for t in WRITABLE}


def _simulate(conn, ctx, case, **kw):
    return simulate_subject(
        conn, subject_type=case["subject_type"], subject_id=case["subject_id"],
        lane=case["lane"], ctx=ctx, **kw)


# ---------------------------------------------------------------- the numbers
def test_every_signed_off_fixture_is_reproduced_without_being_stored(conn, ctx, expected):
    """87 / 68 / 64 / 58 / 0, derived on demand rather than read back.

    This is the same engine over the same rows, so agreement is not surprising —
    what it establishes is that the simulation path plans the SAME evaluation.
    A hand-built request would diverge on S-077, whose subject is an account and
    whose IP condition resolves against the triggering transfer.
    """
    for case in expected["cases"]:
        result = _simulate(conn, ctx, case)
        published = to_simulation(result, ctx.rules, as_of=config.reference_now())

        assert published.score == Decimal(case["score"]), case["subject_id"]
        assert published.band == case["band"], case["subject_id"]
        assert published.action.taken == case["action"], case["subject_id"]
        assert published.action.vetoed_by == case["vetoed_by"], case["subject_id"]
        assert published.would_alert == case["alert_expected"], case["subject_id"]
        assert published.persisted is False


def test_the_simulated_bar_sums_to_the_score(conn, ctx, expected):
    """The invariant travels with the payload. It is enforced in the model, so a
    simulation that broke it would raise rather than render."""
    for case in expected["cases"]:
        published = to_simulation(_simulate(conn, ctx, case), ctx.rules,
                                  as_of=config.reference_now())
        assert sum((s.contribution for s in published.signals), Decimal(0)) \
            == published.score, case["subject_id"]


def test_the_simulated_bar_is_the_stored_bar(conn, ctx, expected):
    """Same rows, same order, for a fixture that actually raised an alert."""
    case = next(c for c in expected["cases"] if c["subject_id"] == "TXN-48291")
    published = to_simulation(_simulate(conn, ctx, case), ctx.rules,
                              as_of=config.reference_now())

    stored = fetch_value(
        conn,
        """
        SELECT array_agg(s.feature_key ORDER BY s.rank) AS keys
          FROM alert_signals s
          JOIN alerts a ON a.alert_id = s.alert_id
         WHERE a.subject_id = %s
        """,
        (case["subject_id"],),
    )
    assert [s.feature_key for s in published.signals] == stored


def test_the_veto_reaches_the_simulated_payload(conn, ctx, expected):
    """TXN-48300: R-114 wants to challenge, T-021's veto holds it at monitor. The
    veto is a signal on the bar, not invisible policy."""
    case = next(c for c in expected["cases"] if c["subject_id"] == "TXN-48300")
    published = to_simulation(_simulate(conn, ctx, case), ctx.rules,
                              as_of=config.reference_now())

    assert published.action.taken == "monitor"
    assert published.action.vetoed_by == "T-021"
    assert any(s.direction == "veto" for s in published.signals)
    veto_rule = next(r for r in published.rules if r.rule_id == "T-021")
    assert veto_rule.is_veto and veto_rule.veto_established is True
    assert veto_rule.authorised is False, "a veto rule never holds action authority"


def test_the_dropped_pool_publishes_an_empty_bar(conn, ctx, expected):
    """TXN-48251 after the reprice: 12 - 9 - 6 - 4 = -7, so consolidation drops
    the pool whole. Score 0, and nothing on the bar that carries weight."""
    case = next(c for c in expected["cases"] if c["subject_id"] == "TXN-48251")
    published = to_simulation(_simulate(conn, ctx, case), ctx.rules,
                              as_of=config.reference_now())

    assert published.score == 0
    assert published.would_alert is False
    assert all(s.contribution == 0 for s in published.signals)


def test_the_rule_trace_shows_what_declined_to_fire(conn, ctx, expected):
    """The two questions an alert cannot answer: which rules looked, and which
    fired without authority."""
    case = next(c for c in expected["cases"] if c["subject_id"] == "TXN-48291")
    published = to_simulation(_simulate(conn, ctx, case), ctx.rules,
                              as_of=config.reference_now())

    assert published.rules, "no rule was traced"
    carried = [r for r in published.rules if r.authorised]
    assert [r.rule_id for r in carried] == ["R-114"]
    assert all(r.review_threshold is not None for r in published.rules)


# ---------------------------------------------------------------- the guarantee
def test_a_simulation_writes_nothing(conn, ctx, expected):
    """The criterion this endpoint lives or dies on."""
    before = _counts(conn)
    for case in expected["cases"]:
        _simulate(conn, ctx, case)
    assert _counts(conn) == before


def test_the_scope_refuses_an_autocommit_connection(built_database):
    """`conn.transaction()` on an autocommit connection is a no-op that commits.

    Session 2 fabricates a rule inside this scope and session 4 fabricates a
    transaction and runs the feature runner. On an autocommit connection both
    would publish what they were asked to pretend, so the scope raises rather
    than degrading quietly.
    """
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(built_database, row_factory=dict_row, autocommit=True) as c:
        with pytest.raises(SimulationUnsafe):
            with simulation_scope(c):
                pass          # pragma: no cover - the scope raises before this


def test_the_scope_rolls_back_what_the_body_wrote(conn):
    """Nothing in session 1 writes inside the scope, so this constructs the case
    sessions 2 and 4 depend on and proves the primitive itself."""
    before = fetch_value(conn, "SELECT count(*) AS n FROM case_outcomes")
    alert_id = fetch_value(conn, "SELECT min(alert_id) AS a FROM alerts")

    with simulation_scope(conn):
        conn.execute(
            "INSERT INTO case_outcomes (alert_id, disposition) VALUES (%s, %s)",
            (alert_id, "confirmed_fraud"),
        )
        assert fetch_value(conn, "SELECT count(*) AS n FROM case_outcomes") == before + 1

    assert fetch_value(conn, "SELECT count(*) AS n FROM case_outcomes") == before


# ---------------------------------------------------------------- the bounds
def test_a_replay_ceiling_is_actually_applied(conn, ctx, expected):
    """`replay_as_of` bounds how far into our OWN knowledge the evaluation sees.

    Every feature value in a built database was computed at bootstrap time, so a
    ceiling before that excludes all of them: the conditions cannot be evaluated,
    the rule is not satisfied, and the degradation is recorded rather than scored
    as a quiet zero. That is §5's policy observed through the simulation surface.
    """
    case = next(c for c in expected["cases"] if c["subject_id"] == "TXN-48291")
    ancient = datetime(2020, 1, 1, tzinfo=timezone.utc)

    published = to_simulation(_simulate(conn, ctx, case, replay_as_of=ancient),
                              ctx.rules, as_of=config.reference_now())

    assert published.score == 0
    assert published.action.taken == "allow"
    assert published.would_alert is False
    assert published.evidence.degraded_features, (
        "a feature the ceiling excluded must be recorded as degraded, not scored "
        "as a zero that looks like evidence of innocence")
    assert published.evidence.replay_as_of == ancient


def test_replaying_a_stored_decision_reproduces_it(conn, ctx, expected):
    """The audit question, answered by re-derivation rather than by read-back.

    `decided_at` is the ceiling — not a feature's `computed_at`, which would
    exclude every value the runner wrote after that one and is a trap the Week-2
    handoff paid for in debugging time. What this establishes is that the stored
    number and the number the engine produces from the same bounded evidence are
    the same number; `feature_values` being append-only and bitemporal is what
    keeps that true after a recomputation.
    """
    for case in expected["cases"]:
        stored = fetch_value(
            conn,
            """
            SELECT to_jsonb(d) AS d FROM decisions d
             WHERE subject_type = %s AND subject_id = %s AND execution_mode = %s
             ORDER BY decision_id DESC LIMIT 1
            """,
            (case["subject_type"], case["subject_id"], case["lane"]),
        )
        assert stored is not None, case["subject_id"]

        published = to_simulation(
            _simulate(conn, ctx, case,
                      replay_as_of=datetime.fromisoformat(stored["decided_at"])),
            ctx.rules, as_of=config.reference_now())

        assert published.score == Decimal(str(stored["score"])), case["subject_id"]
        assert published.action.taken == stored["action_taken"], case["subject_id"]
        assert published.band == stored["band"], case["subject_id"]


def test_an_unevaluable_subject_says_so_rather_than_scoring_zero(conn, ctx):
    with pytest.raises(SubjectNotEvaluable):
        simulate_subject(conn, subject_type="transaction",
                         subject_id="TXN-DOES-NOT-EXIST", lane="inline_sync", ctx=ctx)
