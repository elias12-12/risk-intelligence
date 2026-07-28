-- =====================================================================
-- 0014_bitemporal_features.sql  ·  The one non-additive migration.
--
-- feature_values today is keyed (feature_key, entity_type, entity_id, as_of).
-- That key can hold ONE value per as_of, so recomputing a feature at an as_of
-- that has already been used DESTROYS the value a past decision was made on.
-- Replay then silently produces a different answer than the audit trail says.
--
-- Widening the key to include computed_at makes the store bitemporal: as_of is
-- when the fact was true, computed_at is when we learned it. A replay reads
-- (as_of <= bound AND computed_at <= replay_as_of) and gets what the engine
-- actually saw; a live read takes the newest computed_at and gets today's
-- best answer. Both, from one table.
--
-- RUN THIS WHILE feature_values IS EMPTY. After the generator stops writing
-- feature_values (§7 of the plan), it is empty at migration time and steps 1-3
-- are no-ops. Ordering matters: 0014 before the first run_features.py.
-- =====================================================================
BEGIN;

-- 1. A PK column cannot be nullable. 0005 gave computed_at a DEFAULT but no
--    NOT NULL, so any row inserted with an explicit NULL is still NULL.
UPDATE feature_values SET computed_at = as_of WHERE computed_at IS NULL;

-- 2.
ALTER TABLE feature_values ALTER COLUMN computed_at SET NOT NULL;

-- 3. Fail LOUDLY rather than silently widening the key over data that would
--    collapse. If this raises, the table was not empty and the operator needs
--    to decide what the duplicates mean — the migration must not decide.
DO $$
DECLARE dup_count BIGINT;
BEGIN
    SELECT count(*) INTO dup_count FROM (
        SELECT 1 FROM feature_values
        GROUP BY feature_key, entity_type, entity_id, as_of, computed_at
        HAVING count(*) > 1
    ) d;
    IF dup_count > 0 THEN
        RAISE EXCEPTION
          'feature_values holds % (feature_key, entity_type, entity_id, as_of, computed_at) collisions; refusing to widen the primary key over ambiguous rows',
          dup_count;
    END IF;
END $$;

-- 4.
ALTER TABLE feature_values DROP CONSTRAINT feature_values_pkey;
ALTER TABLE feature_values
    ADD CONSTRAINT feature_values_pkey
    PRIMARY KEY (feature_key, entity_type, entity_id, as_of, computed_at);

-- 5. §4's access pattern is "newest as_of at or before the bound, then newest
--    computed_at at or before the replay ceiling" — both descending.
DROP INDEX IF EXISTS ix_fv_lookup;
CREATE INDEX ix_fv_pit ON feature_values
    (entity_type, entity_id, feature_key, as_of DESC, computed_at DESC);

COMMENT ON TABLE feature_values IS
 'Bitemporal and APPEND-ONLY. A recomputation is an INSERT, never an UPSERT: '
 'any ON CONFLICT ... DO UPDATE here silently defeats 0014 and destroys the '
 'value a past decision was made on. computed_at must be passed explicitly as '
 'clock_timestamp() — the DEFAULT now() is transaction_timestamp(), so two '
 'recomputations in one transaction would collide on the new key.';

COMMIT;
