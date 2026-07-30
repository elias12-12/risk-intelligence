-- =====================================================================
-- 0005_features.sql  ·  Layer 4 — feature layer (the extension seam)
-- feature_catalog: the registry of everything a rule may reference.
--   inline_capable decides whether a rule on it can run in the auth path
--   and therefore PREVENT; is_graph marks cross-entity (entity_links) features.
-- feature_values: the point-in-time store. Every value is stamped as_of,
--   so windowed features never see the future (no leakage).
-- =====================================================================
BEGIN;

CREATE TABLE feature_catalog (
    feature_key         TEXT PRIMARY KEY,
    display_name        TEXT NOT NULL,
    description         TEXT,
    entity_type         TEXT REFERENCES ref_subject_type,   -- what it keys on
    value_type          TEXT NOT NULL,          -- numeric|boolean|categorical|set
    window_spec         TEXT,                   -- '90s' | '24h' | NULL (stateless)
    inline_capable      BOOLEAN NOT NULL,       -- fast enough to back a blocking rule?
    is_graph            BOOLEAN NOT NULL DEFAULT FALSE,
    source              TEXT,                   -- internal|external|derived|event
    default_reason_code TEXT REFERENCES ref_reason_code,
    status              TEXT DEFAULT 'active',
    created_at          TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE feature_values (
    feature_key TEXT REFERENCES feature_catalog,
    entity_type TEXT REFERENCES ref_subject_type,
    entity_id   TEXT NOT NULL,
    as_of       TIMESTAMPTZ NOT NULL,           -- point-in-time correctness
    value_num   NUMERIC,
    value_bool  BOOLEAN,
    value_text  TEXT,
    value_json  JSONB,
    computed_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (feature_key, entity_type, entity_id, as_of)
);

-- The inline read path: latest value for (entity, feature). In production the
-- inline slice lives in a low-latency store; here an index is enough.
CREATE INDEX ix_fv_lookup ON feature_values (entity_type, entity_id, feature_key, as_of DESC);

COMMIT;