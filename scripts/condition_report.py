#!/usr/bin/env python3
"""§10's condition-level report: find the mispriced conditions.

Recommends. Never writes. §10 is explicit that calibration output goes to a human
because silently retuned weights break the audit story — an analyst could not
explain why last week's identical transaction scored differently — and repricing
itself is Week 4.

The report exists because "plot a score histogram" would have missed the actual
false-positive engine in this system, which is a single condition priced at +50
so that one demo case's points would sum to its displayed 31.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from glassbox.db import connect, fetch_all, fetch_one   # noqa: E402

_HEAD = (f"{'rule':<7} {'feature':<30} {'dir':<4} {'priced':>6} {'fired':>7} "
         f"{'fire%':>7} {'degr':>5} {'prec%':>7} {'case%':>7} {'pts/pp':>7}")

# Below this many firings a precision is an anecdote, not a measurement.
MIN_SAMPLE = 20


def _fmt(v, width: int, dp: int = 2) -> str:
    return f"{'—':>{width}}" if v is None else f"{float(v):>{width}.{dp}f}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=None)
    args = ap.parse_args()

    with connect(args.dsn) as conn:
        rows = fetch_all(conn, "SELECT * FROM v_condition_performance")
        base = fetch_one(
            conn,
            """
            SELECT COUNT(*) AS n,
                   COUNT(*) FILTER (WHERE synthetic_label = 'fraud') AS fraud
              FROM transactions
            """)
        decisions = fetch_one(conn, "SELECT COUNT(*) AS n FROM decisions")

    base_rate = 100.0 * base["fraud"] / base["n"] if base["n"] else 0.0
    print(f"condition performance over {decisions['n']} decisions")
    print(f"cohort base rate {base_rate:.2f}%  ({base['fraud']}/{base['n']} "
          f"transactions labelled fraud)")
    print("\nprec% is DIRECTION-AWARE: the fraud rate among firings for an")
    print("aggravator, the legitimate rate for a mitigator — because a mitigator")
    print("firing on legitimate traffic is doing its job, not failing at it.")
    print("Measured against transactions.synthetic_label: exact on this dataset,")
    print("meaningless beyond it. case% is the DISPOSITION of the case a condition")
    print("fired on, which may have been raised by a different rule.\n")
    print(_HEAD)
    print("-" * len(_HEAD))

    for r in rows:
        print(f"{r['rule_id']:<7} {r['feature_key']:<30} "
              f"{r['direction'][:3]:<4} {float(r['priced_points']):>6.0f} "
              f"{r['fired']:>7} {_fmt(r['fire_rate_pct'], 7)} {r['degraded']:>5} "
              f"{_fmt(r['precision_pct'], 7)} "
              f"{_fmt(r['alert_precision_pct'], 7)} "
              f"{_fmt(r['points_per_precision_point'], 7, 1)}")

    # Ranked among AGGRAVATORS. A mispriced aggravator invents risk that was not
    # there, which is what a false positive is; a mispriced mitigator forgives
    # risk, and that shows up as a false negative instead. They are different
    # failures and §10 is asking about the first.
    priced = [r for r in rows if r["points_per_precision_point"] is not None
              and r["direction"] == "aggravating"]
    # The comparator needs a sample. Four of these conditions fire only on the
    # planted fixtures, so "100% precise" over one firing is not a benchmark —
    # quoting a ratio against it would be the same uncalibrated confidence the
    # report exists to find.
    benchmark = [r for r in priced if r["fired"] >= MIN_SAMPLE]
    if len(priced) >= 2 and benchmark:
        worst, best = priced[0], benchmark[-1]
        ratio = (float(worst["points_per_precision_point"])
                 / float(best["points_per_precision_point"]))
        print("\nRECOMMENDATION (not applied — repricing is Week 4, and §10 says "
              "calibration\noutput is a recommendation to a human, never an "
              "automatic write):\n")
        print(f"  {worst['feature_key']} is priced at "
              f"{float(worst['priced_points']):+.0f} and earns "
              f"{float(worst['precision_pct']):.2f}% precision over "
              f"{worst['fired']} firings —")
        print(f"  {float(worst['points_per_precision_point']):.1f} points per "
              f"precision point, against "
              f"{float(best['points_per_precision_point']):.1f} for "
              f"{best['feature_key']} at "
              f"{float(best['priced_points']):+.0f}")
        print(f"  ({float(best['precision_pct']):.2f}% over {best['fired']} "
              f"firings). That is {ratio:.0f}x the cost per unit of measured")
        print(f"  precision of the best-earning aggravator with a sample of at "
              f"least {MIN_SAMPLE}.")
        if worst["fire_rate_pct"] is not None:
            print(f"\n  It fires on {float(worst['fire_rate_pct']):.2f}% of the "
                  f"population. At {float(worst['priced_points']):+.0f} points that "
                  f"is a mid-elevated score")
            print("  for every one of them unless a mitigator happens to fire.")

    never = [r for r in rows if r["fired"] == 0]
    if never:
        print(f"\n  {len(never)} condition(s) never fired over this population: "
              f"{', '.join(r['feature_key'] for r in never)}.")
        print("  A condition that never fires is neither right nor wrong — it is "
              "unevidenced,")
        print("  and pricing it is a guess either way.")

    blind = [r for r in rows if r["fired"] and r["precision_pct"] is None]
    if blind:
        print(f"\n  {len(blind)} condition(s) fired but have no label to measure "
              f"against.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
