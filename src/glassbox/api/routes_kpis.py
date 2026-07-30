"""The nine tiles — kpis.v1 (§11).

Its own router, like queue.v1's, because it is its own contract. A console
binding /alerts/{id} sees exactly the bytes alert.v1 promised, before and after
this file existed.

`window_days` and `as_of` are both exposed rather than hidden. Every tile carries
the window it was computed over, and a caller that cannot name the window cannot
reproduce a number it was shown — which for an analytics surface is the same
argument §1 makes about a score.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Query

from ..contract.kpis import KpiSet, read_kpis
from ..db import connect

router = APIRouter()


@router.get("/kpis", response_model=KpiSet)
def get_kpis(window_days: int = Query(7, ge=1, le=365),
             as_of: datetime | None = None) -> KpiSet:
    """Computed from stored rows; nothing here is illustrative.

    The default window is seven days because the dataset spans thirty: at any
    window over about fifteen the preceding equal-length window falls off the
    front of the data, the baseline becomes unavailable, and every delta is
    correctly — but uninformatively — null. `baseline_absent_reason` says so on
    the wire when it happens rather than leaving a client to infer it.
    """
    with connect() as conn:
        return read_kpis(conn, as_of=as_of, window=timedelta(days=window_days))
