-- =====================================================================
-- 0008_feedback.sql  ·  Layer 7 — feedback / labels
-- The inbound path of the pipeline: analyst dispositions become labels for
-- retraining and the source of the FP/FN KPIs. A 'block' decision later
-- dispositioned 'confirmed_legit' is a prevention false positive to learn from.
-- =====================================================================
BEGIN;

CREATE TABLE case_outcomes (
    outcome_id    BIGSERIAL PRIMARY KEY,
    alert_id      BIGINT REFERENCES alerts,
    disposition   TEXT NOT NULL,                -- confirmed_fraud|false_positive|confirmed_legit|inconclusive
    analyst_id    TEXT,
    decided_at    TIMESTAMPTZ DEFAULT now(),
    notes         TEXT,
    feeds_retrain BOOLEAN DEFAULT TRUE
);

CREATE INDEX ix_outcomes_alert ON case_outcomes (alert_id);

COMMIT;