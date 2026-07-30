-- =====================================================================
-- 0002_dimensions.sql  ·  Layer 1 — dimensions (entities)
-- Slowly-changing context. Keys are tokenized, never raw PANs.
-- Every table carries attributes JSONB so new fields don't need a migration.
-- =====================================================================
BEGIN;

CREATE TABLE customers (
    customer_id       TEXT PRIMARY KEY,
    home_country      TEXT,
    home_lat          NUMERIC,
    home_lon          NUMERIC,
    account_open_date DATE,
    risk_tier         TEXT,
    contact_hash      TEXT,                     -- masked; raw contact never stored here
    attributes        JSONB DEFAULT '{}'::jsonb,
    created_at        TIMESTAMPTZ DEFAULT now(),
    updated_at        TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE accounts (
    account_id   TEXT PRIMARY KEY,
    customer_id  TEXT REFERENCES customers,
    account_type TEXT,
    open_date    DATE,
    status       TEXT,
    attributes   JSONB DEFAULT '{}'::jsonb
);

CREATE TABLE cards (
    card_id         TEXT PRIMARY KEY,           -- tokenized
    account_id      TEXT REFERENCES accounts,
    issuing_country TEXT,
    card_type       TEXT,
    first_seen      TIMESTAMPTZ,
    status          TEXT,
    attributes      JSONB DEFAULT '{}'::jsonb
);

CREATE TABLE merchants (
    merchant_id           TEXT PRIMARY KEY,
    name                  TEXT,
    mcc                   TEXT,
    merchant_country      TEXT,
    historical_fraud_rate NUMERIC,
    attributes            JSONB DEFAULT '{}'::jsonb
);

CREATE TABLE devices (
    device_id   TEXT PRIMARY KEY,               -- fingerprint
    first_seen  TIMESTAMPTZ,
    device_type TEXT,
    attributes  JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX ix_accounts_customer ON accounts (customer_id);
CREATE INDEX ix_cards_account     ON cards (account_id);

COMMIT;