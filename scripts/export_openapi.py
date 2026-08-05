"""Write the API's OpenAPI document, which is what the console's types come from.

**This is D6's answer, and D6 is the reason this file exists.**

The nine files under `contract/` are the published, frozen artifacts. They also
contain dangling `$ref`s: `scripts/export_contract_schema.py` asks Pydantic for
`ref_template="#/$defs/{model}"`, which is document-root-absolute, while Pydantic
nests the referenced definitions under each model's OWN `$defs`. So `#/$defs/Signal`
inside `alert.v1.schema.json` resolves to nothing at the document root. True since
Week 2 and true of every contract; harmless until something tries to *resolve* a
`$ref`, which is exactly what a TypeScript generator does.

WEEK5-PLAN names three honest ways out and says to choose in the plan rather than
in a build script. The choice is the third: **generate the client from the
OpenAPI document, and leave the committed schemas as the frozen artifacts they
are.** The reasoning, in order:

  - `alert.v1`'s pinned digest never comes near a build tool. Fixing the exporter
    would move it, and moving it to fix a `$ref` template spends the project's
    single most load-bearing signal on plumbing. Publishing `alert.v2` would spend
    a version number on the same.
  - FastAPI's document is internally consistent because it hoists every model into
    `components.schemas`, so a generator resolves every reference without anything
    being edited.
  - It is derived from the ROUTES, which is what the console actually calls. A
    console typed off the contract files would be typed against models, and the
    two agree only because the routes declare `response_model`.

D6 therefore stays OPEN and recorded rather than being closed quietly, because
nothing here fixes the dangling refs — it routes around them, and the day
somebody wants the published schemas to be resolvable that work is still waiting.

Written offline, from the app object, with no server running and no database
touched: `app.openapi()` reads route signatures and Pydantic models and nothing
else. `test_openapi.py` asserts the committed copy is current, so a route change
that would silently stale the console's types fails the suite instead.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

DEST = ROOT / "console" / "src" / "api" / "openapi.json"


def document() -> dict:
    """The OpenAPI document, exactly as the running service would serve it."""
    from glassbox.api.app import app

    return app.openapi()


def render(doc: dict) -> str:
    # Sorted keys and a trailing newline, for the same reason the contract
    # exporter does it: a byte comparison is only a useful test if the bytes are
    # deterministic.
    return json.dumps(doc, indent=2, sort_keys=True, default=str) + "\n"


def main() -> int:
    text = render(document())
    DEST.parent.mkdir(parents=True, exist_ok=True)
    previous = DEST.read_text(encoding="utf-8") if DEST.exists() else None
    DEST.write_text(text, encoding="utf-8")

    paths = len(json.loads(text).get("paths", {}))
    state = "unchanged" if previous == text else "written"
    print(f"{DEST.relative_to(ROOT)}: {state}, {paths} paths, {len(text)} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
