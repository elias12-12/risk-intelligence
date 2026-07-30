"""How much money is at stake behind an alert (§9 prioritization).

The queue is score-ordered today, which is the wrong order the moment volume is
real: §9's own example is a 72 with $40,000 at risk outranking an 88 with $30.
Ordering needs a second number, and that number is money.

Two properties this module exists to guarantee.

**It is bounded at the decision's point-in-time bound, never at now().** An
unbounded exposure changes every time anything is recomputed, so the queue order
would silently drift and §9's "N runs produce the same result" invariant would
depend on the wall clock. Bounding it makes exposure a stored fact about an
instant, the same way every feature value is (§4).

**Every number says how it was derived.** There is no accounts.available_balance
in this schema (0015 says so in as many words), so account and network exposure
are DERIVED from movement, and `exposure_basis` names the derivation on the row.
A money number that reorders an analyst's queue has to explain itself; that is
the same argument §1 makes about scores, applied to the thing that decides which
score gets looked at first.

Shape deliberately mirrors evaluation._SUBJECT_SQL: one statement per subject
type, per batch, with the per-subject bound carried in a VALUES list. Adding a
subject type is a code change here exactly as it is there, and the README says
so rather than implying otherwise.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Sequence

import psycopg

from ..db import fetch_all

# The window each derivation looks back over, and the name it publishes.
TRIGGER_TXN = "trigger_txn_amount_base"


def _dimension_exposure(relation_key: str, window: str, basis: str) -> tuple[str, str]:
    """A dimension's exposure is the volume that moved through it in `window`.

    Not a balance — this schema has no balances. It is "how much has been moving
    here lately", which is the honest available proxy for what is at risk, and
    the basis string says that out loud.
    """
    return (f"""
        SELECT v.subject_id,
               COALESCE(SUM(COALESCE(t.amount_base, t.amount)), 0) AS exposure
          FROM (VALUES {{values}}) AS v(subject_id, bound)
          LEFT JOIN transactions t
                 ON t."{relation_key}" = v.subject_id
                AND t.occurred_at <= v.bound
                AND t.occurred_at >  v.bound - INTERVAL '{window}'
         GROUP BY v.subject_id
    """, basis)


_EXPOSURE_SQL: dict[str, tuple[str, str]] = {
    # A transaction's exposure is simply the amount being authorised. This is the
    # only subject type where the number is exact rather than derived.
    "transaction": ("""
        SELECT v.subject_id, COALESCE(t.amount_base, t.amount) AS exposure
          FROM (VALUES {values}) AS v(subject_id, bound)
          JOIN transactions t ON t.txn_id = v.subject_id
    """, TRIGGER_TXN),

    # An account under takeover: what could still leave is best estimated by
    # what has been leaving. Outbound only — inbound is not the account's risk.
    "account": ("""
        SELECT v.subject_id,
               COALESCE(SUM(COALESCE(t.amount_base, t.amount)), 0) AS exposure
          FROM (VALUES {values}) AS v(subject_id, bound)
          LEFT JOIN transactions t
                 ON t.account_id = v.subject_id
                AND t.direction  = 'outbound'
                AND t.occurred_at <= v.bound
                AND t.occurred_at >  v.bound - INTERVAL '90 days'
         GROUP BY v.subject_id
    """, "account_net_flow_90d"),

    # A ring's exposure is what has flowed INTO it and can still be forwarded
    # out. Summed over cluster_members, so it grows and shrinks with the cluster
    # the graph builder actually found — never a literal member list (§3.3).
    "network": ("""
        SELECT v.subject_id,
               COALESCE(SUM(COALESCE(tx.amount_base, tx.amount)), 0) AS exposure
          FROM (VALUES {values}) AS v(subject_id, bound)
          LEFT JOIN cluster_members cm
                 ON cm.cluster_id = v.subject_id AND cm.subject_type = 'account'
          LEFT JOIN transactions tx
                 ON tx.account_id = cm.subject_id
                AND tx.direction  = 'inbound'
                AND tx.occurred_at <= v.bound
                AND tx.occurred_at >  v.bound - INTERVAL '7 days'
         GROUP BY v.subject_id
    """, "cluster_inbound_7d"),

    "card":     _dimension_exposure("card_id", "30 days", "card_volume_30d"),
    "customer": _dimension_exposure("customer_id", "30 days", "customer_volume_30d"),
    "device":   _dimension_exposure("device_id", "30 days", "device_volume_30d"),
    "merchant": _dimension_exposure("merchant_id", "30 days", "merchant_volume_30d"),
}


def exposure_for(conn: psycopg.Connection,
                 results: Sequence[Any]) -> dict[tuple[str, str], tuple[Decimal, str]]:
    """Exposure and its basis, keyed on (subject_type, subject_id).

    A subject type with no derivation returns nothing rather than zero: an
    unpriced alert is honest, a confidently-wrong $0 is not. contract/queue.py
    damps a missing exposure instead of zeroing the priority, so an unpriced
    alert stays reachable in the queue.
    """
    by_type: dict[str, list[Any]] = {}
    for r in results:
        by_type.setdefault(r.request.subject.type, []).append(r)

    out: dict[tuple[str, str], tuple[Decimal, str]] = {}
    for subject_type, group in by_type.items():
        entry = _EXPOSURE_SQL.get(subject_type)
        if entry is None:
            continue
        template, basis = entry
        # One row per subject, carrying ITS OWN bound — the whole point of this
        # module. Deduped so a repeated subject cannot fan the aggregate out.
        pairs: dict[str, Any] = {r.request.subject.id: r.pit_bound_at for r in group}
        values = ", ".join(["(%s::text, %s::timestamptz)"] * len(pairs))
        params = [v for item in pairs.items() for v in item]
        for row in fetch_all(conn, template.format(values=values), params):
            out[(subject_type, row["subject_id"])] = (
                Decimal(row["exposure"] or 0), basis)
    return out
