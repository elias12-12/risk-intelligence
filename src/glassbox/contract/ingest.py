"""ingest.v1 — the first surfaces that let a row into this system.

A sibling of alert.v1, like every contract added since Week 3. `Action`,
`Signal` and `Evidence` are REUSED unmodified: the bar an authorization decision
renders and the bar a stored alert renders are the same object, and giving this
surface its own signal type would be the first step toward the two disagreeing
about the same decision. Nothing here adds a field to any of them, so alert.v1's
digest does not move.

**Two doors, and they mean different things.** That distinction is the whole
design, and it is carried in the models rather than in a docstring:

  * `AuthorizationRequest` has NO `auth_result` and no `decline_reason`, because
    it is asking for a decision. The engine sets both, and the row is committed
    carrying what the engine chose — so a charge the engine declines is never an
    approved transaction. That is what makes prevention real here rather than a
    note attached to a charge that already went through.
  * `TransactionRecord` HAS both, because it reports a decision somebody else
    already made. It also has `synthetic_label`, because deliberately planted
    demo data should be labelled or it is invisible to every precision number in
    the system.

The field list itself lives once, in `TransactionFields`, and is shared with
`contract/simulation.py`'s `TransactionDraft`. Three models describing a
transaction three different ways would drift, and the drift would be silent:
a what-if that accepted a shape the live path refuses is a what-if that answers
a question nobody can act on.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..ingest.records import TRANSACTION_DEFAULTS
from .models import Action, Evidence, Signal, Subject

STRICT = ConfigDict(extra="forbid", frozen=True)
OPEN_INPUT = ConfigDict(extra="forbid")


class TransactionFields(BaseModel):
    """The writable subset of `transactions`, minus everything the caller's
    door decides for them. Closed: an unknown field is a 422, not a silently
    dropped value.

    Every entity reference must ALREADY EXIST — `transactions` carries foreign
    keys to cards, accounts, customers, merchants and devices (0003) and
    `ingest/records.py` checks them before anything is written, so an unknown
    card is a sentence rather than an IntegrityError. The one exception is
    `device_id`: a device is OBSERVED rather than opened, so an unrecognised
    fingerprint is registered at the instant it is first presented. That is not
    a convenience — `device_first_seen_min` is measured from exactly that
    instant, and it is R-114's second condition.

    An omitted field is not the same as a null one. Omitted means "the engine
    fills this in" (see `ingest.records.TRANSACTION_DEFAULTS`); an explicit null
    means the same thing here only because nulls are dropped before validation.
    A form that posts null for every empty input gets the defaults, which is
    what you want and is worth knowing rather than discovering.
    """
    model_config = OPEN_INPUT

    txn_id: str | None = None
    occurred_at: datetime | None = None
    amount: Decimal
    currency: str | None = None
    amount_base: Decimal | None = None
    direction: str | None = None
    txn_type: str | None = None

    card_id: str | None = None
    account_id: str | None = None
    customer_id: str | None = None
    merchant_id: str | None = None
    device_id: str | None = None

    mcc: str | None = None
    channel: str | None = None
    entry_mode: str | None = None

    txn_country: str | None = None
    txn_lat: Decimal | None = None
    txn_lon: Decimal | None = None
    ip_address: str | None = None

    payee_id: str | None = None
    counterparty: str | None = None
    billing_country: str | None = None
    shipping_country: str | None = None

    def columns(self) -> dict[str, Any]:
        """The stated columns only. An omitted field must not reach the writer
        as an explicit null, or it would override the default that exists for
        it — `currency` is NOT NULL, and a null `amount_base` degrades three
        reducers for a reason the caller never chose."""
        return {k: v for k, v in self.model_dump().items() if v is not None}


class AuthorizationRequest(TransactionFields):
    """A charge asking to be approved.

    `auth_result` and `decline_reason` are deliberately absent. The engine
    decides them, the row is written carrying the decision, and raw capture is
    append-only — so there is no moment at which this charge existed as an
    approved transaction and was then downgraded.
    """
    model_config = OPEN_INPUT


class TransactionRecord(TransactionFields):
    """A charge that already happened, reported as it was settled.

    This is the door for planting a card-testing burst: `merchant_decline_burst`
    counts declines, and an authorization request cannot describe one because
    the engine would be deciding it.
    """
    model_config = OPEN_INPUT

    auth_result: str | None = None
    decline_reason: str | None = None
    # Labelling ingested demo data is legitimate and the simulation's refusal
    # does not apply: a row committed as planted fraud SHOULD count toward the
    # false-negative tile, or the tile silently measures a shrinking fraction of
    # the population. Absent means unlabelled, which is also a real answer.
    synthetic_label: str | None = None


class EventRecord(BaseModel):
    """A behavioural event. The log a pure transactions table cannot express —
    and the only way to reach S-077, whose first condition is "a password reset
    N minutes before this transfer"."""
    model_config = OPEN_INPUT

    occurred_at: datetime | None = None
    event_type: str
    subject_type: str
    subject_id: str
    ip_address: str | None = None
    device_id: str | None = None
    attributes: dict[str, Any] | None = None


class LinkRecord(BaseModel):
    """An edge in the link layer, which is what the graph builder reads.

    This is the only way to reach L-203: a ring is discovered from
    `entity_links`, not from transactions, so ingesting transfers alone will
    never surface one however suspicious the money looks.
    """
    model_config = OPEN_INPUT

    from_type: str
    from_id: str
    to_type: str
    to_id: str
    link_type: str
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    weight: Decimal | None = None


# ------------------------------------------------------------------ responses
AUTHORIZATION_BASIS = (
    "The inline lane, run synchronously in this request: the charge was written, "
    "the features an arriving transaction drives were recomputed at its instant, "
    "the rules were evaluated, precedence chose an action, and the row was "
    "committed carrying the auth_result that action implies. Everything here is "
    "STORED — this is not a simulation."
)


class ExecutionIssued(BaseModel):
    """One thing the decision actually caused. Empty when the action was
    `allow`: nothing is issued for a charge nobody objected to."""
    model_config = STRICT

    execution_id: int
    action: str
    channel: str | None = None
    issued_at: datetime | None = None


class AuthorizationOutcome(BaseModel):
    """What the engine did with a charge that asked to be approved.

    `persisted` is `True` and stated, the mirror of `simulation.v1`'s literal
    `false`. The two surfaces are deliberately shaped alike so a console can
    render either — and deliberately not interchangeable, because the guarantee
    each carries is the opposite one.
    """
    model_config = STRICT

    persisted: Literal[True] = True
    txn_id: str
    subject: Subject
    occurred_at: datetime

    # The authorization itself, which is the part that touches money.
    authorization: Literal["approved", "declined"]
    decline_reason: str | None = None
    device_registered: bool = False

    # ...and the decision behind it, in the same shape an alert publishes.
    decision_id: int | None = None
    score: Decimal
    band: str
    action: Action
    signals: list[Signal] = Field(default_factory=list)
    evidence: Evidence

    alert_id: int | None = None
    alert_routing: str | None = None     # raised | folded | restated | suppressed
    executions: list[ExecutionIssued] = Field(default_factory=list)

    latency_ms: Decimal | None = None
    basis: str = AUTHORIZATION_BASIS


class Rejected(BaseModel):
    """One row that was not written, and why. Every reason, not the first."""
    model_config = STRICT

    index: int
    reasons: list[str] = Field(default_factory=list)


INGEST_BASIS = (
    "Rows reported as already settled. They are written to raw capture and "
    "evaluated by the next cycle, not by this request — so an alert appears "
    "when the scheduler next runs, or immediately if a lane is run by hand. "
    "Nothing here decided anything."
)


class IngestReceipt(BaseModel):
    """What a batch actually did to the database.

    `duplicates` is separate from `rejected` on purpose: re-POSTing a batch
    after a timeout is a retry, not an error, and a receipt that counted it as a
    failure would push a caller toward doing something worse. Raw capture is
    append-only, so the duplicate is DROPPED rather than merged — an arriving
    row may not take the identity of one already recorded.
    """
    model_config = STRICT

    relation: str
    submitted: int
    written: int
    duplicates: list[str] = Field(default_factory=list)
    rejected: list[Rejected] = Field(default_factory=list)
    devices_registered: list[str] = Field(default_factory=list)
    # The newest event time in what was written. This is what the background
    # cycle will consume up to, and it is EVENT time — the fixtures are pinned
    # to GLASSBOX_NOW and a wall-clock watermark would place every ingested row
    # months after the history it is meant to be read against.
    max_occurred_at: datetime | None = None
    basis: str = INGEST_BASIS


class CycleReport(BaseModel):
    """One turn of the background cycle, published so the console can show that
    something is actually running rather than implying it."""
    model_config = STRICT

    ran: bool
    reason: str | None = None            # why it did nothing, when it did nothing
    as_of: datetime | None = None
    since: datetime | None = None
    clusters: int = 0
    feature_values: int = 0
    lanes: dict[str, dict[str, int]] = Field(default_factory=dict)
    duration_ms: Decimal | None = None


class RescoreReport(BaseModel):
    """A full-population pass over one lane, against the rules as they stand now.

    A SIBLING OF CycleReport, not a widening of it, because the two answer
    different questions and `CycleReport`'s fields would have to lie to carry
    this one. A cycle reports `since` — the window it consumed — and the counts
    from the cluster and feature stages it ran on the way. A re-score has no
    window by construction (that is the entire point: it ignores watermarks) and
    runs neither of those stages. Published through CycleReport it would report
    `since: null, clusters: 0, feature_values: 0`, which reads as "those stages
    ran and found nothing" rather than "those stages were not part of this", and
    a payload whose zeros mean absence is the thing this project keeps refusing
    to ship.

    `totals` is the raw dict `run_lane` returns rather than a fixed set of named
    counters, for the reason `scripts/run_cycle.py` gives for printing it the
    same way: a writer that learns to count something new should show up here
    without an edit. The console renders `evaluations`, `decisions` and `alerts`
    and ignores the rest.
    """
    model_config = STRICT

    lane: Literal["inline_sync", "async"]
    as_of: datetime
    totals: dict[str, int] = Field(default_factory=dict)
    duration_ms: Decimal | None = None


# ------------------------------------------------------------------- requests
class TransactionBatch(BaseModel):
    model_config = OPEN_INPUT
    transactions: list[TransactionRecord] = Field(min_length=1, max_length=1000)


class EventBatch(BaseModel):
    model_config = OPEN_INPUT
    events: list[EventRecord] = Field(min_length=1, max_length=1000)


class LinkBatch(BaseModel):
    model_config = OPEN_INPUT
    links: list[LinkRecord] = Field(min_length=1, max_length=1000)


assert set(TRANSACTION_DEFAULTS) - set(TransactionFields.model_fields) == {"auth_result"}, (
    "every default must be expressible on the shared field set, except "
    "auth_result — which only the door that reports a settled row may state")
