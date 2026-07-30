#!/usr/bin/env python3
"""Settle issued actions and disposition the cases they opened (§8).

Every rate is printed WITH ITS DENOMINATOR. On this dataset a full pass issues
four challenges and two holds, so prevention precision is n=4 — a number worth
quoting only next to the 4. §11 already requires each tile to name its window;
this is the same discipline applied at the source.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from glassbox.db import connect               # noqa: E402
from glassbox.engine.outcomes import settle    # noqa: E402


def _rate(label: str, n: int, d: int) -> str:
    pct = f"{100.0 * n / d:5.1f}%" if d else "    — "
    return f"  {label:<28} {pct}   ({n}/{d})"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=None)
    args = ap.parse_args()

    with connect(args.dsn) as conn:
        tally = settle(conn)
        conn.commit()

    ch = tally["challenges"]
    print("action outcomes  (SYNTHETIC — settled against transactions.synthetic_label)")
    print(_rate("challenge pass rate", tally["passed"], ch))
    print(_rate("challenge fail rate", tally["failed"], ch))
    print(_rate("challenge abandon rate", tally["abandoned"], ch))
    print(f"  {'notifications delivered':<28} {tally['notifies']:>6}")
    print(f"  {'analyst-settled holds/blocks':<28} {tally['analyst']:>6}")
    print(f"  {'cases dispositioned':<28} {tally['dispositions']:>6}")
    if 0 < ch < 30:
        print(f"\n  n={ch}. Every rate above is a small-sample rate and must be "
              f"quoted with its denominator.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
