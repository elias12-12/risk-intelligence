-- =====================================================================
-- 0031_backfill_published_versions.sql  ·  The definitions that predate
--                                           the publish step.
--
-- `rule_versions` and `feature_catalog_versions` have existed since 0013 and
-- have been empty ever since, because nothing ever published. Every decision
-- stored so far records a `rule_version_set` naming a number with no definition
-- behind it — `explain/case_report.py` prints the numbers AND says they do not
-- resolve, which turned a silent audit gap into a stated one but did not close
-- it.
--
-- Four rules and 23 catalog features were seeded before the publish path
-- existed. This snapshots each at its CURRENT version, through the same
-- `publish_rule_version` / `publish_feature_version` functions 0030 defines
-- and `rules/publish.py` calls — so the backfilled rows and everything written
-- from here on are the same shape by construction, not by agreement.
--
-- ---------------------------------------------------------------------
-- A STATEMENT ABOUT HISTORY, made deliberately rather than by omission.
--
-- Seed 0026 repriced `country_is_new_for_customer` from +50 to +12 and left
-- `rule_definitions.version` at 1 — there was no bump anywhere in the project
-- at the time. So T-021 version 1 denotes TWO definitions: the one that scored
-- TXN-48251 at 31, and the one that scores it at 0.
--
-- This backfill does NOT retroactively call the repriced definition version 2.
-- Two reasons, and the second is the one that decides it:
--
--   * every decision now in the database was stored AFTER 0026 ran, because the
--     database is built from scratch by reset_db.py, so no stored row is
--     actually ambiguous — inventing a version 2 would fabricate a history
--     nothing here lived through;
--   * a stored decision records the version its evaluation READ. Bumping T-021
--     to 2 here would leave every one of those rows pointing at a version 1
--     that no snapshot exists for, converting a gap that is closed into a
--     dangling reference. Retroactive versioning breaks the thing it is meant
--     to fix.
--
-- The honest form of the loss: the reprice happened before versioning existed
-- and is recorded in `db/seeds/0026_reprice_country_novelty.sql` and in
-- HANDOFF.md §W4.2, not in the version store. From 0030 onward an edit that
-- changes a price bumps the counter and snapshots the definition, so this is
-- the last change in the project's history that a version set cannot
-- distinguish.
-- =====================================================================
BEGIN;

SELECT publish_rule_version(rule_id, 'seed:0031_backfill')
  FROM rule_definitions
 ORDER BY rule_id;

SELECT publish_feature_version(feature_key, 'seed:0031_backfill')
  FROM feature_catalog
 ORDER BY feature_key;

-- Belt and braces: the backfill is worthless if it silently snapshotted
-- nothing, and a seed that quietly does nothing is the failure mode this
-- project keeps finding.
DO $$
DECLARE
    missing INT;
BEGIN
    SELECT count(*) INTO missing
      FROM rule_definitions r
      LEFT JOIN rule_versions v
             ON v.rule_id = r.rule_id AND v.version = r.version
     WHERE v.rule_id IS NULL;
    IF missing > 0 THEN
        RAISE EXCEPTION '% rule(s) have no published version after backfill', missing;
    END IF;

    SELECT count(*) INTO missing
      FROM feature_catalog f
      LEFT JOIN feature_catalog_versions v
             ON v.feature_key = f.feature_key AND v.spec_version = f.spec_version
     WHERE v.feature_key IS NULL;
    IF missing > 0 THEN
        RAISE EXCEPTION '% feature(s) have no published version after backfill', missing;
    END IF;
END;
$$;

COMMIT;
