-- =====================================================================
-- 0019_seed_novelty_baseline.sql  ·  A novelty baseline must exclude today.
--
-- "Merchant category never used before by this customer" has to mean BEFORE
-- the current activity, not before this instant. Without a lag, the first of
-- five identical gift-card charges establishes the category, and by the fifth —
-- the one R-114 actually flags — mcc_is_new_for_customer reads FALSE. The burst
-- self-establishes, and R-114 quietly scores 73 instead of 87.
--
-- This was not visible while the generator hand-derived these values against a
-- Python set that never had the burst added to it. It surfaced the moment the
-- feature became a real query, which is the point of making it one.
-- =====================================================================
BEGIN;

UPDATE feature_catalog
   SET baseline_spec = coalesce(baseline_spec, '{}'::jsonb) || '{"baseline_lag":"1d"}'::jsonb
 WHERE feature_key IN ('mcc_is_new_for_customer', 'country_is_new_for_customer');

COMMIT;
