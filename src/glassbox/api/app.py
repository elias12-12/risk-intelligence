from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .. import scheduler as scheduler_mod
from .auth import Principal, principal
from .routes_alerts import router
from .routes_catalog import router as catalog_router
from .routes_cases import router as cases_router
from .routes_explain import router as explain_router
from .routes_ingest import router as ingest_router
from .routes_kpis import router as kpi_router
from .routes_queue import router as queue_router
from .routes_rules import router as rules_router
from .routes_simulate import router as simulate_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start the cycle with the service, stop it with the service.

    §15's topology is "one service, one database, a scheduler" and this is the
    third of those. It is OFF unless `GLASSBOX_CYCLE_SECONDS` allows it, and the
    test suite leaves it off deliberately: `conftest.py` builds one database that
    every test then mutates inside a rolled-back transaction, and a background
    thread committing into the middle of that would be the least debuggable
    failure this project could have.
    """
    scheduler_mod.start()
    try:
        yield
    finally:
        scheduler_mod.stop()


app = FastAPI(
    title="GlassBox API",
    version="1.1.0",
    lifespan=lifespan,
    description=("Serves the frozen alert.v1 contract, plus its siblings "
                 "queue.v1, executions.v1, kpis.v1, explanation.v1, "
                 "dispositions.v1, simulation.v1, catalog.v1 and ingest.v1. "
                 "Reads are open; the surfaces that leave a mark — writing a "
                 "disposition, running the engine on demand, writing to the "
                 "control plane, and letting a row into raw capture — require a "
                 "bearer token, and everything that touches a rule or ingests "
                 "requires the admin role. A published rule lands in shadow: it "
                 "is scored and recorded, and it acts on nothing until it is "
                 "promoted. POST /authorize is the one endpoint that can stop a "
                 "charge: the engine decides before the row is committed, so a "
                 "declined charge is never an approved transaction."),
)
app.include_router(router)
app.include_router(catalog_router)
app.include_router(rules_router)
app.include_router(queue_router)
app.include_router(kpi_router)
app.include_router(explain_router)
app.include_router(cases_router)
app.include_router(simulate_router)
app.include_router(ingest_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _mount_console() -> None:
    """Serve `console/dist` at /console, when it has been built.

    Decision 9 chose a Vite dev proxy over a CORS allowlist on the grounds that
    the bundle is served same-origin in the end — so CORS never becomes a
    production surface. This is that end. In development the console runs on
    :5173 and proxies `/api` here; in a build it is served from this process and
    calls the routes at the root, with no second origin either way.

    Mounted under a PREFIX rather than at `/`. A console at the root would need a
    catch-all to serve an SPA deep link, and a catch-all changes what every
    unmatched path returns — including the ones 443 tests assert 404s on. A
    console must not be able to move an API's behaviour, so it gets its own
    prefix and the rest of the service is untouched.

    Every route here is `include_in_schema=False`: the OpenAPI document is what
    the console's own types are generated from, and static file serving is not
    part of the contract.
    """
    bundle = Path(__file__).resolve().parents[3] / "console" / "dist"
    if not (bundle / "index.html").is_file():
        return

    assets = bundle / "assets"
    if assets.is_dir():
        app.mount("/console/assets", StaticFiles(directory=assets), name="console-assets")

    @app.get("/console", include_in_schema=False)
    @app.get("/console/{path:path}", include_in_schema=False)
    def console(path: str = "") -> FileResponse:
        # Any path under the prefix returns the shell; the router in the browser
        # decides what to render. A deep link to /console/alerts/5 has to work,
        # and the server does not know the client's routes.
        direct = bundle / path
        if path and direct.is_file():
            return FileResponse(direct)
        return FileResponse(bundle / "index.html")


_mount_console()


@app.get("/me")
def me(who: Principal = Depends(principal)) -> dict[str, str]:
    """Who the presented token is, and what it may do.

    The console needs this before it can decide whether to render the admin
    surfaces at all, and a client that has to POST something to find out it is
    not allowed has already asked the user to do work it knew would fail.
    """
    return {"actor": who.actor, "role": who.role}
