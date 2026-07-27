-- =====================================================================
-- 0006_rules.sql  ·  Layer 5 — detection logic (rules as data)
-- A rule is a stored object interpreted at runtime, not code. The FK on
-- rule_conditions.feature_key enforces the invariant that rules can only be
-- built from registered catalog features. A subject's score is the sum of the
-- contribution_points of the conditions that fire -> the additive glass-box model.
-- (rule_logic JSONB is reserved for deeply-nested trees; out of scope for now.)
-- =====================================================================
BEGIN;

CREATE TABLE rule_definitions (
    rule_id                 TEXT PRIMARY KEY,   -- e.g. R-114
    name                    TEXT NOT NULL,
    description             TEXT,
    subject_type            TEXT REFERENCES ref_subject_type,
    execution_mode          TEXT REFERENCES ref_execution_mode,
    action                  TEXT REFERENCES ref_action,     -- the consequence
    review_threshold        NUMERIC,            -- score line at which this rule surfaces
    recommended_action_text TEXT,               -- nuanced human recommendation (demo "rec")
    clear_text              TEXT,               -- counterfactual: what would clear it (demo "clear")
    status                  TEXT DEFAULT 'active',  -- active|shadow|inactive
    rule_logic              JSONB,              -- reserved: complex nested trees (future work)
    combine                 TEXT DEFAULT 'AND',
    created_by              TEXT,
    created_at              TIMESTAMPTZ DEFAULT now(),
    version                 INT DEFAULT 1,
    shadow_since            TIMESTAMPTZ         -- set while shadow-testing before go-live
);

CREATE TABLE rule_conditions (
    condition_id        BIGSERIAL PRIMARY KEY,
    rule_id             TEXT REFERENCES rule_definitions ON DELETE CASCADE,
    condition_group     INT DEFAULT 1,          -- groups AND'd/OR'd together
    feature_key         TEXT REFERENCES feature_catalog,   -- enforced: rules reference the catalog
    operator            TEXT NOT NULL,          -- > | >= | < | <= | = | != | in
    threshold_num       NUMERIC,
    threshold_text      TEXT,
    contribution_points NUMERIC NOT NULL,       -- signed; negative = mitigating
    reason_code         TEXT REFERENCES ref_reason_code,
    signal_template     TEXT                    -- human line for the alert signal
);

CREATE INDEX ix_conditions_rule ON rule_conditions (rule_id);

COMMIT;