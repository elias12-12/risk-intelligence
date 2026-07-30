-- =====================================================================
-- 0003_raw_capture.sql  ·  Layer 2 — raw capture (immutable, append-only)
-- transactions: wide + sparse superset. Today's rules read ~1/3 of these
--   columns; the rest are captured so future rules never require a backfill.
-- events: the behavioral log a pure transactions table cannot express.
--   Extends by adding ref_event_type values, never new tables.
-- =====================================================================
BEGIN;

CREATE TABLE transactions (
    txn_id           TEXT PRIMARY KEY,
    occurred_at      TIMESTAMPTZ NOT NULL,      -- precise, tz-aware
    amount           NUMERIC NOT NULL,
    currency         TEXT NOT NULL,
    amount_base      NUMERIC,                   -- normalized to base currency
    direction        TEXT,                      -- debit|credit|inbound|outbound
    txn_type         TEXT,                      -- purchase|transfer|withdrawal|payment
    card_id          TEXT REFERENCES cards,
    account_id       TEXT REFERENCES accounts,
    customer_id      TEXT REFERENCES customers,
    merchant_id      TEXT REFERENCES merchants,
    mcc              TEXT,
    channel          TEXT,                      -- cnp|pos|atm|online|wire
    entry_mode       TEXT,                      -- chip_pin|contactless|keyed|ecom
    auth_result      TEXT,                      -- approved|declined
    decline_reason   TEXT,
    txn_country      TEXT,
    txn_lat          NUMERIC,
    txn_lon          NUMERIC,
    ip_address       INET,
    device_id        TEXT REFERENCES devices,
    payee_id         TEXT,                      -- for transfers
    counterparty     TEXT,                      -- external destination, etc.
    billing_country  TEXT,
    shipping_country TEXT,
    attributes       JSONB DEFAULT '{}'::jsonb, -- sparse catch-all; promote to a column when mature
    ingested_at      TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE events (
    event_id     BIGSERIAL PRIMARY KEY,
    occurred_at  TIMESTAMPTZ NOT NULL,
    event_type   TEXT REFERENCES ref_event_type,
    subject_type TEXT REFERENCES ref_subject_type,
    subject_id   TEXT NOT NULL,                 -- the entity the event is about
    ip_address   INET,
    device_id    TEXT,
    attributes   JSONB DEFAULT '{}'::jsonb,
    ingested_at  TIMESTAMPTZ DEFAULT now()
);

-- Hot paths for velocity / point-in-time feature computation.
CREATE INDEX ix_txn_card_time     ON transactions (card_id, occurred_at DESC);
CREATE INDEX ix_txn_account_time  ON transactions (account_id, occurred_at DESC);
CREATE INDEX ix_txn_customer_time ON transactions (customer_id, occurred_at DESC);
CREATE INDEX ix_txn_device_time   ON transactions (device_id, occurred_at DESC);
CREATE INDEX ix_events_subject    ON events (subject_type, subject_id, occurred_at DESC);

COMMIT;