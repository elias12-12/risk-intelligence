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
from ..contract.simulation import (
    RuleSimulation,
    RuleSimulationRequest,
    SimulatedDecision,
    SimulationRequest,
    to_rule_simulation,
    to_simulation,
)
from ..db import connect
from ..engine.evaluation import EngineContext
from ..engine.simulate import SubjectNotEvaluable, simulate_rule, simulate_subject
from ..rules.validate import RuleInvalid, ensure_valid, normalised
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


@router.post("/simulate/rule", response_model=RuleSimulation)
def simulate_candidate_rule(body: RuleSimulationRequest,
                            who: Principal = Depends(require_role("admin"))):
    """Test a candidate rule against history before anything is written.

    **Admin only.** It is a read of the population by way of the control plane,
    and the control plane is the admin's surface; session 3's `POST /rules` takes
    the identical body and the identical validator, so an analyst who could run
    this could author against the write endpoint's exact contract without being
    allowed to use it.

    Validation happens here and again at publish, from one function
    (`rules/validate.py`) — that is the point of decision 6. A rejection is a 422
    carrying EVERY problem, not the first one, because an author fixing errors one
    round trip at a time is an author who stops using the validator.
    """
    draft = normalised(body.rule)
    with connect() as conn:
        try:
            ensure_valid(conn, draft)
        except RuleInvalid as exc:
            raise HTTPException(
                status_code=422,
                detail=[r.as_dict() for r in exc.rejections]) from exc

        whatif = simulate_rule(conn, draft, as_of=body.as_of,
                               sample_cap=body.sample_cap,
                               subject_ids=body.subject_ids)
        return to_rule_simulation(whatif, examples=body.examples,
                                  diff_limit=body.diff_limit)
        # No commit, and simulation_scope has already rolled the draft back.
