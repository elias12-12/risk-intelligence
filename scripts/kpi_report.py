#!/usr/bin/env python3
"""§11's nine tiles, in a terminal.

Every number here is computed from stored rows. There is no illustrative value
anywhere in this output, which is the whole point of §11 being in scope: "the
console shows real analytics, not illustration."

Three things this printout does that a dashboard usually does not, each because
§11 asks for it in as many words:

  * it prints the DENOMINATOR beside every rate. §8's denominators are single
    digits here, and "0% prevention false positives" reads as a result when it
    is a sample size;
  * it prints a delta only when a real preceding window exists, and says why
    when one does not;
  * it prints the caveat attached to a tile rather than a footnote nobody
    reads. A synthetic pass rate that does not announce itself is the console
    copy §11 objects to.
"""
from __future__ import annotations

import argparse
import sys
import textwrap
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from glassbox.contract.kpis import read_kpis   # noqa: E402
from glassbox.db import connect                # noqa: E402

WIDTH = 78


def _num(value, unit: str) -> str:
    if value is None:
        return "—"
    if unit == "percent":
        return f"{float(value):.2f}%"
    if unit == "seconds":
        return f"{float(value) / 3600.0:.1f}h"
    return f"{float(value):,.0f}"


def _delta(value) -> str:
    if value is None:
        return ""
    return f"  ({float(value):+.1f}% vs prior window)"


def _wrap(text: str, indent: str = "      ") -> str:
    return textwrap.fill(text, width=WIDTH, initial_indent=indent,
                         subsequent_indent=indent)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=None)
    ap.add_argument("--window-days", type=int, default=7)
    ap.add_argument("--as-of", default=None)
    ap.add_argument("--verbose", action="store_true",
                    help="also print each tile's basis and prerequisite")
    args = ap.parse_args()

    as_of = datetime.fromisoformat(args.as_of) if args.as_of else None
    with connect(args.dsn) as conn:
        kpis = read_kpis(conn, as_of=as_of, window=timedelta(days=args.window_days))

    print(f"GlassBox KPIs — kpis.v1")
    print(f"window   {kpis.window_start.isoformat()}  ->  {kpis.window_end.isoformat()}")
    if kpis.baseline_available:
        print(f"baseline {kpis.baseline_start.isoformat()}  ->  "
              f"{kpis.baseline_end.isoformat()}")
    else:
        print("baseline unavailable")
        print(_wrap(kpis.baseline_absent_reason, indent="         "))
    print()

    for tile in kpis.tiles:
        flag = "  [SYNTHETIC]" if tile.synthetic else ""
        head = f"{tile.label}{flag}"
        print(f"{head}")
        den = f"   ({tile.numerator}/{tile.denominator})" if tile.denominator else ""
        print(f"   {_num(tile.value, tile.unit)}{den}{_delta(tile.delta_pct)}")

        for part in tile.parts:
            pden = f" ({part.numerator}/{part.denominator})" if part.denominator else ""
            print(f"      {part.label:<46} {_num(part.value, part.unit):>10}{pden}"
                  f"{_delta(part.delta_pct)}")

        if args.verbose:
            print(_wrap(f"basis: {tile.basis}"))
            print(_wrap(f"needs: {tile.requires}"))
        if tile.caveat:
            print(_wrap(f"CAVEAT: {tile.caveat}"))
        print()

    print("Every rate above carries its denominator, and every tile its window.")
    print("Run with --verbose for each tile's derivation and the item it depended on.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
