"""The read API. Thin on purpose.

response_model= on both endpoints means the served payload is validated against
the same models the published schema is generated from, so the two cannot drift.
A ContractViolation is a 500, never a 200 with a broken score bar.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import ValidationError

from ..contract.models import AlertDetail, AlertSummary, ContractViolation, is_contract_violation
from ..contract.read import get_alert, list_alerts
from ..db import connect

router = APIRouter()


@router.get("/alerts", response_model=list[AlertSummary])
def get_alerts(status: str | None = None, band: str | None = None,
               subject_type: str | None = None,
               limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)):
    with connect() as conn:
        return list_alerts(conn, status, band, subject_type, limit, offset)


@router.get("/alerts/{alert_id}", response_model=AlertDetail)
def get_alert_detail(alert_id: int):
    with connect() as conn:
        try:
            alert = get_alert(conn, alert_id)
        except (ContractViolation, ValidationError) as exc:
            if not is_contract_violation(exc):
                raise
            # A payload that does not explain itself is a failure, not a
            # response. The product is the explanation.
            raise HTTPException(status_code=500, detail=str(exc)) from exc
    if alert is None:
        raise HTTPException(status_code=404, detail=f"no alert {alert_id}")
    return alert
