"""Rows that already happened, arriving after the fact.

The other door. `/authorize` asks the engine for a decision; this reports one
somebody else already made — a transaction the processor settled, a password
reset, an account opened on a device. Nothing here decides anything: the rows
land in raw capture and the next cycle evaluates them.

**Why three relations and not one.** Two of the four shipped rules are
unreachable from transactions alone, and it is not a limitation that can be
argued away:

  * **L-203** discovers a mule ring from `entity_links`. The cluster builder
    reads edges, not money, so a hundred ingested transfers between four
    accounts produce no ring. The `opened_on` edges are the pattern.
  * **S-077** reads `min_since_password_reset` from `events`. Without the event
    log there is no credential signal and the rule cannot fire, whatever the
    transfer looks like.

So a demo that can only ingest transactions can only demonstrate half the
model. That is the whole reason this file takes three shapes.

**Duplicates are dropped, not merged, and not treated as errors.** Raw capture
is append-only, so an arriving row may not take the identity of one already
recorded — from that point on no feature could tell the two apart. But a client
re-POSTing after a timeout is retrying, not failing, and a receipt that counted
a retry as an error pushes a caller toward doing something worse. They are
counted separately and named.

Identity differs by relation, and the difference is honest rather than tidy:

  * `transactions` has `txn_id`, a real primary key.
  * `entity_links` has a BIGSERIAL id and a natural key that MEANS something —
    (from, to, link_type). Two identical edges are one edge, and the cluster
    builder counts DISTINCT accounts per device, so a duplicate would be
    harmless to the ring and misleading to anyone reading the link layer.
  * `events` has neither, and is deliberately left non-idempotent. There is no
    natural key for "a password reset happened", the reducer that reads them
    (`age_minutes_latest`) is insensitive to a repeat at the same instant, and
    inventing a synthetic key to make the receipt look tidier would be inventing
    a fact. The receipt says so rather than implying otherwise.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Sequence

import psycopg

from ..features.predicate import load_allowlist
from . import records
from .records import ARRIVALS, RecordRefused

SOURCE = "ingested"

# What a caller may not state on a settled row. Short, because this door is FOR
# reporting what already happened: `auth_result`, `decline_reason` and
# `synthetic_label` are all legitimate here and refused on `/authorize`.
UNINGESTABLE_COLUMNS: frozenset[str] = frozenset({"ingested_at", "source"})

# `events` has no natural key, and this is where that is said out loud rather
# than papered over. See the module docstring.
NON_IDEMPOTENT_NOTE = (
    "events have no natural key, so this relation is not idempotent: re-sending "
    "the same batch appends it again. `age_minutes_latest` — the only reducer "
    "that reads them — is insensitive to a repeat at the same instant, and "
    "inventing a key to make this receipt tidier would be inventing a fact."
)


@dataclass
class Receipt:
    """What a batch did to the database. The engine's terms."""

    relation: str
    submitted: int
    written: int = 0
    duplicates: list[str] = field(default_factory=list)
    rejected: list[tuple[int, list[str]]] = field(default_factory=list)
    devices_registered: list[str] = field(default_factory=list)
    max_occurred_at: datetime | None = None
    idempotent: bool = True


def ingest(conn: psycopg.Connection, relation: str,
           rows: Sequence[Mapping[str, Any]],
           register_devices: bool = True) -> Receipt:
    """Write a batch of arriving rows. Does not commit — the route does.

    Partial acceptance is deliberate: a batch of two hundred transactions with
    one bad card reference writes the other hundred and ninety-nine and names
    the one it refused, with its index. Refusing the whole batch would make a
    caller re-send everything to fix one row, and re-sending is exactly the
    thing the duplicate handling exists to make safe.
    """
    spec = ARRIVALS[relation]
    allowlist = load_allowlist(conn)
    receipt = Receipt(relation=relation, submitted=len(rows),
                      idempotent=relation != "events")

    for index, row in enumerate(rows):
        try:
            prepared = records.prepare(
                conn, relation, row, allowlist,
                forbidden=UNINGESTABLE_COLUMNS, id_prefix="ING",
                refuse_existing_key=False)
        except RecordRefused as exc:
            receipt.rejected.append((index, exc.reasons))
            continue

        if spec.key_column and records.key_exists(conn, spec, prepared[spec.key_column]):
            receipt.duplicates.append(str(prepared[spec.key_column]))
            continue
        if spec.natural_key and records.natural_key_exists(conn, spec, prepared):
            receipt.duplicates.append(_natural_key_label(spec, prepared))
            continue

        # A transaction may present a device nobody has seen. Registering it is
        # the same act `/authorize` performs, through the same helper and at the
        # same point in the sequence — after validation, before the INSERT — so
        # the two doors cannot disagree about what an unknown fingerprint means.
        if register_devices:
            created = records.ensure_device(conn, relation, prepared, SOURCE)
            if created:
                receipt.devices_registered.append(created)

        prepared["source"] = SOURCE
        records.insert_row(conn, relation, prepared, allowlist)
        receipt.written += 1
        receipt.max_occurred_at = _newest(receipt.max_occurred_at,
                                          _instant(spec, prepared))

    return receipt


def _instant(spec, row: Mapping[str, Any]) -> datetime | None:
    """Event time, from the column `predicate.TIME_COLUMN` names for the
    relation — `occurred_at` for transactions and events, `first_seen` for a
    link. Derived from the relation rather than passed in, for the same reason
    the feature compiler derives it: a caller cannot point the watermark at the
    wrong column."""
    return row.get(spec.time_column) if spec.time_column else None


def _newest(current: datetime | None, candidate: datetime | None) -> datetime | None:
    if candidate is None:
        return current
    return candidate if current is None else max(current, candidate)


def _natural_key_label(spec, row: Mapping[str, Any]) -> str:
    return ":".join(str(row.get(c)) for c in spec.natural_key)
