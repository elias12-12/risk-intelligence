from __future__ import annotations

from fastapi import FastAPI

from .routes_alerts import router
from .routes_explain import router as explain_router
from .routes_kpis import router as kpi_router
from .routes_queue import router as queue_router

app = FastAPI(
    title="GlassBox read API",
    version="1.0.0",
    description=("Serves the frozen alert.v1 contract, plus its siblings "
                 "queue.v1, executions.v1, kpis.v1 and explanation.v1. "
                 "Read-only."),
)
app.include_router(router)
app.include_router(queue_router)
app.include_router(kpi_router)
app.include_router(explain_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
