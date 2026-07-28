-- =====================================================================
-- 0013_versions_and_execution.sql  ·  Version stores, action executions,
--                                     and clusters as first-class rows.
--
-- rule_definitions.version sits on a MUTABLE row: editing a rule overwrites
-- the definition an old decision was made under, so a counter alone cannot
-- reconstruct anything. These are version STORES — the definition at publish
-- time, kept.
--
-- clusters/cluster_members exist because §3.3 requires alert_subjects to be
-- derived, not literal. Today RING-1187 is a hardcoded string in the scorer.
-- natural_key is how a rebuild finds the same cluster and keeps that id stable.
-- =====================================================================
BEGIN;

CREATE TABLE feature_catalog_versions (
    feature_key  TEXT NOT NULL REFERENCES feature_catalog,
    spec_version INT  NOT NULL,
    published_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_by TEXT,
    definition   JSONB NOT NULL,
    PRIMARY KEY (feature_key, spec_version)
);

CREATE TABLE rule_versions (
    rule_id      TEXT NOT NULL REFERENCES rule_definitions,
    version      INT  NOT NULL,
    published_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_by TEXT,
    status       TEXT,
    definition   JSONB NOT NULL,
    conditions   JSONB NOT NULL,
    PRIMARY KEY (rule_id, version)
);

-- Table only. §8's issue/resolve machinery is Week 3; this exists now so a
-- Week-2 decision has somewhere to point when Week 3 arrives.
--
-- `action` is a CHECK, not an FK to ref_action, on purpose: `notify` is a
-- channel-level execution, not a rung on the severity ladder. Adding it to
-- ref_action would tie with `alert` at severity 2 and make §7.2's "maximum
-- severity" non-deterministic.
CREATE TABLE action_executions (
    execution_id   BIGSERIAL PRIMARY KEY,
    decision_id    BIGINT NOT NULL REFERENCES decisions,
    action         TEXT NOT NULL,
    channel        TEXT,
    issued_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at    TIMESTAMPTZ,
    outcome        TEXT,
    outcome_source TEXT,
    CONSTRAINT ck_ae_action CHECK (
        action IN ('allow','monitor','alert','challenge','hold','block','notify')),
    CONSTRAINT ck_ae_outcome CHECK (
        outcome IS NULL OR outcome IN
        ('passed','failed','abandoned','expired','overridden','completed')),
    CONSTRAINT ck_ae_outcome_source CHECK (
        outcome_source IS NULL OR outcome_source IN ('customer','analyst','system','timeout'))
);

CREATE INDEX ix_action_exec_decision ON action_executions (decision_id);

CREATE TABLE clusters (
    cluster_id      TEXT PRIMARY KEY,
    cluster_type    TEXT NOT NULL,
    natural_key     TEXT NOT NULL UNIQUE,   -- e.g. 'device_fanout:DEV-F90D2'
    first_seen      TIMESTAMPTZ,
    last_seen       TIMESTAMPTZ,
    member_count    INT NOT NULL DEFAULT 0,
    built_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    builder_version INT NOT NULL DEFAULT 1
);

CREATE TABLE cluster_members (
    cluster_id   TEXT NOT NULL REFERENCES clusters ON DELETE CASCADE,
    subject_type TEXT NOT NULL REFERENCES ref_subject_type,
    subject_id   TEXT NOT NULL,
    role         TEXT,
    joined_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (cluster_id, subject_type, subject_id)
);

CREATE INDEX ix_cluster_members_subject ON cluster_members (subject_type, subject_id);

-- Vocabulary grows by INSERT, never migration — that is the whole point of
-- layer 0. These are the values the Week-2 fixtures and engine emit.
INSERT INTO ref_event_type (event_type, description) VALUES
 ('challenge_issued','A step-up challenge was issued to the customer'),
 ('challenge_outcome','The customer passed, failed or abandoned a challenge'),
 ('auth_declined','An authorization was declined by the issuer'),
 ('refund_issued','A refund was posted against an earlier purchase')
ON CONFLICT DO NOTHING;

INSERT INTO ref_reason_code (reason_code, description) VALUES
 ('CARD_TESTING','Small-value declines probing card validity at one merchant'),
 ('REFUND_ABUSE','Refund volume or value abnormal against purchase history'),
 ('VETO_APPLIED','A veto rule established exonerating evidence; severity capped'),
 ('DEGRADED_EVIDENCE','Evidence was missing, stale or unresolvable at decision time'),
 ('BEHAVIOR_DRIFT','Activity diverges from the established behavioral baseline')
ON CONFLICT DO NOTHING;

-- Deliberately NO new ref_action rows. See the action_executions comment.

COMMIT;
