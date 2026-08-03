"""The queue and the executions behind an alert — queue.v1 and executions.v1.

Separate router from routes_alerts because these are separate contracts. A
console binding /alerts/{id} sees exactly the bytes alert.v1 promised, before
and after this file existed.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from pydantic import ValidationError

from ..contract.executions import ExecutionRecord, read_executions
from ..contract.models import is_contract_violation
from ..contract.queue import QueueEntry, read_queue
from ..db import connect, fetch_one

router = APIRouter()


@router.get("/queue", response_model=list[QueueEntry])
def get_queue(status: str | None = "open", subject_type: str | None = None,
              as_of: datetime | None = None, include_worked: bool = False,
              limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)):
    """Priority-ordered, not score-ordered (§9).

    `as_of` is exposed rather than hidden: recency decay makes the order a
    function of an instant, and a caller that cannot name the instant cannot
    reproduce the order it was shown.

    `include_worked` brings back cases a PERSON has dispositioned — a
    recently-closed view, not the working queue. A case closed only by the
    synthetic settler was never worked and never left (contract/queue.py).
    """
    with connect() as conn:
        try:
            return read_queue(conn, status=status, subject_type=subject_type,
                              as_of=as_of, limit=limit, offset=offset,
                              include_worked=include_worked)
        except (ValidationError, Exception) as exc:  # noqa: BLE001
            if is_contract_violation(exc):
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            raise


@router.get("/alerts/{alert_id}/executions", response_model=list[ExecutionRecord])
def get_alert_executions(alert_id: int):
    """What was actually done to the customer, and how it turned out.

    An empty list is a real answer — most alerts authorise no preventive action —
    so a 404 is reserved for an alert that does not exist.
    """
    with connect() as conn:
        if fetch_one(conn, "SELECT 1 FROM alerts WHERE alert_id = %s", (alert_id,)) is None:
            raise HTTPException(status_code=404, detail="no such alert")
        return read_executions(conn, alert_id=alert_id)
