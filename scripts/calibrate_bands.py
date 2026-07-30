#!/usr/bin/env python3
"""§10's other half: where the band cutoffs should sit, per subject type.

Recommends. Never writes — the same rule condition_report.py follows, for the
same reason: a cutoff that retunes itself means an analyst cannot explain why
last week's identical subject landed in a different band. A human reads this and
hand-writes a reviewed seed file. (`test_calibration.py` greps this source for
SQL write keywords, so the prose above avoids spelling them.)

`0018` seeded 70/45/0 across all seven subject types and admitted it in its own
`basis` column: "week-1 global cutoff, uncalibrated; per-subject-type calibration
is Week 4". After consolidation those are different populations with different
score ranges, so one global line cannot be right for all of them.

WHY MAXIMUM-GAP AND NOT A PERCENTILE
------------------------------------
The obvious method — high at p99, elevated at p95 — is wrong on this data, and
wrong in a way that would have looked fine in a summary table. The transaction
distribution is bimodal with a 56-point empty region in the middle: scores land
at 2, 3, 8 and 12, then nothing at all until 68, 81 and 87. p95 of the scoring
subjects is 12, so a percentile rule would put every transaction where one
condition fired into `elevated`, and p99 is 68, which would promote the veto
fixture out of the band it was signed off in.

A cutoff's job is to separate populations. When the populations are already
separated by an empty region, the cutoff belongs inside that region, and every
value inside it produces the identical partition — which is exactly what makes
the choice robust rather than tuned. So: find the widest gaps between adjacent
observed scores, put the cutoff at the midpoint, round to the nearest 5.

What this method can support: "no observed subject sits near this line."
What it cannot: any claim that the resulting bands carry a calibrated risk
appetite. That needs dispositions at volume, and §8's denominators here are
single digits. The `basis` column should say so, and the seed this recommends
should quote this paragraph rather than imply more.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from glassbox.db import connect, fetch_all   # noqa: E402

# Below this many SCORING subjects there is no distribution, only anecdotes.
# `account` has one and `network` has one; proposing a cutoff from either would
# be dressing n=1 up as calibration.
MIN_SUBJECTS = 30

BANDS = ("high", "elevated")


def _round5(x: float) -> int:
    return int(round(x / 5.0) * 5)


def gaps(scores: list[int]) -> list[tuple[int, int, int]]:
    """(width, lower, upper) for each adjacent pair, widest first."""
    ladder = sorted(set(scores))
    out = [(hi - lo, lo, hi) for lo, hi in zip(ladder, ladder[1:])]
    return sorted(out, key=lambda g: (-g[0], g[1]))


def propose(scores: list[int]) -> dict[str, int] | None:
    """high and elevated at the midpoints of the two widest gaps."""
    found = gaps(scores)
    if len(found) < 2:
        return None
    cuts = sorted(_round5((lo + hi) / 2.0) for _, lo, hi in found[:2])
    if cuts[0] == cuts[1]:
        return None
    return {"elevated": cuts[0], "high": cuts[1]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=None)
    args = ap.parse_args()

    with connect(args.dsn) as conn:
        current = {(r["subject_type"], r["band"]): r for r in fetch_all(
            conn, "SELECT subject_type, band, min_score, basis FROM score_bands")}
        rows = fetch_all(
            conn,
            """
            SELECT subject_type, score::int AS score, count(*) AS n
              FROM decisions
             GROUP BY 1, 2
             ORDER BY 1, 2
            """)
        pinned = fetch_all(
            conn,
            """
            SELECT subject_type, subject_id, score::int AS score, band
              FROM decisions
             WHERE alert_id IS NOT NULL
             ORDER BY score DESC
            """)

    by_type: dict[str, list[tuple[int, int]]] = {}
    for r in rows:
        by_type.setdefault(r["subject_type"], []).append((r["score"], r["n"]))

    print("band calibration — a recommendation, never a write (§10)\n")

    for subject_type in sorted(by_type):
        ladder = by_type[subject_type]
        total = sum(n for _, n in ladder)
        scoring = [(s, n) for s, n in ladder if s > 0]
        n_scoring = sum(n for _, n in scoring)
        now = {b: current.get((subject_type, b), {}).get("min_score")
               for b in BANDS}

        print(f"{subject_type}")
        print(f"  {total} decisions, {n_scoring} scoring above zero "
              f"({100.0 * n_scoring / total:.1f}%)")
        print("  observed  " + "  ".join(f"{s}x{n}" for s, n in scoring) or "  observed  —")
        print(f"  current   high >= {now['high']}, elevated >= {now['elevated']}")

        if n_scoring < MIN_SUBJECTS:
            print(f"  PROPOSE   nothing. {n_scoring} scoring subject(s) is not a "
                  f"distribution;\n            a cutoff derived from it would be "
                  f"n={n_scoring} dressed as calibration.")
            print("            Keep the inherited cutoff and say so in `basis`.\n")
            continue

        expanded = [s for s, n in scoring for _ in range(min(n, 1))]
        found = gaps(expanded)
        print("  gaps      " + ", ".join(f"{lo}->{hi} ({w})" for w, lo, hi in found[:3]))

        proposal = propose(expanded)
        if proposal is None:
            print("  PROPOSE   nothing — fewer than two gaps to place cutoffs in.\n")
            continue

        print(f"  PROPOSE   high >= {proposal['high']}, "
              f"elevated >= {proposal['elevated']}")
        for band, cut in (("high", proposal["high"]),
                          ("elevated", proposal["elevated"])):
            nearest = min((abs(s - cut), s) for s, _ in scoring)[1]
            print(f"            {band:<8} at {cut}: nearest observed score is "
                  f"{nearest}, {abs(nearest - cut)} away")

        counts = {"high": 0, "elevated": 0, "low": 0}
        for s, n in ladder:
            key = ("high" if s >= proposal["high"]
                   else "elevated" if s >= proposal["elevated"] else "low")
            counts[key] += n
        print(f"            would band: high {counts['high']}, "
              f"elevated {counts['elevated']}, low {counts['low']}")

        moved = [p for p in pinned if p["subject_type"] == subject_type
                 and p["band"] != ("high" if p["score"] >= proposal["high"]
                                   else "elevated" if p["score"] >= proposal["elevated"]
                                   else "low")]
        if moved:
            print("            WARNING — this moves a subject that already "
                  "alerted:")
            for p in moved:
                print(f"              {p['subject_id']} scores {p['score']}, "
                      f"banded {p['band']} today")
            print("            A cutoff that reclassifies a signed-off case is a "
                  "finding to\n            surface, not something to absorb "
                  "silently.")
        else:
            print("            every subject that already alerted keeps its band")
        print()

    print("Apply by hand as a seed file, with the numbers above in the comment.")
    print("What a maximum-gap cutoff supports: no observed subject sits near the")
    print("line. What it does NOT support: a calibrated risk appetite — that needs")
    print("dispositions at volume, and §8's denominators here are single digits.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
