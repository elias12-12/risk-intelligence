-- =====================================================================
-- 0030_shadow_and_publish.sql  ·  Week 5 session 3 — what a shadow rule
--                                 records, and what a publish snapshots.
--
-- Two defects close here, and they have to close together.
--
-- D1 · NOTHING EVER BUMPS A VERSION. `rule_definitions.version` and
--      `feature_catalog.spec_version` are written once by a seed and never
--      again. Seed 0026 repriced T-021 from +50 to +12 and left version at 1,
--      so a decision from before the reprice and a decision from after it both
--      record `rule_version_set = {"T-021": 1}` — the exact column Part II
--      exists to make replay possible. The version STORES have been empty since
--      0013 created them, so the number resolves to nothing either way.
--
-- D2 · SHADOW MODE IS INERT. `catalog.load_rules` selects status IN
--      ('active','shadow') and `Rule.status` is never read again. A rule
--      authored as shadow scores, alerts and issues preventive actions exactly
--      like a live one. That is worse than not having shadow mode: an admin who
--      believes a rule is being observed is an admin who has just started
--      challenging customers.
--
-- They close together because the write path lands a rule at `shadow` and a
-- shadow rule that acts is a rule that starts touching customers the moment it
-- is saved.
--
-- ---------------------------------------------------------------------
-- O3, ANSWERED: a shadow rule EVALUATES, RECORDS EVERYTHING, AND TAKES NOTHING.
--
-- The alternative — evaluate and record nothing — makes promotion a leap of
-- faith and reduces shadow to a spelling of `inactive`. The whole point of
-- shadow mode is that precision is measurable BEFORE anything is done to a
-- customer, so:
--
--   * a shadow rule is excluded from consolidation and from precedence. It
--     cannot contribute a signal to the published score, cannot hold authority,
--     cannot carry a severity and cannot establish a veto. No alert, no
--     execution, no action;
--   * its conditions still reach `decision_conditions`, flagged `is_shadow`,
--     so fire rate and direction-aware precision come out of the same
--     `v_condition_performance` a live rule is measured by. `contributed` is 0
--     for those rows, which is not a nicety — it is the true statement that the
--     firing moved no published score;
--   * the decision records what it WOULD have been had the shadow rules been
--     active: `shadow_score`, `shadow_action`, `shadow_rules`. Directly
--     comparable to `score` and `action_taken` on the same row, so "what changes
--     if I promote this" is a query rather than a re-run.
--
-- The three columns are NULL wherever no shadow rule applied — which is every
-- decision on the shipped fixtures. This migration moves no stored number.
--
-- Why not a separate shadow_decisions table: a shadow evaluation is not a second
-- decision. It is the same decision under a different control plane, and giving
-- it a row of its own would put it in the denominator of alert volume, of the
-- routing spread, and of every rate §11 publishes.
-- =====================================================================
BEGIN;

-- ---------------------------------------------------------------------
-- D2 · what the shadow rules would have done to this decision.
-- ---------------------------------------------------------------------
ALTER TABLE decisions
    ADD COLUMN shadow_score  NUMERIC,
    ADD COLUMN shadow_action TEXT REFERENCES ref_action,
    ADD COLUMN shadow_rules  TEXT[];

COMMENT ON COLUMN decisions.shadow_action IS
 'The action this decision would have taken if the rules in shadow_rules had '
 'been active. NULL when no shadow rule applied to the subject. Compared '
 'against action_taken on the same row, this is the promotion question — how '
 'often a shadow rule would have changed the outcome, and to what — answered '
 'from stored rows rather than by re-running the lane.';

COMMENT ON COLUMN decisions.shadow_score IS
 'The pooled score over the live AND shadow rules. Never the published score: '
 'decisions.score is the live pool, and a shadow rule cannot move it.';

CREATE INDEX ix_decisions_shadow ON decisions (shadow_action)
    WHERE shadow_action IS NOT NULL;

-- ---------------------------------------------------------------------
-- D2 · the ledger tells the two apart.
--
-- Without this flag a shadow rule's firings are indistinguishable from a live
-- rule's in the one relation §10 measures conditions from, and the report would
-- price a condition on evidence the engine never acted on.
--
-- ck_dc_contributed has to be restated rather than added to: it asserts
-- contributed = priced WHEN fired AND satisfied, and a shadow firing is fired,
-- satisfied and contributes nothing. The rewritten form says exactly that.
-- ---------------------------------------------------------------------
ALTER TABLE decision_conditions
    ADD COLUMN is_shadow BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE decision_conditions
    DROP CONSTRAINT ck_dc_contributed,
    ADD CONSTRAINT ck_dc_contributed CHECK (
        contributed = CASE WHEN fired AND rule_satisfied AND NOT is_shadow
                           THEN priced_points ELSE 0 END);

COMMENT ON COLUMN decision_conditions.is_shadow IS
 'TRUE when the rule was in shadow at evaluation time. The row is kept — that '
 'is what makes shadow precision measurable before promotion — but it '
 'contributed 0 to the published score, and the CHECK enforces it.';

-- ---------------------------------------------------------------------
-- D1 · one definition of what PUBLISHING is.
--
-- These live in SQL rather than in rules/publish.py because they have two
-- callers: the publish path, and seed 0031, which backfills every definition
-- seeded before a publish step existed. A Python snapshotter plus a
-- hand-written seed would be two spellings of "the definition, kept" — the
-- divergence §3.1 argues about for features, applied to the audit trail.
--
-- THE BUMP IS CONDITIONAL, AND THAT IS THE DESIGN. `decisions.rule_version_set`
-- records the version an evaluation READ, so a counter that moves every time
-- somebody presses save makes the set meaningless in the other direction: a
-- hundred versions, ninety-nine of them identical, and no way to tell which
-- change was the one that mattered. So:
--
--   * no snapshot at the current version yet  -> store it, version unchanged.
--     (This is the backfill case: four rules and 23 features that have been
--     stable since Week 1 do not become version 2 for being written down.)
--   * a snapshot exists and the definition has MOVED -> bump, then store.
--   * a snapshot exists and nothing moved -> nothing happens. Publishing twice
--     is not a change.
--
-- `status` is part of the definition, so promoting shadow -> active bumps. That
-- is deliberate: a rule that starts acting is a different rule from the point of
-- view of the decision it then makes, and a replay that could not tell them
-- apart would be answering the wrong question.
--
-- `version` and `created_at` are stripped from the compared document — one is
-- the label being assigned and the other never changes — and `condition_id` is
-- stripped from the conditions because it is a surrogate key recreated on every
-- edit, which would otherwise read as a definition change on every save.
-- ---------------------------------------------------------------------
CREATE FUNCTION rule_definition_document(p_rule_id TEXT)
RETURNS TABLE (definition JSONB, conditions JSONB)
LANGUAGE sql STABLE AS $$
    SELECT to_jsonb(r) - 'version' - 'created_at',
           COALESCE((SELECT jsonb_agg(to_jsonb(c) - 'condition_id'
                                      ORDER BY c.condition_group, c.condition_id)
                       FROM rule_conditions c WHERE c.rule_id = r.rule_id),
                    '[]'::jsonb)
      FROM rule_definitions r
     WHERE r.rule_id = p_rule_id;
$$;

CREATE FUNCTION publish_rule_version(p_rule_id TEXT, p_published_by TEXT)
RETURNS INT
LANGUAGE plpgsql AS $$
DECLARE
    v_version INT;
    v_doc     RECORD;
    v_stored  RECORD;
BEGIN
    SELECT version INTO v_version
      FROM rule_definitions WHERE rule_id = p_rule_id;
    IF v_version IS NULL THEN
        RAISE EXCEPTION 'no rule %', p_rule_id;
    END IF;

    SELECT * INTO v_doc FROM rule_definition_document(p_rule_id);
    SELECT definition, conditions INTO v_stored
      FROM rule_versions WHERE rule_id = p_rule_id AND version = v_version;

    IF FOUND THEN
        IF v_stored.definition IS NOT DISTINCT FROM v_doc.definition
           AND v_stored.conditions IS NOT DISTINCT FROM v_doc.conditions THEN
            RETURN v_version;                       -- nothing moved; no bump
        END IF;
        v_version := v_version + 1;
        UPDATE rule_definitions SET version = v_version WHERE rule_id = p_rule_id;
    END IF;

    INSERT INTO rule_versions (rule_id, version, published_by, status,
                               definition, conditions)
    SELECT p_rule_id, v_version, p_published_by, r.status,
           v_doc.definition, v_doc.conditions
      FROM rule_definitions r WHERE r.rule_id = p_rule_id;

    RETURN v_version;
END;
$$;

COMMENT ON FUNCTION publish_rule_version(TEXT, TEXT) IS
 'Make the current definition of a rule retrievable at a version that '
 'identifies it, bumping rule_definitions.version only if the definition has '
 'actually moved since the last publish. The one definition of publishing: '
 'called by rules/publish.py and by seed 0031''s backfill.';

CREATE FUNCTION publish_feature_version(p_feature_key TEXT, p_published_by TEXT)
RETURNS INT
LANGUAGE plpgsql AS $$
DECLARE
    v_version INT;
    v_doc     JSONB;
    v_stored  JSONB;
BEGIN
    SELECT spec_version, to_jsonb(f) - 'spec_version' - 'created_at'
      INTO v_version, v_doc
      FROM feature_catalog f WHERE feature_key = p_feature_key;
    IF v_version IS NULL THEN
        RAISE EXCEPTION 'no feature %', p_feature_key;
    END IF;

    SELECT definition INTO v_stored
      FROM feature_catalog_versions
     WHERE feature_key = p_feature_key AND spec_version = v_version;

    IF FOUND THEN
        IF v_stored IS NOT DISTINCT FROM v_doc THEN
            RETURN v_version;
        END IF;
        v_version := v_version + 1;
        UPDATE feature_catalog SET spec_version = v_version
         WHERE feature_key = p_feature_key;
        -- Re-read: spec_version is INSIDE the stripped document only by name,
        -- but the row has moved, so the stored copy must be the current one.
        SELECT to_jsonb(f) - 'spec_version' - 'created_at' INTO v_doc
          FROM feature_catalog f WHERE feature_key = p_feature_key;
    END IF;

    INSERT INTO feature_catalog_versions (feature_key, spec_version,
                                          published_by, definition)
    VALUES (p_feature_key, v_version, p_published_by, v_doc);

    RETURN v_version;
END;
$$;

COMMIT;
