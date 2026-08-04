from __future__ import annotations

from fastapi import Depends, FastAPI

from .auth import Principal, principal
from .routes_alerts import router
from .routes_catalog import router as catalog_router
from .routes_cases import router as cases_router
from .routes_explain import router as explain_router
from .routes_kpis import router as kpi_router
from .routes_queue import router as queue_router
from .routes_rules import router as rules_router
from .routes_simulate import router as simulate_router

app = FastAPI(
    title="GlassBox read API",
    version="1.0.0",
    description=("Serves the frozen alert.v1 contract, plus its siblings "
                 "queue.v1, executions.v1, kpis.v1, explanation.v1, "
                 "dispositions.v1, simulation.v1 and catalog.v1. Reads are "
                 "open; the surfaces that leave a mark — writing a disposition, "
                 "running the engine on demand, and writing to the control "
                 "plane — require a bearer token, and everything that touches a "
                 "rule requires the admin role. A published rule lands in "
                 "shadow: it is scored and recorded, and it acts on nothing "
                 "until it is promoted."),
)
app.include_router(router)
app.include_router(catalog_router)
app.include_router(rules_router)
app.include_router(queue_router)
app.include_router(kpi_router)
app.include_router(explain_router)
app.include_router(cases_router)
app.include_router(simulate_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/me")
def me(who: Principal = Depends(principal)) -> dict[str, str]:
    """Who the presented token is, and what it may do.

    The console needs this before it can decide whether to render the admin
    surfaces at all, and a client that has to POST something to find out it is
    not allowed has already asked the user to do work it knew would fail.
    """
    return {"actor": who.actor, "role": who.role}
