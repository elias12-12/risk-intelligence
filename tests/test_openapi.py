"""The console's types are generated from a committed document. Keep it current.

D6, answered in session 5: the TypeScript client is generated from the OpenAPI
document rather than from the nine published contract files, which contain
dangling `$ref`s and are frozen artifacts that a build tool has no business
touching (`scripts/export_openapi.py` carries the full reasoning).

That choice moves the risk somewhere new and this module is where it is caught.
A generated client is only as true as the document it came from, and a document
committed to the repository goes stale the moment a route's `response_model`
changes — silently, because nothing re-runs the exporter. The console would then
typecheck green against a shape the service no longer serves.

So: the committed document must equal the document the app produces right now.
Same argument as `test_contract.py`'s byte-equality check, with the opposite
conclusion about what to do when it fails. `alert.v1` failing means *stop, you
are unfreezing a contract*. This failing means *run the exporter*, because this
file is derived and is not a promise to anybody.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import export_openapi  # noqa: E402


def test_the_committed_openapi_document_is_current():
    """If this fails, run `python scripts/export_openapi.py`."""
    expected = export_openapi.render(export_openapi.document())
    actual = export_openapi.DEST.read_text(encoding="utf-8")
    assert actual == expected, (
        "console/src/api/openapi.json is stale — the routes have moved since it "
        "was written. Run `python scripts/export_openapi.py`, then "
        "`npm run types` in console/, and commit both."
    )


def test_every_published_contract_model_appears_in_the_document():
    """The console binds to the contracts THROUGH this document, so a model that
    never reaches it is a model no console screen can be typed against."""
    doc = json.loads(export_openapi.DEST.read_text(encoding="utf-8"))
    schemas = doc["components"]["schemas"]

    # One representative model per published contract. If a whole contract stops
    # being reachable from a route, this is what notices.
    for model in ("AlertDetail",        # alert.v1
                  "QueueEntry",         # queue.v1
                  "ExecutionRecord",    # executions.v1
                  "KpiSet",             # kpis.v1
                  "CopilotResponse",    # explanation.v1
                  "CaseVerdict",        # dispositions.v1
                  "SimulatedDecision",  # simulation.v1
                  "RuleDetail",         # catalog.v1
                  "AuthorizationOutcome"):  # ingest.v1
        assert model in schemas, f"{model} is not reachable from any route"


def test_the_document_has_no_dangling_refs():
    """The property the published contract files do NOT have, and the reason
    this document is what the client is generated from.

    `scripts/export_contract_schema.py` asks Pydantic for
    `ref_template="#/$defs/{model}"`, which is document-root-absolute, while
    Pydantic nests the referenced definitions under each model's own `$defs`. So
    `#/$defs/Signal` in `alert.v1.schema.json` resolves to nothing. FastAPI
    hoists every model into `components.schemas` instead, and this asserts it
    rather than assuming it — if it ever stopped being true, generating a client
    from here would silently produce the same broken output D6 describes.
    """
    doc = json.loads(export_openapi.DEST.read_text(encoding="utf-8"))
    schemas = doc["components"]["schemas"]

    refs: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str):
                refs.append(ref)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(doc)
    assert refs, "a document with no $refs would make this test vacuous"

    dangling = sorted({
        ref for ref in refs
        if not (ref.startswith("#/components/schemas/")
                and ref.removeprefix("#/components/schemas/") in schemas)
    })
    assert not dangling, f"unresolvable $refs in the OpenAPI document: {dangling}"


@pytest.mark.parametrize("path,method", [
    ("/alerts/{alert_id}/outcome", "post"),   # the analyst's one write
    ("/rules", "post"),                       # the control plane
    ("/rules/{rule_id}/promote", "post"),
    ("/simulate/rule", "post"),
    ("/simulate/transaction", "post"),
    ("/authorize", "post"),                   # the one that can stop a charge
    ("/cycle", "get"),                        # the only source of liveness
])
def test_the_surfaces_the_console_binds_to_are_published(path, method):
    """Named individually rather than counted, so that a route being REMOVED is
    a failure with the route's name in it."""
    doc = json.loads(export_openapi.DEST.read_text(encoding="utf-8"))
    assert path in doc["paths"], f"{path} is not in the document"
    assert method in doc["paths"][path], f"{path} has no {method.upper()}"


def test_the_exporter_runs_without_a_database():
    """`app.openapi()` reads route signatures and Pydantic models and nothing
    else, so type generation never depends on a running Postgres. Asserted by
    running it in a subprocess with the DSN pointed at nothing."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "export_openapi.py")],
        capture_output=True, text=True, cwd=ROOT,
        env={"PATH": "", "SYSTEMROOT": "C:\\Windows",
             "GLASSBOX_DSN": "postgresql://nobody@127.0.0.1:1/nothing"},
    )
    assert result.returncode == 0, result.stderr
    assert "paths" in result.stdout
