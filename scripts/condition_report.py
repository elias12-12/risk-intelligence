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

# How far above the catalog's own median cost per precision point a condition has
# to sit before the report calls it a misprice rather than a spread. Stated as a
# constant so the threshold is arguable rather than implied by whatever the
# printout happens to emphasise. country_is_new_for_customer sat at 4.4x its
# median peer before 0026 and sits at ~1.1x after it.
MATERIAL = 3.0


def _fmt(v, width: int, dp: int = 2) -> str:
    return f"{'—':>{width}}" if v is None else f"{float(v):>{width}.{dp}f}"


def cost_anchor(rows: list[dict]) -> tuple[dict | None, list[dict], float | None, float | None]:
    """(worst aggravator, comparably-sampled peers, median cost, ratio).

    One implementation, used by the printout below and by
    test_condition_report.py. Two would be two definitions of "mispriced", and
    the test would stop being able to fail the report.
    """
    priced = [r for r in rows if r["points_per_precision_point"] is not None
              and r["direction"] == "aggravating"]
    benchmark = [r for r in priced if r["fired"] >= MIN_SAMPLE]
    if len(priced) < 2 or len(benchmark) < 2:
        return (priced[0] if priced else None), benchmark, None, None

    costs = sorted(float(r["points_per_precision_point"]) for r in benchmark)
    mid = len(costs) // 2
    anchor = costs[mid] if len(costs) % 2 else (costs[mid - 1] + costs[mid]) / 2
    worst = priced[0]
    ratio = float(worst["points_per_precision_point"]) / anchor if anchor else None
    return worst, benchmark, anchor, ratio


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
    # The comparator needs a sample, and the ANCHOR is the median of the
    # comparably-sampled aggravators rather than the cheapest one. The cheapest
    # is whichever condition happens to fire almost exclusively on the planted
    # fixtures, where precision is a property of the fixture:
    # device_first_seen_min earns 97% over 36 firings and would anchor every
    # comparison at a number no population-scale condition can reach.
    worst, benchmark, anchor, ratio = cost_anchor(rows)
    if worst is not None and anchor:
        print(f"\nWorst-earning aggravator: {worst['feature_key']} at "
              f"{float(worst['priced_points']):+.0f}, "
              f"{float(worst['precision_pct']):.2f}% precision over "
              f"{worst['fired']} firings")
        print(f"  = {float(worst['points_per_precision_point']):.1f} points per "
              f"precision point, against a median of {anchor:.1f} across the "
              f"{len(benchmark)} aggravators")
        print(f"  with a sample of at least {MIN_SAMPLE}. Ratio {ratio:.1f}x "
              f"(material at {MATERIAL:.0f}x).")

        if ratio >= MATERIAL:
            print("\nRECOMMENDATION (not applied — §10 says calibration output is "
                  "a recommendation\nto a human, never an automatic write, because "
                  "silently retuned weights break\nthe audit story):\n")
            print(f"  Reprice {worst['feature_key']} from "
                  f"{float(worst['priced_points']):+.0f} to "
                  f"{float(worst['precision_pct']) * anchor:+.0f}, which is its "
                  f"measured precision at the")
            print("  median cost this catalog charges. Apply it as a new seed file "
                  "with these\n  numbers in the comment: the reason for a price "
                  "belongs next to the price.")
            if worst["fire_rate_pct"] is not None:
                print(f"\n  It fires on {float(worst['fire_rate_pct']):.2f}% of "
                      f"the population, so the current price is paid that often.")
        else:
            print("\n  No aggravator is materially mispriced against its "
                  "comparably-sampled peers.")
            print("  The three population-scale aggravators — "
                  + ", ".join(f"{r['feature_key']} {float(r['priced_points']):+.0f}"
                              for r in benchmark[:3])
                  + " —")
            print("  now cost within a factor of two of each other per unit of "
                  "measured precision.")
            print("  Nothing here is evidence that the prices are RIGHT; it is "
                  "evidence that they are\n  consistent, which is the most a "
                  "cost-per-precision comparison can establish.")

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
