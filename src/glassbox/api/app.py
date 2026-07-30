from __future__ import annotations

from fastapi import FastAPI

from .routes_alerts import router
from .routes_queue import router as queue_router

app = FastAPI(
    title="GlassBox read API",
    version="1.0.0",
    description=("Serves the frozen alert.v1 contract, plus its siblings "
                 "queue.v1 and executions.v1. Read-only."),
)
app.include_router(router)
app.include_router(queue_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
