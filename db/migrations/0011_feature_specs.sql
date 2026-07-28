-- =====================================================================
-- 0011_feature_specs.sql  ·  How a feature is COMPUTED, and how a subject
--                            REACHES the entity it keys on.
--
-- Week 1 registered what a feature IS (name, type, window, entity_type).
-- It never said how to compute one, so the generator computed 21 of them in
-- Python and the catalog was decoration. These columns make the catalog the
-- executable definition: one spec, read by the runner, with no second
-- implementation to drift from.
--
-- default_when_absent is JSONB on purpose. A SQL NULL means "no default —
-- write nothing, let absence stay observable" (§5). A JSONB value means
-- "write this", so 0 and false are expressible and distinguishable from
-- unset. That distinction is the whole of §5.
--
-- NOTE: window_spec already exists (0005_features.sql:17). Not re-added.
-- =====================================================================
BEGIN;

ALTER TABLE feature_catalog
    ADD COLUMN source_kind        TEXT,
    ADD COLUMN source_relation    TEXT,
    ADD COLUMN subject_key        TEXT,          -- column identifying the entity the value keys on
    ADD COLUMN scope_key          TEXT,          -- column the history is scoped by (may differ)
    ADD COLUMN resolution_path    TEXT NOT NULL DEFAULT 'auto',
    ADD COLUMN filter_predicate   JSONB,         -- AST, never a SQL string
    ADD COLUMN value_expr         TEXT,          -- a column name; validated against an allow-list
    ADD COLUMN aggregation        TEXT,          -- a named reducer, not an expression language
    ADD COLUMN baseline_spec      JSONB,         -- reducer-specific config (baseline windows etc.)
    ADD COLUMN refresh            TEXT NOT NULL DEFAULT 'incremental',
    ADD COLUMN max_staleness      INTERVAL,
    ADD COLUMN default_when_absent JSONB,
    ADD COLUMN fanout_policy      TEXT NOT NULL DEFAULT 'error',
    ADD COLUMN spec_version       INT  NOT NULL DEFAULT 1;

ALTER TABLE feature_catalog
    ADD CONSTRAINT ck_fc_source_kind CHECK (
        source_kind IS NULL OR source_kind IN
        ('aggregate','dimension','graph','external','sequence')),
    ADD CONSTRAINT ck_fc_refresh CHECK (
        refresh IN ('incremental','batch','on_read')),
    ADD CONSTRAINT ck_fc_fanout CHECK (
        fanout_policy IN ('one','error','max','min','mean','sum',
                          'any_true','all_true','count_distinct')),
    ADD CONSTRAINT ck_fc_aggregation CHECK (
        aggregation IS NULL OR aggregation IN (
            -- the seven §3.1 names ...
            'count','sum','distinct_count','ratio','zscore','bool_exists','min_gap',
            -- ... and the eleven the 21 real features actually need.
            'age_minutes','age_minutes_latest','geo_jump_km','rate_ratio',
            'out_over_in_ratio','pct_of_running_balance','cluster_density',
            'in_reference_set','eq_const','bool_not_exists','zscore_of_self'));

COMMENT ON COLUMN feature_catalog.default_when_absent IS
 'SQL NULL = no default; the runner writes nothing and absence stays observable (§5). '
 'A JSONB value is written when the computation returns no rows.';
COMMENT ON COLUMN feature_catalog.resolution_path IS
 'self | trigger | auto | subject.<edge>[.<edge>...]  — see resolution_edges.';

-- ---------------------------------------------------------------------
-- The resolution graph, as rows.
--
-- A per-feature path string alone is under-powered: the same feature is
-- reached from different subject types. A graph alone is under-specified:
-- two routes can exist and the choice is semantic. So: the graph is here,
-- and feature_catalog.resolution_path selects the route through it.
--
-- filter_equals is a JSON object of column -> literal, compiled through the
-- same allow-list the feature predicates use. No admin-authored SQL text.
-- ---------------------------------------------------------------------
CREATE TABLE resolution_edges (
    edge_id       BIGSERIAL PRIMARY KEY,
    from_type     TEXT NOT NULL REFERENCES ref_subject_type,
    edge_name     TEXT NOT NULL,
    to_type       TEXT NOT NULL REFERENCES ref_subject_type,
    kind          TEXT NOT NULL,          -- column|link|cluster
    relation      TEXT NOT NULL,
    key_column    TEXT NOT NULL,          -- matched against the from_id
    value_column  TEXT NOT NULL,          -- the to_id is read from here
    filter_equals JSONB,
    cardinality   TEXT NOT NULL DEFAULT 'one',
    description   TEXT,
    UNIQUE (from_type, edge_name),
    CONSTRAINT ck_re_kind        CHECK (kind IN ('column','link','cluster')),
    CONSTRAINT ck_re_cardinality CHECK (cardinality IN ('one','many'))
);

CREATE INDEX ix_resolution_edges_from ON resolution_edges (from_type);

-- Which spec produced a stored value. Without it decisions.feature_version_set
-- is an assertion the engine makes about itself; with it, it is reconstructible
-- from the store.
ALTER TABLE feature_values ADD COLUMN spec_version INT;

COMMIT;
