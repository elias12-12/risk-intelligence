#!/usr/bin/env python3
"""One definition of "a database somebody can demo".

`reset_db.py` builds a database: migrated, seeded, fixtures loaded, features
computed. That is not yet demoable — nothing has been *decided*. Until both
lanes run there are no decisions, no alerts and no queue; until the actions are
settled there is no verdict on anything and every precision tile is empty; and
until `card_challenge_fails_30d` is recomputed the loop the system is built to
show — its own actions becoming evidence the next decision reads — has not
closed.

Those five steps lived only in `bootstrap.ps1`, which is PowerShell, needs a
host virtualenv, and is not runnable from a container. The packaged
`docker compose up` needs exactly the same sequence, and a second copy of it in
a compose command would be a second answer to "what does a built GlassBox look
like" — drifting silently, the way the four verdict CTEs did. So the sequence
lives here, and `bootstrap.ps1` calls this rather than repeating it.

    python scripts/bootstrap_demo.py              build everything
    python scripts/bootstrap_demo.py --keep-fixtures   skip the generator

The generator is the slow step and its output is deterministic at a fixed seed,
so it is skipped when `fixtures/synthetic_data.sql` already exists. It is
gitignored, which is why a fresh clone regenerates it and a rebuild does not.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

from glassbox import config                                       # noqa: E402
from glassbox.db import connect                                   # noqa: E402
from glassbox.engine.evaluation import EngineContext, run_lane    # noqa: E402
from glassbox.engine.outcomes import settle                       # noqa: E402
from glassbox.features.runner import IncrementalRunner            # noqa: E402
from glassbox.ingest import watermark                             # noqa: E402

import reset_db                                                   # noqa: E402

# The feature the loop closes on. `resolve_actions` writes challenge_failed
# events, and this is the only feature that reads them — so it is recomputed
# after settling rather than during the main pass, when the events it counts do
# not exist yet.
LOOP_FEATURE = "card_challenge_fails_30d"


def step(text: str) -> None:
    print(f"\n=== {text} ===", flush=True)


def generate_fixtures() -> None:
    """Run the generator unless its output is already on disk.

    A separate process rather than an import: `generate_synthetic.py` is a
    top-level script that ends in `sys.exit`, and importing it would end this
    one too.
    """
    target = config.FIXTURES_DIR / "synthetic_data.sql"
    if target.exists():
        print(f"  {target.name} already present — skipping the generator")
        return
    subprocess.run([sys.executable, str(REPO / "scripts" / "generate_synthetic.py")],
                   check=True, cwd=REPO)


def build(dsn: str | None = None, fixtures: bool = True) -> None:
    started = time.perf_counter()

    if fixtures:
        step("Fixtures")
        generate_fixtures()

    step("Database — migrate, seed, load, compute features")
    reset_db.build(dsn or config.dsn())

    step("Decisioning — both lanes")
    as_of = config.reference_now()
    with connect(dsn) as conn:
        ctx = EngineContext.load(conn)
        for lane in ("inline_sync", "async"):
            totals = run_lane(conn, lane, as_of, run_id="bootstrap", ctx=ctx)
            # Advanced for the same reason `run_cycle.py` advances it: the
            # background cycle should start from a population it has consumed,
            # not re-score everything on its first tick.
            watermark.advance(conn, lane, as_of)
            print(f"  {lane:<12} {totals}")
        conn.commit()

    step("Actions — settle outcomes, disposition cases")
    with connect(dsn) as conn:
        tally = settle(conn)
        conn.commit()
        print(f"  {tally}")

    step(f"Feature loop — {LOOP_FEATURE} reads the actions just settled")
    with connect(dsn) as conn:
        written = sum(r.rows_written for r in
                      IncrementalRunner(conn).run_population(as_of, None, [LOOP_FEATURE]))
        conn.commit()
        print(f"  {written} rows")

    print(f"\nready in {time.perf_counter() - started:.1f}s", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dsn", default=None)
    ap.add_argument("--keep-fixtures", action="store_true",
                    help="never run the generator, even if the file is missing")
    args = ap.parse_args()

    build(args.dsn, fixtures=not args.keep_fixtures)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
