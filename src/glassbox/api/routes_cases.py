"""The analyst's verdict — dispositions.v1.

The first write endpoint in the project. Three things it deliberately does not
do, each of which would have been the shorter code:

  * It does not touch `alerts.status`. The engine owns the alert lifecycle; the
    analyst owns the verdict. Two writers on one column is how a status stops
    meaning anything.
  * It does not update a previous disposition. A correction is a new row, and
    `v_kpi_cases` publishes the latest as the case's verdict.
  * It does not take `analyst_id` from the request body. It comes from the
    authenticated principal, or the audit column records whoever the caller said
    they were — which is worse than no audit column, because it looks like one.

The GET is public, like every other read here. The POST is not.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..contract.dispositions import (
    CaseVerdict,
    DispositionRequest,
    alert_exists,
    read_verdict,
    write_disposition,
)
from ..db import connect
from .auth import Principal, require_role

router = APIRouter()


@router.get("/alerts/{alert_id}/outcome", response_model=CaseVerdict)
def get_outcome(alert_id: int):
    """The case's verdict and every disposition behind it.

    An undispositioned case returns a verdict of `null` rather than a 404: the
    alert exists and nobody has judged it, which is a real answer and the one the
    queue is built on.
    """
    with connect() as conn:
        if not alert_exists(conn, alert_id):
            raise HTTPException(status_code=404, detail="no such alert")
        return read_verdict(conn, alert_id)


@router.post("/alerts/{alert_id}/outcome", response_model=CaseVerdict,
             status_code=201)
def post_outcome(alert_id: int, body: DispositionRequest,
                 who: Principal = Depends(require_role("analyst"))):
    """Mark a case: confirmed fraud, false positive, confirmed legitimate, or
    inconclusive. Returns the whole case state, including the history, so a
    console never has to ask a second time whether the verdict stuck."""
    with connect() as conn:
        if not alert_exists(conn, alert_id):
            raise HTTPException(status_code=404, detail="no such alert")
        verdict = write_disposition(conn, alert_id, body, actor=who.actor)
        conn.commit()
        return verdict
