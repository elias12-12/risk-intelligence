"""The authorization path: decide, then write the decision into the row.

This is the difference between a system that DETECTS and a system that
PREVENTS, and until now this project only had the first one. Every transaction
arrived already stamped `auth_result='approved'`, so a `challenge` decision
could only ever be a note attached to a charge that had already gone through.
§7.3 argues that prevention needs a higher threshold because a wrong block costs
a customer — and nothing here could ever have cost a customer anything, because
nothing here could stop a charge.

**The order is the whole design.**

    1  register the device if this is the first time it has been seen
    2  INSERT the charge as PRESUMED APPROVED
    3  scoped feature pass at the charge's own instant
    4  plan + evaluate the inline lane
    5  precedence chooses an action
    6  the action decides the authorization; write it back
    7  persist the decision, route the alert, issue the executions
    8  recompute, because step 6 changed the answer to step 3
    9  COMMIT — all of it, or none of it

Three of those steps are not obvious and each is load-bearing.

**Step 2 presumes approval, and it has to.** `card_cnp_count` filters on
`auth_result = 'approved'`, so a row inserted with a null result would not count
itself and the sixth charge of a burst would read five. Presuming approval is
also what actually happens: the processor is on track to approve, and the risk
engine is the thing that declines. The row must exist before evaluation at all,
because `mcc_is_new_for_customer` keys on `txn_id` and there is nothing to key
on until it does.

**Step 6 writes the result the ENGINE chose, and raw capture stays honest.**
0003 calls raw capture immutable and append-only, and this does not break that:
the UPDATE happens inside the same uncommitted transaction as the INSERT, so no
reader ever observes the row holding a value other than its final one. There is
no moment at which a blocked charge existed as an approved transaction.

**Step 8 exists because step 6 invalidated step 3.** If the engine declined the
charge, the features computed in step 3 counted it as approved — which was the
correct evidence at decision time and is the wrong description of history
afterwards. So the pass runs again. `feature_values` is append-only and
bitemporal, so the second pass INSERTs rows with the same `as_of` and a later
`computed_at`: the decision keeps the evidence it actually saw, the store ends
up describing what really happened, and §4's replay reads whichever of the two
it asks for. This is the first place in the project where bitemporality earns
its keep on the write path rather than in an argument.

A note on what `challenge` means to an authorization. It is not `approved`: a
step-up that has not been answered has not been passed, and letting the money
move while asking the question would make the step-up decorative. It is written
as `declined` with `decline_reason='step_up_required'`, which is also how 3DS
actually behaves — the charge is refused and the customer retries after
authenticating. That retry is a NEW authorization, and it will be evaluated
against a card that now carries the challenge in its history.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping

import psycopg

from ..config import reference_now
from ..db import fetch_all, fetch_one, fetch_value
from ..engine.evaluation import EngineContext, EvaluationResult, evaluate, plan_evaluations
from ..engine.persist import write_batch
from ..engine.simulate import ARRIVAL_RELATION, scoped_feature_pass
from ..features.predicate import load_allowlist
from . import records

LANE = "inline_sync"
SOURCE = "authorized"

# An authorization request asks for a decision, so it may not state one. Both
# columns are the engine's to write, and `records._forbidden_reason` says so in
# a sentence the caller can act on. `synthetic_label` is refused for the reason
# it is refused everywhere on a live path: a row does not get to label itself.
UNAUTHORIZABLE_COLUMNS: frozenset[str] = frozenset(
    {"auth_result", "decline_reason", "synthetic_label", "ingested_at", "source"})

APPROVED, DECLINED = "approved", "declined"

# Why an authorization was refused, by the action that refused it. `challenge`
# is the interesting one and is not a decline in the usual sense: the charge is
# refused *pending* an answer, which is what a step-up is.
DECLINE_REASONS: dict[str, str] = {
    "challenge": "step_up_required",
    "hold": "risk_hold_pending_review",
    "block": "risk_block",
}


class NoInlineRules(RuntimeError):
    """Nothing in the inline lane takes a transaction subject, so there is no
    decision to be made. An answer about the configuration, not a failure."""


@dataclass
class Authorized:
    """What happened to one charge. The engine's terms, not the wire's."""

    row: dict[str, Any]
    result: EvaluationResult
    authorization: str
    decline_reason: str | None
    device_registered: bool
    written: dict[str, int] = field(default_factory=dict)
    latency_ms: float = 0.0

    @property
    def txn_id(self) -> str:
        return self.row["txn_id"]

    @property
    def approved(self) -> bool:
        return self.authorization == APPROVED


def authorize(conn: psycopg.Connection, request: Mapping[str, Any],
              ctx: EngineContext | None = None,
              as_of: datetime | None = None) -> Authorized:
    """Decide one charge and commit the decision with it.

    Does NOT commit — the caller owns the transaction, exactly as
    `rules/publish.publish_rule` does, so an authorization that fails while
    issuing its own step-up leaves no charge behind. The route commits once, at
    the end.
    """
    started = time.perf_counter()
    ctx = ctx or EngineContext.load(conn)
    allowlist = load_allowlist(conn)

    prepared = records.prepare(conn, ARRIVAL_RELATION, request, allowlist,
                               forbidden=UNAUTHORIZABLE_COLUMNS,
                               id_prefix="AUTH", as_of=as_of)
    prepared["auth_result"] = APPROVED          # presumed; see the module docstring
    prepared["source"] = SOURCE
    occurred_at, txn_id = prepared["occurred_at"], prepared["txn_id"]

    # Between validation and the INSERT: a charge refused for an unknown card
    # must not leave a device behind, and the foreign key fires if this runs
    # any later.
    registered = bool(records.ensure_device(conn, ARRIVAL_RELATION, prepared, SOURCE))

    records.insert_row(conn, ARRIVAL_RELATION, prepared, allowlist)
    scoped_feature_pass(conn, occurred_at)

    plans = [p for p in plan_evaluations(conn, ctx, LANE, occurred_at,
                                         run_id="auth", subject_ids=[txn_id])
             if p.subject.type == "transaction" and p.subject.id == txn_id]
    if not plans:
        raise NoInlineRules(
            f"no rule takes a transaction subject in the {LANE!r} lane, so this "
            f"charge cannot be decided. It was not written")

    result = evaluate(conn, plans[0], ctx=ctx)
    authorization, reason = _authorization_for(conn, result.outcome.action)

    if authorization != APPROVED:
        _write_back(conn, txn_id, authorization, reason)

    written = write_batch(conn, [result])

    if authorization != APPROVED:
        # Step 3 counted this charge as approved. That was the right evidence at
        # decision time and is the wrong description of history now, so the pass
        # runs again: append-only and bitemporal means the decision keeps what
        # it saw and the store ends up describing what happened.
        scoped_feature_pass(conn, occurred_at)

    return Authorized(
        row=prepared, result=result, authorization=authorization,
        decline_reason=reason, device_registered=registered, written=written,
        latency_ms=(time.perf_counter() - started) * 1000)


def _authorization_for(conn: psycopg.Connection, action: str) -> tuple[str, str | None]:
    """Does this action let the money move?

    Read off `ref_action.is_preventive` rather than a literal list, for the same
    reason `engine/execute.py` reads it there: the severity ladder is ordered
    DATA, and a second copy of "which actions stop a charge" in Python is a copy
    that goes stale the first time somebody adds a rung by INSERT.
    """
    preventive = fetch_value(
        conn, "SELECT is_preventive FROM ref_action WHERE action = %s", (action,))
    if not preventive:
        return APPROVED, None
    return DECLINED, DECLINE_REASONS.get(action, f"risk_{action}")


def _write_back(conn: psycopg.Connection, txn_id: str, authorization: str,
                reason: str | None) -> None:
    """The engine's verdict, onto the row, inside the same transaction.

    The only UPDATE this project makes to raw capture, and it is confined to a
    row this same call inserted moments earlier and has not committed. Guarded
    on `source` so it can never touch a generated or ingested row even by
    accident: those describe decisions somebody else made.
    """
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE transactions SET auth_result = %s, decline_reason = %s "
            " WHERE txn_id = %s AND source = %s",
            (authorization, reason, txn_id, SOURCE))
        if cur.rowcount != 1:      # pragma: no cover - the INSERT above made it
            raise RuntimeError(
                f"authorization write-back touched {cur.rowcount} rows for "
                f"{txn_id}; expected exactly the row this call inserted")


def executions_for(conn: psycopg.Connection, decision_id: int) -> list[dict]:
    """What the decision actually caused, for the response payload."""
    return fetch_all(
        conn,
        """
        SELECT execution_id, action, channel, issued_at
          FROM action_executions
         WHERE decision_id = %s
         ORDER BY execution_id
        """,
        (decision_id,))


def routing_for(conn: psycopg.Connection, decision_id: int) -> dict | None:
    return fetch_one(
        conn,
        "SELECT alert_id, alert_routing FROM decisions WHERE decision_id = %s",
        (decision_id,))
