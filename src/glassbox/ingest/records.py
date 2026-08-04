"""What a row has to satisfy before this system will write it down.

One definition, four callers: the authorization path, the batch ingest path,
the transaction what-if (`engine/simulate.py`), and the tests. The what-if is
the interesting one — it runs every check here and then rolls back, so the row
a caller TESTED and the row a caller COMMITS pass through the same gate. A
second, laxer gate on the simulation side would make the test worthless in the
one direction that matters.

Three kinds of check, in the order they are applied:

  1. **Columns.** Names are checked against the allow-list `predicate.py`
     builds from `information_schema` — the same boundary every feature spec
     and resolution edge passes through. A caller cannot name a column that is
     not really there, and every value is bound.
  2. **Forbidden columns.** Per call, not global, because the answer differs
     by door: `/authorize` may not set `auth_result` (the engine chooses it),
     a fabricated row may not set `synthetic_label` (it is planted ground
     truth), and batch ingest of settled rows may legitimately set both.
  3. **References.** Foreign keys would refuse a bad one anyway — by aborting
     the transaction, which reaches a caller as an IntegrityError about a
     constraint rather than as "there is no card CARD-XYZ". Checked here so a
     refusal is an answer about the request, and checked for the polymorphic
     ones too (`events.subject_id`, `entity_links.from_id`) where there is no
     foreign key at all and a bad id would otherwise create a phantom.

Every problem is collected and reported together. An author fixing errors one
round trip at a time is an author who stops using the endpoint — the same
standard `rules/validate.py` holds for a rule draft.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Sequence
from uuid import uuid4

import psycopg

from ..config import reference_now
from ..db import fetch_all, fetch_value
from ..features.predicate import TIME_COLUMN, check_column


class RecordRefused(ValueError):
    """A row this system will not write down.

    Distinct from a database error: these are refusals about the REQUEST, made
    before anything is attempted, and each carries a sentence a caller can act
    on. `reasons` is every problem found, not the first.
    """

    def __init__(self, reasons: Sequence[str]):
        self.reasons = list(reasons)
        super().__init__("; ".join(self.reasons))


# Which table holds each subject type's identity. Used for the POLYMORPHIC
# references — `events.subject_id` and `entity_links.from_id`/`to_id` carry a
# type in a neighbouring column and have no foreign key, so an id that does not
# exist is not refused by the schema. It is refused here, because a link to an
# account nobody opened builds a cluster out of nothing and the ring that comes
# out of it looks exactly like a real one.
SUBJECT_TABLES: dict[str, tuple[str, str]] = {
    "transaction": ("transactions", "txn_id"),
    "account": ("accounts", "account_id"),
    "card": ("cards", "card_id"),
    "customer": ("customers", "customer_id"),
    "device": ("devices", "device_id"),
    "merchant": ("merchants", "merchant_id"),
    "network": ("clusters", "cluster_id"),
}


@dataclass(frozen=True)
class ArrivalSpec:
    """How one relation accepts a row that arrived rather than was generated."""

    relation: str
    key_column: str | None                  # natural key, for idempotency
    natural_key: tuple[str, ...] = ()       # composite identity where there is no PK
    references: dict[str, tuple[str, str]] = field(default_factory=dict)
    polymorphic: tuple[tuple[str, str], ...] = ()   # (type_column, id_column)
    vocabularies: dict[str, tuple[str, str]] = field(default_factory=dict)
    required: tuple[str, ...] = ()
    defaults: dict[str, Any] = field(default_factory=dict)

    @property
    def time_column(self) -> str | None:
        return TIME_COLUMN.get(self.relation)


# `currency`, `direction`, `txn_type` and `auth_result` are the vocabulary an
# ordinary approved purchase carries, and they are the SAME defaults
# `generate_synthetic.mk_txn` applies to every row it writes. Defined once so
# there is one answer to "what does an unstated column mean", whether the caller
# is the ingest API, the authorization path or a test with a bare dict.
TRANSACTION_DEFAULTS: dict[str, Any] = {
    "currency": "USD", "direction": "debit", "txn_type": "purchase",
    "auth_result": "approved",
}

ARRIVALS: dict[str, ArrivalSpec] = {
    "transactions": ArrivalSpec(
        relation="transactions",
        key_column="txn_id",
        # `device_id` is deliberately NOT here. A device is OBSERVED, so an
        # unrecognised fingerprint is created rather than refused, and checking
        # it as a reference would refuse the one case the check exists to
        # allow. `ensure_device` is what every writer calls instead.
        references={
            "card_id": ("cards", "card_id"),
            "account_id": ("accounts", "account_id"),
            "customer_id": ("customers", "customer_id"),
            "merchant_id": ("merchants", "merchant_id"),
        },
        required=("amount", "occurred_at"),
        defaults=TRANSACTION_DEFAULTS,
    ),
    "events": ArrivalSpec(
        relation="events",
        key_column=None,          # BIGSERIAL; see `is_idempotent` below
        polymorphic=(("subject_type", "subject_id"),),
        vocabularies={
            "event_type": ("ref_event_type", "event_type"),
            "subject_type": ("ref_subject_type", "subject_type"),
        },
        required=("occurred_at", "event_type", "subject_type", "subject_id"),
    ),
    "entity_links": ArrivalSpec(
        relation="entity_links",
        key_column=None,
        natural_key=("from_type", "from_id", "to_type", "to_id", "link_type"),
        polymorphic=(("from_type", "from_id"), ("to_type", "to_id")),
        vocabularies={
            "from_type": ("ref_subject_type", "subject_type"),
            "to_type": ("ref_subject_type", "subject_type"),
        },
        required=("from_type", "from_id", "to_type", "to_id", "link_type"),
    ),
}


def prepare(conn: psycopg.Connection, relation: str, row: Mapping[str, Any],
            allowlist: dict[str, frozenset[str]], *,
            forbidden: frozenset[str] = frozenset(),
            id_prefix: str = "ING",
            as_of: datetime | None = None,
            refuse_existing_key: bool = True) -> dict[str, Any]:
    """Validate one arriving row and fill in what it did not say.

    Runs OUTSIDE any sandbox or write, on purpose. A refusal should reach the
    caller as an answer about their request rather than as a rolled-back
    attempt, and a reference check made after the INSERT would be
    indistinguishable from the foreign key doing it a moment later.
    """
    spec = ARRIVALS[relation]
    supplied = {k: v for k, v in row.items() if v is not None}
    reasons: list[str] = []

    for key in sorted(set(supplied) & forbidden):
        reasons.append(_forbidden_reason(relation, key))

    for key in sorted(supplied):
        if key in forbidden:
            continue
        try:
            check_column(relation, key, allowlist)
        except Exception as exc:                                  # noqa: BLE001
            reasons.append(str(exc))

    prepared = {k: v for k, v in supplied.items() if k not in forbidden}
    for column, value in spec.defaults.items():
        prepared.setdefault(column, value)
    if spec.time_column:
        prepared.setdefault(spec.time_column, as_of or reference_now())
    if spec.key_column:
        prepared.setdefault(spec.key_column,
                            f"{id_prefix}-{uuid4().hex[:12].upper()}")
    # The same normalisation the generator makes: a charge in the base currency
    # has amount_base = amount. Several reducers read amount_base and nothing
    # else (zscore_of_self, pass_through_ratio, out_over_in_ratio), so leaving
    # it null would degrade a mitigator for a reason the caller never chose.
    if relation == "transactions" and prepared.get("amount_base") is None \
            and prepared.get("amount") is not None:
        prepared["amount_base"] = prepared["amount"]

    for column in spec.required:
        if prepared.get(column) is None:
            reasons.append(
                f"{column} is required on {relation}: it is NOT NULL or has no "
                f"sensible default, and a row without it is not a row this "
                f"system could have received")

    if refuse_existing_key and spec.key_column and prepared.get(spec.key_column):
        if key_exists(conn, spec, prepared[spec.key_column]):
            reasons.append(
                f"{spec.key_column} {prepared[spec.key_column]!r} already "
                f"exists. Raw capture is append-only — an arriving row may not "
                f"take the identity of one already recorded, because from that "
                f"point on no feature could tell the two apart")

    reasons.extend(_reference_reasons(conn, spec, prepared))
    reasons.extend(_vocabulary_reasons(conn, spec, prepared))

    if reasons:
        raise RecordRefused(reasons)
    return prepared


def insert_row(conn: psycopg.Connection, relation: str, row: Mapping[str, Any],
               allowlist: dict[str, frozenset[str]]) -> None:
    """One INSERT. Column names come from the allow-list; every value is bound.

    Never `ON CONFLICT ... DO UPDATE`. Raw capture is append-only for the same
    reason `feature_values` is: a row that can be silently rewritten is a row no
    stored decision can be replayed against.
    """
    columns = [check_column(relation, k, allowlist) for k in sorted(row)]
    names = ", ".join(f'"{c}"' for c in columns)
    placeholders = ", ".join(["%s"] * len(columns))
    with conn.cursor() as cur:
        cur.execute(f"INSERT INTO {relation} ({names}) VALUES ({placeholders})",
                    [row[c] for c in columns])


def key_exists(conn: psycopg.Connection, spec: ArrivalSpec, value: Any) -> bool:
    return bool(fetch_value(
        conn,
        f'SELECT count(*) AS n FROM {spec.relation} WHERE "{spec.key_column}" = %s',
        (value,)))


def natural_key_exists(conn: psycopg.Connection, spec: ArrivalSpec,
                       row: Mapping[str, Any]) -> bool:
    """Identity for a relation whose primary key is a surrogate.

    `entity_links` has a BIGSERIAL id, so re-POSTing the same edge would build a
    second one — and the cluster builder counts DISTINCT accounts per device, so
    a duplicated edge is harmless to the ring and misleading to anyone reading
    the link layer. Deduplicating on what the edge MEANS is the honest fix.
    """
    if not spec.natural_key:
        return False
    clauses = " AND ".join(f'"{c}" IS NOT DISTINCT FROM %s' for c in spec.natural_key)
    return bool(fetch_value(
        conn, f"SELECT count(*) AS n FROM {spec.relation} WHERE {clauses}",
        [row.get(c) for c in spec.natural_key]))


def register_device(conn: psycopg.Connection, device_id: str,
                    first_seen: datetime, source: str = "authorized") -> bool:
    """Record a device fingerprint the first time it is presented.

    **A device is OBSERVED; an account is OPENED.** That is the line this draws,
    and it is the reason an unknown `device_id` is created while an unknown
    `card_id` is refused: a fingerprint coming into existence at the moment of
    first use is what actually happens, and `device_first_seen_min` — R-114's
    second condition, worth 21 of its 87 points — is measured from exactly that
    instant. Refusing an unseen device would mean the only demonstrable "new
    device" is one the generator planted, which is the opposite of what the
    feature is for.

    Returns True if it created the row.
    """
    if fetch_value(conn, "SELECT count(*) AS n FROM devices WHERE device_id = %s",
                   (device_id,)):
        return False
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO devices (device_id, first_seen, source) VALUES (%s, %s, %s)",
            (device_id, first_seen, source))
    return True


def ensure_device(conn: psycopg.Connection, relation: str, row: Mapping[str, Any],
                  source: str) -> str | None:
    """Called by every writer, between `prepare` and `insert_row`.

    That position is the whole of it. Before `prepare` and a row rejected for an
    unrelated reason would still have left a device behind; after `insert_row`
    and the foreign key fires first. One helper, called from the same place by
    the authorization path, the batch ingest and the transaction what-if — which
    is what stops `device_first_seen_min` depending on which door a charge came
    through.

    Returns the device id if it created one.
    """
    spec = ARRIVALS[relation]
    device_id = row.get("device_id")
    if not device_id or not spec.time_column:
        return None
    if register_device(conn, device_id, first_seen=row[spec.time_column],
                       source=source):
        return device_id
    return None


# ---------------------------------------------------------------- internals
def _forbidden_reason(relation: str, column: str) -> str:
    if column == "synthetic_label":
        return (
            "synthetic_label cannot be set here: it is PLANTED GROUND TRUTH — "
            "the denominator of the false-negative tile and of every precision "
            "number the condition report and the rule what-if publish. A row "
            "that labelled itself would be answering the question it is "
            "supposed to be asked")
    if column in ("auth_result", "decline_reason"):
        return (
            f"{column} cannot be set on an authorization request: the ENGINE "
            f"decides it. That is the whole difference between /authorize and "
            f"/ingest/transactions — one asks for a decision, the other reports "
            f"one that has already been made")
    return (f"{column!r} cannot be set on an arriving {relation} row: it is a "
            f"property of a row this system produced, not of one it received")


def _reference_reasons(conn: psycopg.Connection, spec: ArrivalSpec,
                       row: Mapping[str, Any]) -> list[str]:
    reasons: list[str] = []
    for column, (table, key) in sorted(spec.references.items()):
        value = row.get(column)
        if value is None:
            continue
        if not fetch_value(conn, f'SELECT count(*) AS n FROM {table} '
                                 f'WHERE "{key}" = %s', (value,)):
            reasons.append(
                f"{column}={value!r} is not a {table[:-1]} this system knows. "
                f"An arriving row describes something that happened to entities "
                f"that already exist — opening one is a different act")

    for type_column, id_column in spec.polymorphic:
        subject_type, subject_id = row.get(type_column), row.get(id_column)
        if subject_type is None or subject_id is None:
            continue
        target = SUBJECT_TABLES.get(subject_type)
        if target is None:
            continue          # the vocabulary check will report the bad type
        table, key = target
        if not fetch_value(conn, f'SELECT count(*) AS n FROM {table} '
                                 f'WHERE "{key}" = %s', (subject_id,)):
            reasons.append(
                f"{id_column}={subject_id!r} is not a known {subject_type}. "
                f"There is no foreign key on this column — the type lives in "
                f"{type_column} — so nothing else would have caught it, and a "
                f"link to an account nobody opened builds a cluster out of "
                f"nothing")
    return reasons


def _vocabulary_reasons(conn: psycopg.Connection, spec: ArrivalSpec,
                        row: Mapping[str, Any]) -> list[str]:
    """Vocabularies are ROWS (`ref_*`), so a new value is an INSERT and never a
    migration — which is exactly why they are validated against the rows rather
    than against a literal in Python. Same argument as WEEK5-PLAN decision 16."""
    reasons: list[str] = []
    for column, (table, key) in sorted(spec.vocabularies.items()):
        value = row.get(column)
        if value is None:
            continue
        known = [r[key] for r in fetch_all(conn, f'SELECT "{key}" FROM {table} '
                                                 f'ORDER BY 1')]
        if value not in known:
            reasons.append(
                f"{column}={value!r} is not in {table}. New values are INSERTs "
                f"into that table, never a code change — known: "
                f"{', '.join(known)}")
    return reasons
