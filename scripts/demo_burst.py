#!/usr/bin/env python3
"""The live demo: five charges arriving one at a time, and the fifth is stopped.

Everything else in this repository detects fraud that was already in the file it
was handed. This sends charges the system has never seen, one authorization at a
time, and watches the engine decide each one before the row is committed.

    python scripts/demo_burst.py            in-process, against GLASSBOX_DSN
    python scripts/demo_burst.py --http     through a running `glassbox serve`
    python scripts/demo_burst.py --clean    remove what a previous run wrote

Why the numbers land where they do, so nothing here looks staged:

  * The charges are twenty seconds apart, so all five sit inside
    `card_cnp_count`'s ninety-second window. The fifth is the fifth.
  * The device is one nobody has seen. A device is OBSERVED rather than opened,
    so the first charge registers it — and `device_first_seen_min` is therefore
    measured from that instant, which is what makes it fire on the fifth.
  * They are dated at the fixtures' reference instant rather than at wall clock.
    Every window feature (90s, 24h, 30d) is measured against history pinned to
    2026-01-15, and a charge dated today would see a card with no past at all.
  * `mcc_is_new_for_customer` carries a one-day `baseline_lag` (seed 0019), so
    the burst cannot establish its own novelty. It reads new because gift cards
    genuinely are new for this customer as of yesterday.

The first four charges are approved and score zero. The fifth scores 87 —
34 + 21 + 18 + 14, every point on screen — crosses R-114's prevent threshold of
85, and is DECLINED. That last word is the entire point: `auth_result` on the
committed row says `declined`, so the charge did not go through.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from glassbox import config                                   # noqa: E402
from glassbox.db import connect, fetch_all, fetch_value       # noqa: E402
from glassbox.engine.evaluation import EngineContext          # noqa: E402
from glassbox.ingest.authorize import authorize               # noqa: E402

PREFIX = "DEMO"
DEVICE = "DEV-DEMO-BURST"
CHARGES = 5
GAP_SECONDS = 20

# ~1,412 km from CUST-OKAFOR's home in New York, which is what clears R-114's
# 1,400 km line on session_geo_jump_km.
AWAY_LAT, AWAY_LON = Decimal("40.71"), Decimal("-90.78")
GIFT_CARDS = "5815"

GREEN, RED, DIM, BOLD, OFF = "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"


def charge(n: int, start: datetime) -> dict:
    return {
        "txn_id": f"{PREFIX}-{n + 1}",
        "occurred_at": (start + timedelta(seconds=GAP_SECONDS * n)).isoformat(),
        "amount": "312.00",
        "card_id": "CARD-4417", "account_id": "ACC-4417",
        "customer_id": "CUST-OKAFOR", "merchant_id": "MER-GIFT",
        "mcc": GIFT_CARDS, "channel": "cnp", "entry_mode": "ecom",
        "txn_country": "US", "txn_lat": str(AWAY_LAT), "txn_lon": str(AWAY_LON),
        "ip_address": "45.83.12.9", "device_id": DEVICE, "billing_country": "US",
    }


def header() -> None:
    print(f"\n{BOLD}Five card-not-present charges on CARD-4417, twenty seconds "
          f"apart, from a device nobody has seen.{OFF}")
    print(f"{DIM}Each one is authorized on its own. Nothing below was in the "
          f"fixtures.{OFF}\n")
    print(f"  {'charge':<8} {'authorization':<14} {'reason':<20} {'score':>5}  "
          f"{'action':<10} {'case':<9} {'issued':<26} {'ms':>6}")
    print(f"  {'-' * 8} {'-' * 14} {'-' * 20} {'-' * 5}  {'-' * 10} {'-' * 9} "
          f"{'-' * 26} {'-' * 6}")


def line(txn_id: str, authorization: str, reason: str, score, action: str,
         routing: str, issued: str, ms) -> None:
    colour = GREEN if authorization == "approved" else RED
    print(f"  {txn_id:<8} {colour}{authorization:<14}{OFF} {reason or '-':<20} "
          f"{str(score):>5}  {action:<10} {routing or '-':<9} {issued:<26} "
          f"{float(ms or 0):>6.0f}")


def explain(signals) -> None:
    if not signals:
        return
    print(f"\n{BOLD}Why — every point accounted for:{OFF}")
    total = 0
    for s in signals:
        contribution = int(s["contribution"])
        total += contribution
        value = s.get("feature_value")
        shown = f"{value:.2f}" if isinstance(value, float) else str(value)
        print(f"   {contribution:>+4}  {s['feature_key']:<24} {shown:>8}  "
              f"{DIM}{s['human_text'][:58]}{OFF}")
    print(f"   {'=' * 4}")
    print(f"   {total:>4}  {BOLD}the score{OFF}")


# ------------------------------------------------------------------ in-process
def run_local(start: datetime) -> int:
    header()
    stopped = None
    with connect() as conn:
        ctx = EngineContext.load(conn)
        for n in range(CHARGES):
            decided = authorize(conn, _typed(charge(n, start)), ctx=ctx)
            conn.commit()
            result = decided.result
            issued = ", ".join(
                f"{r['action']}/{r['channel']}" for r in fetch_all(
                    conn,
                    "SELECT action, channel FROM action_executions "
                    " WHERE decision_id = %s ORDER BY execution_id",
                    (result.decision_id,))) or "-"
            routing = fetch_value(
                conn, "SELECT alert_routing AS r FROM decisions WHERE decision_id = %s",
                (result.decision_id,))
            line(decided.txn_id, decided.authorization, decided.decline_reason,
                 result.pool.subject_score, result.outcome.action, routing,
                 issued, decided.latency_ms)
            if not decided.approved:
                stopped = decided

        if stopped is not None:
            explain(fetch_all(
                conn,
                """
                SELECT s.feature_key, s.contribution, s.feature_value, s.human_text
                  FROM alert_signals s JOIN alerts a ON a.alert_id = s.alert_id
                 WHERE a.subject_id = %s ORDER BY s.rank
                """,
                (stopped.txn_id,)))
            _closing(conn, stopped.txn_id)
    return 0


def _typed(row: dict) -> dict:
    """The HTTP body, back into Python types for the in-process path."""
    typed = dict(row)
    typed["occurred_at"] = datetime.fromisoformat(row["occurred_at"])
    for key in ("amount", "txn_lat", "txn_lon"):
        typed[key] = Decimal(row[key])
    return typed


def _closing(conn, txn_id: str) -> None:
    stored = fetch_all(
        conn,
        "SELECT txn_id, auth_result, decline_reason, source FROM transactions "
        " WHERE txn_id LIKE %s ORDER BY txn_id", (f"{PREFIX}-%",))
    print(f"\n{BOLD}What is actually stored:{OFF}")
    for row in stored:
        mark = GREEN if row["auth_result"] == "approved" else RED
        print(f"   {row['txn_id']:<8} {mark}{row['auth_result']:<9}{OFF} "
              f"{row['decline_reason'] or '':<20} {DIM}source={row['source']}{OFF}")
    print(f"\n{DIM}The declined row was never committed as approved: the engine "
          f"decided inside\nthe same transaction that inserted it, and raw "
          f"capture is append-only.{OFF}")
    print(f"{DIM}Re-run with --clean to remove all of it.{OFF}")


# ------------------------------------------------------------------- over http
def run_http(start: datetime, base: str, token: str) -> int:
    import urllib.error
    import urllib.request
    import json

    header()
    stopped = None
    for n in range(CHARGES):
        body = json.dumps(charge(n, start)).encode()
        request = urllib.request.Request(
            f"{base}/authorize", data=body, method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(request) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as exc:
            print(f"\n{RED}HTTP {exc.code}{OFF}: {exc.read().decode()[:400]}")
            return 1

        issued = ", ".join(f"{e['action']}/{e['channel']}"
                           for e in payload["executions"]) or "-"
        line(payload["txn_id"], payload["authorization"],
             payload.get("decline_reason"), payload["score"],
             payload["action"]["taken"], payload.get("alert_routing"),
             issued, payload.get("latency_ms"))
        if payload["authorization"] != "approved":
            stopped = payload

    if stopped is not None:
        explain(stopped["signals"])
        print(f"\n{DIM}Every number above came off the response body. "
              f"persisted={stopped['persisted']}.{OFF}")
    return 0


# ---------------------------------------------------------------------- clean
def clean() -> int:
    """Take the demo back out, so the script can be run again.

    Deliberately narrow: it removes rows this script created, identified by
    their `source` and their id prefix, and nothing else. A `DELETE FROM
    transactions` in a demo script is how somebody loses a dataset.
    """
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM action_executions WHERE decision_id IN
                    (SELECT decision_id FROM decisions WHERE subject_id LIKE %s)
                """, (f"{PREFIX}-%",))
            cur.execute(
                "DELETE FROM alerts WHERE subject_id LIKE %s", (f"{PREFIX}-%",))
            cur.execute(
                "DELETE FROM decisions WHERE subject_id LIKE %s", (f"{PREFIX}-%",))
            cur.execute(
                "DELETE FROM feature_values WHERE entity_id LIKE %s", (f"{PREFIX}-%",))
            cur.execute(
                "DELETE FROM transactions WHERE txn_id LIKE %s", (f"{PREFIX}-%",))
            cur.execute("DELETE FROM devices WHERE device_id = %s", (DEVICE,))
        conn.commit()
    print(f"removed the {PREFIX}-* charges and {DEVICE}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--http", action="store_true",
                    help="go through a running `glassbox serve` instead of the DSN")
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--token", default="admin-token")
    ap.add_argument("--at", default=None,
                    help="ISO instant for the first charge; defaults to GLASSBOX_NOW")
    ap.add_argument("--clean", action="store_true",
                    help="remove what a previous run wrote, and exit")
    args = ap.parse_args()

    if args.clean:
        return clean()
    start = datetime.fromisoformat(args.at) if args.at else config.reference_now()
    if args.http:
        return run_http(start, args.base.rstrip("/"), args.token)
    return run_local(start)


if __name__ == "__main__":
    raise SystemExit(main())
