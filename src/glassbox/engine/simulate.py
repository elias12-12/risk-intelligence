"""Evaluating without acting.

Three simulations are planned (WEEK5-PLAN.md §2): re-score an existing subject,
what-if a candidate rule over history, and score a hypothetical transaction. They
differ in what the caller is allowed to fabricate — nothing, a rule, an event —
and they share one property, which is that **nothing survives the call**.

`simulation_scope` is that property, made structural. Sessions 2 and 4 fabricate
rows to get their answer; this session's simulation writes nothing at all and
still goes through the scope, because a discipline that applies only where it is
currently needed is a discipline nobody can rely on later.

Two guards, and the first one matters more than it looks:

  * **An autocommit connection is refused outright.** `conn.transaction()` on an
    autocommit connection is a no-op that silently commits, so the one line
    standing between "simulate a rule" and "publish a rule" would quietly do
    nothing. That is not a failure worth making recoverable, so it raises.
  * **force_rollback**, so the exit path is rollback whether the body raised or
    returned. Nested inside an outer transaction — which is every test, and every
    request handler that opened one — psycopg uses a SAVEPOINT, so the caller's
    transaction survives and only the simulation's work is undone.

Simulation is also the machinery shadow mode wants (D2): a shadow rule is one
that evaluates and persists a decision but takes no action, and a simulation is
one that evaluates and persists nothing. Session 3 builds the first out of the
second rather than writing a second evaluator.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime
from typing import Iterator

import psycopg

from ..config import reference_now
from .evaluation import EngineContext, EvaluationResult, evaluate, plan_evaluations


class SimulationUnsafe(RuntimeError):
    """The scope cannot guarantee rollback, so the simulation does not run."""


class SubjectNotEvaluable(LookupError):
    """No evaluation exists for this (subject, lane) — not an error in the
    engine, an answer about the request."""


@contextmanager
def simulation_scope(conn: psycopg.Connection) -> Iterator[psycopg.Connection]:
    if conn.autocommit:
        raise SimulationUnsafe(
            "simulation refuses an autocommit connection: conn.transaction() "
            "cannot roll back what autocommit has already committed, so the "
            "guarantee this scope exists to provide would be silently absent."
        )
    with conn.transaction(force_rollback=True):
        yield conn


def simulate_subject(conn: psycopg.Connection, subject_type: str, subject_id: str,
                     lane: str, as_of: datetime | None = None,
                     replay_as_of: datetime | None = None,
                     ctx: EngineContext | None = None) -> EvaluationResult:
    """Re-derive one subject's decision. Existing rows, existing rules, existing
    feature values; nothing fabricated and nothing written.

    The request is rebuilt through `plan_evaluations`, not hand-assembled, so a
    simulation sees the same trigger row, the same `occurred_at` and the same
    evaluation trigger the cycle would have produced. A hand-built request would
    be a second planner, and S-077 — whose subject is an account and whose IP
    condition resolves against the triggering transfer — is exactly the case
    where the two would diverge.

    `replay_as_of` is the `computed_at` ceiling: it bounds how far into our own
    knowledge the evaluation may see, which is what makes "what did this look
    like on 14 January?" answerable rather than a re-score with today's values.
    """
    ctx = ctx or EngineContext.load(conn)
    as_of = as_of or reference_now()

    plans = plan_evaluations(conn, ctx, lane, as_of, run_id="sim",
                             subject_ids=[subject_id])
    matching = [p for p in plans
                if p.subject.type == subject_type and p.subject.id == subject_id]
    if not matching:
        raise SubjectNotEvaluable(
            f"no {lane} evaluation is planned for {subject_type}:{subject_id} "
            f"at as_of={as_of.isoformat()} — either no rule targets this subject "
            f"type in this lane, or the subject has no triggering row at or "
            f"before that instant"
        )

    request = matching[0]
    if replay_as_of is not None:
        request = replace(request, replay_as_of=replay_as_of)

    with simulation_scope(conn):
        return evaluate(conn, request, ctx=ctx)
