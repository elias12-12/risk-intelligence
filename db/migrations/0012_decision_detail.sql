-- =====================================================================
-- 0012_decision_detail.sql  ·  The record-now columns.
--
-- Everything here exists so a decision can be EXPLAINED and REPLAYED later:
-- which evaluation it belonged to, what triggered it, which rule and feature
-- versions were in force, what evidence was missing, who vetoed, and whether a
-- preventive action cleared its own threshold.
--
-- Four items are beyond the original Part II list and are deliberate:
--   rule_definitions.evaluation_lag  — §4 is unimplementable without it
--   rule_conditions.is_required      — AND semantics otherwise force mitigators
--                                      to fire before a rule can be satisfied,
--                                      which inverts what a mitigator is
--   decisions.vetoed_by / prevent_threshold_met — quoted by the §12 payload,
--                                      so they must be stored to be served
--   score_bands                      — makes Week 4 calibration an UPDATE
-- =====================================================================
BEGIN;

ALTER TABLE decisions
    ADD COLUMN evaluation_id        TEXT,
    ADD COLUMN evaluation_trigger   TEXT,      -- what caused this pass to run
    ADD COLUMN trigger_type         TEXT REFERENCES ref_subject_type,
    ADD COLUMN trigger_id           TEXT,
    ADD COLUMN rule_version_set     JSONB,
    ADD COLUMN feature_version_set  JSONB,
    ADD COLUMN degraded_features    TEXT[],
    ADD COLUMN action_source_rule   TEXT REFERENCES rule_definitions,
    ADD COLUMN vetoed_by            TEXT REFERENCES rule_definitions,
    ADD COLUMN prevent_threshold_met BOOLEAN,
    ADD COLUMN pit_bound_at         TIMESTAMPTZ,   -- the as_of ceiling actually used
    ADD COLUMN replay_as_of         TIMESTAMPTZ;   -- the computed_at ceiling, for replay

-- §6 says "one decision per (subject, lane, evaluation)". Today that is a claim
-- with no enforcement. This is the enforcement.
CREATE UNIQUE INDEX ux_decisions_eval
    ON decisions (subject_type, subject_id, execution_mode, evaluation_id);

ALTER TABLE rule_definitions
    ADD COLUMN is_veto           BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN prevent_threshold NUMERIC,
    ADD COLUMN evaluation_lag    INTERVAL NOT NULL DEFAULT '0 seconds';

COMMENT ON COLUMN rule_definitions.evaluation_lag IS
 'How far after occurred_at this rule is allowed to see. An async rule that '
 'needs 15 minutes of downstream activity reads at occurred_at + lag — never '
 'at decided_at, which would reintroduce lookahead on replay.';

ALTER TABLE rule_conditions
    ADD COLUMN is_required BOOLEAN NOT NULL DEFAULT TRUE;

COMMENT ON COLUMN rule_conditions.is_required IS
 'Only required conditions participate in satisfaction. Contribution is summed '
 'over conditions that FIRED, required or not — so a mitigator can lower a '
 'score without being a precondition for the rule firing at all.';

ALTER TABLE alert_signals
    ADD COLUMN asserted_by_rules TEXT[],
    ADD COLUMN feature_value     JSONB,
    ADD COLUMN value_as_of       TIMESTAMPTZ,
    ADD COLUMN value_computed_at TIMESTAMPTZ,
    ADD CONSTRAINT ck_signal_direction
        CHECK (direction IN ('aggravating','mitigating','veto'));

ALTER TABLE alerts
    ADD COLUMN dedup_key         TEXT,
    ADD COLUMN triggering_events INT NOT NULL DEFAULT 1;

-- Reversal of 0007's "nullable; a network alert may aggregate several".
-- Under §6, consolidation guarantees one decision per (subject, lane,
-- evaluation), so cross-decision aggregation no longer happens. This is the
-- SECOND non-additive change in the plan, not the only one.
ALTER TABLE alerts ALTER COLUMN decision_id SET NOT NULL;

ALTER TABLE transactions ADD COLUMN synthetic_label TEXT;
COMMENT ON COLUMN transactions.synthetic_label IS
 'FIXTURE ARTIFACT. Ground truth for measuring the detection rate against a '
 'synthetic population. A production schema must not have this column — real '
 'labels arrive through case_outcomes, weeks later and incompletely.';

-- §6 says the global 70/45 cutoffs cannot survive consolidation: a subject
-- scored by three rules is not comparable to one scored by one. Seeding
-- today's values here makes Week 4 an UPDATE rather than a code change.
CREATE TABLE score_bands (
    subject_type  TEXT NOT NULL REFERENCES ref_subject_type,
    band          TEXT NOT NULL,
    min_score     NUMERIC NOT NULL,
    calibrated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    basis         TEXT,
    PRIMARY KEY (subject_type, band)
);

COMMIT;
