#!/usr/bin/env python3
"""Run one decisioning cycle over a lane."""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from glassbox import config                       # noqa: E402
from glassbox.db import connect                   # noqa: E402
from glassbox.engine.evaluation import EngineContext, run_lane  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lane", required=True, choices=["inline_sync", "async"])
    ap.add_argument("--as-of", default=None)
    ap.add_argument("--dsn", default=None)
    ap.add_argument("--subject", action="append", help="restrict to these subject ids")
    ap.add_argument("--run-id", default=None)
    args = ap.parse_args()

    as_of = datetime.fromisoformat(args.as_of) if args.as_of else config.reference_now()
    t0 = time.perf_counter()
    with connect(args.dsn) as conn:
        ctx = EngineContext.load(conn)
        totals = run_lane(conn, args.lane, as_of, run_id=args.run_id,
                          subject_ids=args.subject, ctx=ctx)
        conn.commit()
    print(f"lane={args.lane}  as_of={as_of.isoformat()}  {time.perf_counter() - t0:.1f}s")
    # Printed in pipeline order, and every key run_lane reports is printed —
    # a new counter should show up here without an edit.
    order = ("evaluations", "decisions", "conditions", "alerts", "folded",
             "restated", "suppressed", "signals", "executions")
    for k in list(order) + [k for k in totals if k not in order]:
        if k in totals:
            print(f"  {k:<12} {totals[k]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
