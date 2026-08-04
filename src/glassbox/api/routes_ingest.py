"""ingest.v1 — the doors a row comes in through, and the cycle that reacts.

**Admin only, all of it.** Ingestion is a system-to-system act and neither demo
user really represents a payment processor; with two principals, admin is the
honest place to put it. A real deployment gives the feed its own service
principal, and nothing downstream changes when it does, because everything here
consumes an actor *string* (`api/auth.py`).

**Why `/authorize` is a different endpoint from `/ingest/transactions` rather
than a flag on one.** They make opposite guarantees. One asks the engine for a
decision and writes the row carrying it; the other reports a decision somebody
else already made. A single endpoint with `decide=true` would be one typo away
from silently approving a charge the engine was never asked about — the same
argument WEEK5-PLAN decision 6 makes for keeping simulate and publish apart.

Every write path here commits exactly once, at the end of the handler. A
refusal, a contract violation or an exception all leave the connection closing
without a commit, which is a rollback.
"""
from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException

from .. import scheduler as scheduler_mod
from ..contract.ingest import (
    AuthorizationOutcome,
    AuthorizationRequest,
    CycleReport,
    EventBatch,
    ExecutionIssued,
    IngestReceipt,
    LinkBatch,
    Rejected,
    TransactionBatch,
)
from ..contract.models import Action, Evidence, Signal, Subject, is_contract_violation
from ..db import connect
from ..engine.evaluation import EngineContext
from ..engine.persist import ranked_signals
from ..ingest import arrivals, watermark
from ..ingest.authorize import NoInlineRules, authorize, executions_for, routing_for
from ..ingest.cycle import run_cycle
from ..ingest.records import RecordRefused
from ..types import jsonable
from .auth import Principal, require_role

router = APIRouter()


@router.post("/authorize", response_model=AuthorizationOutcome)
def authorize_charge(body: AuthorizationRequest,
                     who: Principal = Depends(require_role("admin"))):
    """Decide a charge, and write the row carrying the decision.

    This is the only endpoint in the project that can stop something. The action
    precedence §7 describes has always chosen `challenge` or `block` correctly;
    what it has never had is a charge whose fate depended on the answer. Here it
    does: a preventive action means the row is committed as `declined`, and
    because raw capture is append-only there is no moment at which a blocked
    charge existed as an approved transaction.

    Synchronous by design. The inline lane's budget is 50 ms p99 (§2.1) and this
    is where that number stops being a design target and starts being something
    a caller can measure — `latency_ms` rides on the response for exactly that
    reason, and it is honest about including the feature pass.
    """
    with connect() as conn:
        ctx = EngineContext.load(conn)
        try:
            decided = authorize(conn, body.columns(), ctx=ctx)
        except RecordRefused as exc:
            raise HTTPException(status_code=422, detail=exc.reasons) from exc
        except NoInlineRules as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        try:
            payload = _to_outcome(conn, decided, ctx)
        except Exception as exc:                                  # noqa: BLE001
            if is_contract_violation(exc):
                # Same standard as an alert: a payload whose numbers do not
                # explain themselves is a 500, and nothing is committed.
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            raise

        conn.commit()
        return payload


@router.post("/ingest/transactions", response_model=IngestReceipt)
def ingest_transactions(body: TransactionBatch,
                        who: Principal = Depends(require_role("admin"))):
    """Transactions that already settled, including declines.

    The door for planting a card-testing burst: `merchant_decline_burst` counts
    declines, and an authorization request cannot describe one because the
    engine would be deciding it.
    """
    return _ingest("transactions",
                   [r.model_dump(exclude_none=True) for r in body.transactions])


@router.post("/ingest/events", response_model=IngestReceipt)
def ingest_events(body: EventBatch,
                  who: Principal = Depends(require_role("admin"))):
    """The behavioural log. Without it S-077 cannot fire, whatever the transfer
    looks like: its first condition is a password reset N minutes before the
    movement, and that fact lives nowhere else."""
    return _ingest("events", [r.model_dump(exclude_none=True) for r in body.events])


@router.post("/ingest/links", response_model=IngestReceipt)
def ingest_links(body: LinkBatch,
                 who: Principal = Depends(require_role("admin"))):
    """Edges in the link layer, which is what the graph builder reads.

    The only way to reach L-203. A ring is discovered from `entity_links`, so a
    hundred ingested transfers between four accounts produce no cluster and no
    network subject to score — the `opened_on` edges are the pattern.
    """
    return _ingest("entity_links", [r.model_dump(exclude_none=True) for r in body.links])


@router.post("/cycle", response_model=CycleReport)
def run_one_cycle(who: Principal = Depends(require_role("admin"))):
    """Turn the engine over once, now, without waiting for the scheduler.

    The demo control, and the test seam. Same function the background thread
    calls — there is no second cycle implementation, because a second one would
    be a second answer about what a cycle does.
    """
    with connect() as conn:
        result = run_cycle(conn)
        if result.ran:
            conn.commit()
        return CycleReport(
            ran=result.ran, reason=result.reason, as_of=result.as_of,
            since=result.since, clusters=result.clusters,
            feature_values=result.feature_values, lanes=result.lanes,
            duration_ms=_ms(result.duration_ms))


@router.get("/cycle")
def cycle_state(who: Principal = Depends(require_role("analyst"))):
    """Is anything actually running, and how far has it got?

    Published rather than implied. A console that claims the system is live
    while nothing ticks is the same unearned claim §11 objects to in the KPI
    copy, so the answer comes from `ingest_watermark` and from the scheduler
    itself rather than from a hardcoded `true`.
    """
    running = scheduler_mod.get()
    with connect() as conn:
        streams = watermark.state(conn)
        frontier = watermark.frontier(conn)
    return {
        "scheduler_running": bool(running and running.running),
        "interval_seconds": running.interval if running else scheduler_mod.interval_seconds(),
        "started_at": running.started_at if running else None,
        "frontier": frontier,
        "streams": streams,
        "recent_ticks": [t.__dict__ for t in (running.ticks[-5:] if running else [])],
    }


# ---------------------------------------------------------------- internals
def _ingest(relation: str, rows: list[dict]) -> IngestReceipt:
    with connect() as conn:
        receipt = arrivals.ingest(conn, relation, rows)
        conn.commit()
    return IngestReceipt(
        relation=receipt.relation, submitted=receipt.submitted,
        written=receipt.written, duplicates=receipt.duplicates,
        rejected=[Rejected(index=i, reasons=r) for i, r in receipt.rejected],
        devices_registered=receipt.devices_registered,
        max_occurred_at=receipt.max_occurred_at,
    )


def _to_outcome(conn, decided, ctx) -> AuthorizationOutcome:
    """The engine's answer, in the same shape an alert publishes.

    Signals come from `engine.persist.ranked_signals` — the one function that
    also writes a stored alert's bar — so the bar this endpoint returns and the
    bar the alert it just created will render cannot be ordered differently or
    contain different rows.
    """
    result = decided.result
    request, outcome = result.request, result.outcome
    source = ctx.rules.get(outcome.action_source_rule) if outcome.action_source_rule else None
    routed = routing_for(conn, result.decision_id) if result.decision_id else None

    return AuthorizationOutcome(
        txn_id=decided.txn_id,
        subject=Subject(type=request.subject.type, id=request.subject.id),
        occurred_at=request.occurred_at,
        authorization=decided.authorization,
        decline_reason=decided.decline_reason,
        device_registered=decided.device_registered,
        decision_id=result.decision_id,
        score=result.pool.subject_score,
        band=result.band,
        action=Action(
            taken=outcome.action,
            source_rule=outcome.action_source_rule,
            vetoed_by=outcome.vetoed_by,
            prevent_threshold_met=outcome.prevent_threshold_met,
            recommended_text=source.recommended_action_text if source else None,
            clear_text=source.clear_text if source else None,
        ),
        signals=[
            Signal(
                rank=s.rank, feature_key=s.feature_key, contribution=s.contribution,
                direction=s.direction, human_text=s.human_text,
                reason_code=s.reason_code, source_rule_id=s.source_rule_id,
                asserted_by_rules=list(s.asserted_by_rules),
                feature_value=jsonable(s.feature_value),
                value_as_of=s.value_as_of, value_computed_at=s.value_computed_at,
            )
            for s in ranked_signals(result)
        ],
        evidence=Evidence(
            evaluation_id=request.evaluation_id,
            evaluation_trigger=request.evaluation_trigger,
            trigger_type=request.trigger.type if request.trigger else None,
            trigger_id=request.trigger.id if request.trigger else None,
            pit_bound_at=result.pit_bound_at,
            replay_as_of=request.replay_as_of,
            degraded_features=list(result.degraded_features),
            rule_version_set=dict(result.rule_version_set),
            feature_version_set=dict(result.feature_version_set),
        ),
        alert_id=routed["alert_id"] if routed else None,
        alert_routing=routed["alert_routing"] if routed else None,
        executions=[
            ExecutionIssued(execution_id=e["execution_id"], action=e["action"],
                            channel=e["channel"], issued_at=e["issued_at"])
            for e in (executions_for(conn, result.decision_id)
                      if result.decision_id else [])
        ],
        latency_ms=_ms(decided.latency_ms),
    )


def _ms(value: float | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(round(value, 1)))
