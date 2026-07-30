"""The copilot and the case report — explanation.v1 (§13).

Its own router, its own contract. alert.v1 is untouched for the fourth time.

Both endpoints 500 rather than 200 on a ContractViolation, exactly as
/alerts/{id} does: an explanation that drops a mitigator is the failure §13 calls
worse than a wrong score, and returning it with a 200 would be the system lying
politely.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import ValidationError

from ..contract.explanation import CaseReport, CopilotResponse
from ..contract.models import is_contract_violation
from ..db import connect
from ..explain import answer_chips, build_report, load

router = APIRouter()


def _evidence(conn, alert_id: int):
    evidence = load(conn, alert_id)
    if evidence is None:
        raise HTTPException(status_code=404, detail="no such alert")
    return evidence


@router.get("/alerts/{alert_id}/copilot", response_model=CopilotResponse)
def get_copilot(alert_id: int) -> CopilotResponse:
    """The three chips, answered from this alert's rows and nothing else."""
    with connect() as conn:
        try:
            return answer_chips(_evidence(conn, alert_id))
        except (ValidationError, Exception) as exc:  # noqa: BLE001
            if is_contract_violation(exc):
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            raise


@router.get("/alerts/{alert_id}/report", response_model=CaseReport)
def get_case_report(alert_id: int) -> CaseReport:
    """The filing draft. `markdown` carries its own draft notice."""
    with connect() as conn:
        try:
            return build_report(_evidence(conn, alert_id))
        except (ValidationError, Exception) as exc:  # noqa: BLE001
            if is_contract_violation(exc):
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            raise
