"""simulation.v1 — ask the engine what it would say, and write nothing down.

`POST` rather than `GET` because the body is a request object with an instant, a
lane and a replay ceiling in it, not because anything is created. `persisted:
false` rides on every response so a client never has to infer the guarantee from
the URL.

Why this endpoint is authenticated when every read here is not: it runs the
engine on demand. A read serves a stored row; this plans an evaluation, resolves
entities, reads features point-in-time and runs precedence. That is the shape of
thing that should name who asked for it, and session 2's rule what-if — which
evaluates a population — inherits the same dependency rather than inventing one.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..config import reference_now
from ..contract.models import is_contract_violation
from ..contract.simulation import SimulatedDecision, SimulationRequest, to_simulation
from ..db import connect
from ..engine.evaluation import EngineContext
from ..engine.simulate import SubjectNotEvaluable, simulate_subject
from .auth import Principal, require_role

router = APIRouter()


@router.post("/simulate/subject", response_model=SimulatedDecision)
def simulate(body: SimulationRequest,
             who: Principal = Depends(require_role("analyst"))):
    """Re-derive one subject's decision from stored rows.

    With `replay_as_of` set to a stored decision's `decided_at`, this answers
    "what did that decision see?" rather than "what would we say today?" — the
    two differ the moment a feature has been recomputed since, which is the
    failure §4 exists to prevent and the reason `feature_values` is bitemporal.
    """
    as_of = body.as_of or reference_now()
    with connect() as conn:
        ctx = EngineContext.load(conn)
        try:
            result = simulate_subject(
                conn, subject_type=body.subject_type, subject_id=body.subject_id,
                lane=body.lane, as_of=as_of, replay_as_of=body.replay_as_of,
                ctx=ctx,
            )
        except SubjectNotEvaluable as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        try:
            return to_simulation(result, ctx.rules, as_of=as_of)
        except Exception as exc:  # noqa: BLE001
            if is_contract_violation(exc):
                # Same standard as an alert: a payload whose numbers do not
                # explain themselves is a 500, never a 200.
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            raise
        # No commit. The connection closes without one, and simulation_scope has
        # already rolled back — two independent reasons nothing survives.
