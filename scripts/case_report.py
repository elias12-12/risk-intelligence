#!/usr/bin/env python3
"""§13's explanation surfaces, in a terminal.

    python scripts/case_report.py --alert 5              # the filing draft
    python scripts/case_report.py --alert 5 --copilot    # the three chips
    python scripts/case_report.py --alert 5 --citations  # every quoted number

Deterministic templating over stored rows. No model is involved, which is §13's
own recommendation and settles §18's open decision 7: the explanation surface of
a glass-box system should not itself be a black box.

`--citations` is the interesting flag. It prints, for every number in the
document, the table and primary key it was quoted from — or, for a derived
number, the formula. That is what "arithmetic is computed outside the model and
injected" looks like when it is checkable rather than promised.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from glassbox.db import connect, fetch_all      # noqa: E402
from glassbox.explain import answer_chips, build_report, load  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=None)
    ap.add_argument("--alert", type=int, default=None,
                    help="alert id; omit to list the alerts available")
    ap.add_argument("--copilot", action="store_true", help="the three chips")
    ap.add_argument("--citations", action="store_true",
                    help="every quoted number, with its source row")
    args = ap.parse_args()

    with connect(args.dsn) as conn:
        if args.alert is None:
            rows = fetch_all(
                conn, "SELECT alert_id, subject_id, score, band, title "
                      "FROM alerts ORDER BY score DESC, alert_id")
            print("alert  subject        score  band      title")
            for r in rows:
                print(f"{r['alert_id']:>5}  {r['subject_id']:<14} "
                      f"{float(r['score']):>5.0f}  {r['band']:<9} {r['title']}")
            print("\nRun again with --alert <id>.")
            return 0

        evidence = load(conn, args.alert)
        if evidence is None:
            print(f"no alert {args.alert}", file=sys.stderr)
            return 1

        if args.copilot:
            response = answer_chips(evidence)
            print(f"Copilot — alert {response.alert_id} "
                  f"({response.subject_type} {response.subject_id})")
            print(f"method: {response.method}")
            print(f"reads:  {', '.join(response.reads)}\n")
            for answer in response.answers:
                print(f"### {answer.question}")
                for line in answer.lines:
                    print(f"  {line}")
                print()
            citations = [c for a in response.answers for c in a.citations]
        else:
            report = build_report(evidence)
            print(report.markdown)
            citations = report.citations
            if report.unresolvable_versions:
                print(f"\n[versions recorded but unresolvable: "
                      f"{', '.join(report.unresolvable_versions)}]")

        if args.citations:
            print(f"\n--- citations ({len(citations)}) ---")
            for c in citations:
                print(f"  {c.value:>26}   {c.label:<34} {c.source}: {c.key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
