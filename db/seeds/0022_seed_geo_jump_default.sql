-- =====================================================================
-- 0022_seed_geo_jump_default.sql  ·  "No jump" is an answer, not a gap.
--
-- session_geo_jump_km had no default, so a card that has only ever transacted
-- in one place read as ABSENT and every one of its transactions carried a
-- degraded_features entry: 9,325 of 9,923 decisions, 99% of all the degradation
-- in the dataset. That is not §5 working, it is §5 being drowned — the five
-- genuinely-degraded features become invisible next to it.
--
-- A card with a single known location has a great-circle jump of zero. That is
-- a fact we know, not one we are missing. It is also an aggravator, so a
-- default is permitted (every MITIGATOR must stay NULL — test_degraded.py
-- enforces that) and defaulting it low is fail-open, matching §2.1.
--
-- The five degradations that remain are all real: an entry mode a wire transfer
-- does not have, a z-score with no baseline behind it, an IP the row never
-- carried, a balance percentage with no outbound to measure, and the one
-- sequence feature Week 3 still owes.
-- =====================================================================
BEGIN;

UPDATE feature_catalog
   SET default_when_absent = '0'::jsonb
 WHERE feature_key = 'session_geo_jump_km';

COMMIT;
