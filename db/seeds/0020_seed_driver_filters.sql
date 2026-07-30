-- =====================================================================
-- 0020_seed_driver_filters.sql  ·  Narrow a driver only where it must be.
--
-- The runner recomputes a feature at every instant its scope has activity. It
-- does NOT narrow that set by the feature's own filter — see the comment on
-- compiler._driver for why that inference silently breaks windowed features.
--
-- entity_links is the exception: it is heterogeneous, and from_id holds a
-- device id on an 'opened_on' row and an account id on a 'transfer_to' row.
-- Driving accounts_per_device off unfiltered entity_links would compute a
-- device-keyed feature at account ids.
-- =====================================================================
BEGIN;

UPDATE feature_catalog
   SET baseline_spec = coalesce(baseline_spec, '{}'::jsonb) || '{
         "driver_filter": {"op":"and","args":[
             {"op":"eq","col":"link_type","value":"opened_on"},
             {"op":"eq","col":"from_type","value":"device"}]}}'::jsonb
 WHERE feature_key = 'accounts_per_device';

COMMIT;
