from __future__ import annotations

from fastapi import FastAPI

from .routes_alerts import router

app = FastAPI(
    title="GlassBox read API",
    version="1.0.0",
    description="Serves the frozen alert.v1 contract. Read-only.",
)
app.include_router(router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
