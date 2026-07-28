#!/usr/bin/env python3
"""Build the graph, then run the feature layer up to an as_of."""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from glassbox import config                      # noqa: E402
from glassbox.db import connect                  # noqa: E402
from glassbox.features.runner import IncrementalRunner  # noqa: E402
from glassbox.graph.builder import build         # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", default=None, help="ISO instant; defaults to GLASSBOX_NOW")
    ap.add_argument("--since", default=None, help="watermark; omit for a full pass")
    ap.add_argument("--dsn", default=None)
    ap.add_argument("--feature", action="append", help="restrict to these features")
    ap.add_argument("--no-graph", action="store_true")
    args = ap.parse_args()

    as_of = datetime.fromisoformat(args.as_of) if args.as_of else config.reference_now()
    since = datetime.fromisoformat(args.since) if args.since else None

    with connect(args.dsn) as conn:
        if not args.no_graph:
            for c in build(conn):
                print(f"  cluster {c.cluster_id:<10} {c.natural_key:<28} "
                      f"{c.members} members{'  (new)' if c.created else ''}")
            conn.commit()

        runner = IncrementalRunner(conn)
        total = 0
        print(f"\nfeature runner  as_of={as_of.isoformat()}"
              f"{'  since=' + since.isoformat() if since else ''}")
        for report in runner.run_population(as_of, since, args.feature):
            t0 = time.perf_counter()
            if report.skipped:
                print(f"  skip  {report.feature_key:<32} {report.skipped}")
                continue
            total += report.rows_written
            print(f"  ok    {report.feature_key:<32} {report.rows_written:>6} rows"
                  f"   {time.perf_counter() - t0:5.1f}s")
        conn.commit()
        print(f"\n{total} feature values written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
