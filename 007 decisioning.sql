-- =====================================================================
-- 0007_decisioning.sql  ·  Layer 6 — decisioning / output
-- decisions: EVERY subject through decisioning, incl. silent allows -> powers
--   block-rate & false-positive-on-blocks KPIs; distinguishes prevention.
-- alerts: only the reviewable subset. subject_type is generalized (a ring alert
--   is subject_type='network'); alert_subjects lets one alert cover many members.
-- alert_signals: the one-to-many rationale -> renders the GlassBox score bar.
--   Carries mitigating (negative) contributions and cites a catalog feature OR a
--   free-text model -> score-source-indifferent.
-- =====================================================================
BEGIN;

CREATE TABLE decisions (
    decision_id    BIGSERIAL PRIMARY KEY,
    subject_type   TEXT REFERENCES ref_subject_type,
    subject_id     TEXT NOT NULL,
    occurred_at    TIMESTAMPTZ,                 -- when the event happened
    decided_at     TIMESTAMPTZ DEFAULT now(),
    execution_mode TEXT REFERENCES ref_execution_mode,
    score          NUMERIC,
    band           TEXT,                        -- high|elevated|low (derived from score)
    action_taken   TEXT REFERENCES ref_action,
    fail_mode      TEXT,                        -- open|closed (inline timeout policy applied)
    model_ref      TEXT,                        -- free text, e.g. 'gradient-boost v3.2'
    rules_fired    TEXT[]
);

CREATE TABLE alerts (
    alert_id     BIGSERIAL PRIMARY KEY,
    decision_id  BIGINT REFERENCES decisions,   -- nullable; a network alert may aggregate
    subject_type TEXT REFERENCES ref_subject_type,
    subject_id   TEXT,                           -- primary/named subject
    title        TEXT,
    score        NUMERIC,
    band         TEXT,
    status       TEXT DEFAULT 'open',           -- open|escalated|false_positive|resolved
    assigned_to  TEXT,
    created_at   TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE alert_subjects (                   -- for multi-entity alerts (the mule ring)
    alert_id     BIGINT REFERENCES alerts ON DELETE CASCADE,
    subject_type TEXT REFERENCES ref_subject_type,
    subject_id   TEXT NOT NULL,
    role         TEXT,                           -- member|collector|device...
    PRIMARY KEY (alert_id, subject_type, subject_id)
);

CREATE TABLE alert_signals (                    -- THE one-to-many rationale; renders the bar
    signal_id      BIGSERIAL PRIMARY KEY,
    alert_id       BIGINT REFERENCES alerts ON DELETE CASCADE,
    feature_key    TEXT REFERENCES feature_catalog,   -- nullable if the signal came from a model
    contribution   NUMERIC NOT NULL,             -- signed; negative = mitigating
    direction      TEXT,                          -- aggravating|mitigating
    human_text     TEXT NOT NULL,
    reason_code    TEXT REFERENCES ref_reason_code,
    source_rule_id TEXT REFERENCES rule_definitions,
    source_model   TEXT,
    rank           INT
);

CREATE INDEX ix_signals_alert ON alert_signals (alert_id, rank);

COMMIT;