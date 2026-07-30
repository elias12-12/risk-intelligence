"""`python -m glassbox <command>` — a thin front door onto scripts/."""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
COMMANDS = {
    "migrate": "migrate.py",
    "reset": "reset_db.py",
    "generate": "generate_synthetic.py",
    "features": "run_features.py",
    "cycle": "run_cycle.py",
    "resolve": "resolve_actions.py",
    "report": "condition_report.py",
    "contract": "export_contract_schema.py",
}


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print("usage: python -m glassbox <command> [args]\n\ncommands:")
        for name in COMMANDS:
            print(f"  {name}")
        print("  serve            start the read API on :8000")
        return 0

    command, rest = sys.argv[1], sys.argv[2:]
    if command == "serve":
        import uvicorn
        uvicorn.run("glassbox.api.app:app", host="127.0.0.1", port=8000)
        return 0
    if command not in COMMANDS:
        print(f"unknown command {command!r}", file=sys.stderr)
        return 2

    sys.argv = [str(SCRIPTS / COMMANDS[command]), *rest]
    runpy.run_path(str(SCRIPTS / COMMANDS[command]), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
