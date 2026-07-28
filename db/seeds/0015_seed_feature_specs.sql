-- =====================================================================
-- 0015_seed_feature_specs.sql  ·  The 21 features, as computable specs.
--
-- Three features are RE-CATALOGUED from customer to transaction:
--   mcc_is_new_for_customer, country_is_new_for_customer, amount_vs_baseline_z
-- They are not pure functions of (customer, as_of) — they depend on THIS
-- transaction's mcc / country / amount. Two transactions of one customer at the
-- same instant have different values, so a customer-keyed store cannot hold
-- them. They are transaction-keyed features that READ customer history:
-- subject_key='txn_id', scope_key='customer_id'.
--
-- This is the one place the old generator's flattening was accidentally right
-- and the catalog was wrong. The fix is not "make the values match the catalog"
-- — it is "make each match the truth".
--
-- default_when_absent is non-null ONLY where "no rows" has a genuine meaning
-- (a count of zero, no password reset on record). Everywhere else it is SQL
-- NULL, so the runner writes nothing and §5 can see the absence. Every feature
-- referenced by a NEGATIVE contribution is NULL here — that invariant is
-- asserted in test_degraded.py, because a mitigator that defaults to false
-- makes §5's entire policy unreachable.
-- =====================================================================
BEGIN;

-- ---- the re-catalogue -------------------------------------------------
UPDATE feature_catalog SET entity_type = 'transaction'
 WHERE feature_key IN ('mcc_is_new_for_customer',
                       'country_is_new_for_customer',
                       'amount_vs_baseline_z');

-- ---- R-114: card-not-present burst ------------------------------------
UPDATE feature_catalog SET
  source_kind='aggregate', source_relation='transactions',
  subject_key='card_id', scope_key='card_id',
  aggregation='count', value_expr='txn_id',
  filter_predicate='{"op":"and","args":[
      {"op":"eq","col":"channel","value":"cnp"},
      {"op":"eq","col":"auth_result","value":"approved"}]}'::jsonb,
  default_when_absent='0'::jsonb, fanout_policy='one',
  max_staleness='24 hours'
 WHERE feature_key='card_cnp_count';

UPDATE feature_catalog SET
  source_kind='aggregate', source_relation='transactions',
  subject_key='card_id', scope_key='card_id',
  aggregation='rate_ratio', value_expr='txn_id',
  filter_predicate='{"op":"eq","col":"channel","value":"cnp"}'::jsonb,
  baseline_spec='{"baseline_window":"30d"}'::jsonb,
  default_when_absent='0'::jsonb, fanout_policy='one',
  max_staleness='24 hours'
 WHERE feature_key='card_cnp_pace_ratio';

-- Driven by transactions, not by the devices row: the value is an AGE, so it
-- changes with as_of even though the source row never does.
UPDATE feature_catalog SET
  source_kind='dimension', source_relation='devices',
  subject_key='device_id', scope_key='device_id',
  aggregation='age_minutes', value_expr='first_seen',
  baseline_spec='{"driver_relation":"transactions","driver_key":"device_id"}'::jsonb,
  default_when_absent=NULL, fanout_policy='one',
  max_staleness='24 hours'
 WHERE feature_key='device_first_seen_min';

-- "Previous session location" is a PLACE, not the previous row: five charges
-- from one location are one session. The reducer walks back to the most recent
-- location that DIFFERS from the newest one.
UPDATE feature_catalog SET
  source_kind='aggregate', source_relation='transactions',
  subject_key='card_id', scope_key='card_id',
  aggregation='geo_jump_km', value_expr='txn_lat',
  default_when_absent=NULL, fanout_policy='one',
  max_staleness='24 hours'
 WHERE feature_key='session_geo_jump_km';

UPDATE feature_catalog SET
  source_kind='aggregate', source_relation='transactions',
  subject_key='txn_id', scope_key='customer_id',
  aggregation='bool_not_exists', value_expr='txn_id',
  filter_predicate='{"op":"eq","col":"mcc","value":{"ref":"self.mcc"}}'::jsonb,
  baseline_spec='{"exclude_self":true}'::jsonb,
  default_when_absent=NULL, fanout_policy='one',
  max_staleness='24 hours'
 WHERE feature_key='mcc_is_new_for_customer';

-- ---- L-203: mule ring --------------------------------------------------
UPDATE feature_catalog SET
  source_kind='graph', source_relation='entity_links',
  subject_key='from_id', scope_key='from_id',
  aggregation='distinct_count', value_expr='to_id',
  filter_predicate='{"op":"and","args":[
      {"op":"eq","col":"link_type","value":"opened_on"},
      {"op":"eq","col":"from_type","value":"device"}]}'::jsonb,
  default_when_absent='0'::jsonb, fanout_policy='one',
  max_staleness='30 days'
 WHERE feature_key='accounts_per_device';

UPDATE feature_catalog SET
  source_kind='aggregate', source_relation='transactions',
  subject_key='account_id', scope_key='account_id',
  aggregation='bool_exists', value_expr='txn_id',
  filter_predicate='{"op":"and","args":[
      {"op":"eq","col":"direction","value":"inbound"},
      {"op":"gte","col":"amount","value":2000},
      {"op":"lt","col":"amount","value":2500}]}'::jsonb,
  default_when_absent=NULL, fanout_policy='any_true',
  max_staleness='24 hours'
 WHERE feature_key='structuring_flag';

-- fanout 'min' so the seeded text "on all 4 accounts" is TRUE when it fires.
UPDATE feature_catalog SET
  source_kind='aggregate', source_relation='transactions',
  subject_key='account_id', scope_key='account_id',
  aggregation='out_over_in_ratio', value_expr='amount_base',
  default_when_absent='0'::jsonb, fanout_policy='min',
  max_staleness='24 hours'
 WHERE feature_key='pass_through_ratio';

UPDATE feature_catalog SET
  source_kind='aggregate', source_relation='transactions',
  subject_key='account_id', scope_key='account_id',
  aggregation='min_gap', value_expr='amount_base',
  default_when_absent=NULL, fanout_policy='min',
  max_staleness='24 hours'
 WHERE feature_key='passthrough_time_min';

UPDATE feature_catalog SET
  source_kind='aggregate', source_relation='transactions',
  subject_key='account_id', scope_key='account_id',
  aggregation='bool_not_exists', value_expr='txn_id',
  filter_predicate='{"op":"or","args":[
      {"op":"eq","col":"txn_type","value":"purchase"},
      {"op":"in","col":"channel","value":["pos","atm"]}]}'::jsonb,
  default_when_absent=NULL, fanout_policy='all_true',
  max_staleness='24 hours'
 WHERE feature_key='activity_is_passthrough_only';

UPDATE feature_catalog SET
  source_kind='graph', source_relation='cluster_members',
  subject_key='cluster_id', scope_key='cluster_id',
  aggregation='cluster_density', value_expr='subject_id',
  resolution_path='self',
  baseline_spec='{"driver_relation":"cluster_members","driver_key":"cluster_id"}'::jsonb,
  default_when_absent=NULL, fanout_policy='one',
  max_staleness='30 days'
 WHERE feature_key='ring_cohesion';

-- ---- S-077: account takeover -------------------------------------------
-- Driven by transactions on the account: the feature means "minutes between the
-- last reset and THIS MOVEMENT", so the movement supplies the as_of. Driving it
-- off the events table would evaluate at the reset itself and always read 0.
-- 999999 encodes "no reset on record" — an unbounded age, not a fabricated one.
UPDATE feature_catalog SET
  source_kind='aggregate', source_relation='events',
  subject_key='subject_id', scope_key='subject_id',
  aggregation='age_minutes_latest', value_expr='occurred_at',
  filter_predicate='{"op":"and","args":[
      {"op":"eq","col":"event_type","value":"password_reset"},
      {"op":"eq","col":"subject_type","value":"account"}]}'::jsonb,
  baseline_spec='{"driver_relation":"transactions","driver_key":"account_id"}'::jsonb,
  default_when_absent='999999'::jsonb, fanout_policy='one',
  max_staleness='24 hours'
 WHERE feature_key='min_since_password_reset';

-- DEFERRED (§17 accepts S-077 stays hand-specified for one more week).
-- The runner raises UnsupportedSourceKind; the generator hand-seeds this one
-- value in a labelled block. One of 21 is honest; nineteen was the old state.
UPDATE feature_catalog SET
  source_kind='sequence', resolution_path='self',
  default_when_absent=NULL, fanout_policy='one',
  max_staleness='24 hours'
 WHERE feature_key='new_payee_then_drain';

-- resolution_path='trigger' — THE load-bearing case. S-077's subject is
-- ACC-2201, but this condition means "the transfer that triggered this
-- evaluation came from a datacenter IP", not "any transaction ever on this
-- account". Without a trigger root the condition is either unresolvable or it
-- fans out over the account's entire history, and both answers are wrong.
UPDATE feature_catalog SET
  source_kind='external', source_relation='transactions',
  subject_key='txn_id', scope_key='txn_id',
  aggregation='in_reference_set', value_expr='ip_address',
  resolution_path='trigger',
  baseline_spec='{"set":["185.220.101.7","185.220.102.11","185.220.103.22"]}'::jsonb,
  default_when_absent=NULL, fanout_policy='one',
  max_staleness='24 hours'
 WHERE feature_key='ip_is_datacenter';

-- No balance column and no ledger exist, so the balance is DERIVED:
-- sum(credits) - sum(debits) up to as_of over a 90d baseline, then the latest
-- outbound transfer as a percent of the balance standing before it. The
-- alternative is adding accounts.available_balance — a Week-1 model gap
-- surfacing in Week 2. Either way, the old avail/avail*100 = 100 had to go.
UPDATE feature_catalog SET
  source_kind='aggregate', source_relation='transactions',
  subject_key='account_id', scope_key='account_id',
  aggregation='pct_of_running_balance', value_expr='amount_base',
  baseline_spec='{"balance_window":"90d"}'::jsonb,
  default_when_absent=NULL, fanout_policy='one',
  max_staleness='24 hours'
 WHERE feature_key='amount_over_avail_balance_pct';

-- ---- T-021: travel false positive ---------------------------------------
UPDATE feature_catalog SET
  source_kind='aggregate', source_relation='transactions',
  subject_key='txn_id', scope_key='customer_id',
  aggregation='bool_not_exists', value_expr='txn_id',
  filter_predicate='{"op":"eq","col":"txn_country","value":{"ref":"self.txn_country"}}'::jsonb,
  baseline_spec='{"exclude_self":true}'::jsonb,
  default_when_absent=NULL, fanout_policy='one',
  max_staleness='24 hours'
 WHERE feature_key='country_is_new_for_customer';

-- THE most important default in the table. If an absent mitigator defaulted to
-- false, the runner would write false, the condition would not fire, it would
-- contribute 0 — and NO degradation would be recorded. §5's whole policy
-- becomes unreachable and T-021's acceptance test passes for the wrong reason.
UPDATE feature_catalog SET
  source_kind='aggregate', source_relation='transactions',
  subject_key='customer_id', scope_key='customer_id',
  aggregation='bool_exists', value_expr='txn_id',
  filter_predicate='{"op":"eq","col":"mcc","value":"4511"}'::jsonb,
  default_when_absent=NULL, fanout_policy='one',
  max_staleness='24 hours'
 WHERE feature_key='recent_travel_purchase';

UPDATE feature_catalog SET
  source_kind='aggregate', source_relation='transactions',
  subject_key='txn_id', scope_key='customer_id',
  aggregation='zscore_of_self', value_expr='amount_base',
  baseline_spec='{"exclude_self":true,"baseline_window":"90d"}'::jsonb,
  default_when_absent=NULL, fanout_policy='one',
  max_staleness='24 hours'
 WHERE feature_key='amount_vs_baseline_z';

UPDATE feature_catalog SET
  source_kind='dimension', source_relation='transactions',
  subject_key='txn_id', scope_key='txn_id',
  aggregation='eq_const', value_expr='entry_mode',
  baseline_spec='{"const":"chip_pin"}'::jsonb,
  default_when_absent=NULL, fanout_policy='one',
  max_staleness='24 hours'
 WHERE feature_key='entry_mode_chip_pin';

-- ---- extras: growth is catalog rows, not code ---------------------------
UPDATE feature_catalog SET
  source_kind='aggregate', source_relation='transactions',
  subject_key='card_id', scope_key='card_id',
  aggregation='count', value_expr='txn_id',
  default_when_absent='0'::jsonb, fanout_policy='one',
  max_staleness='24 hours'
 WHERE feature_key='card_txn_count_24h';

UPDATE feature_catalog SET
  source_kind='aggregate', source_relation='transactions',
  subject_key='merchant_id', scope_key='merchant_id',
  aggregation='count', value_expr='txn_id',
  filter_predicate='{"op":"eq","col":"auth_result","value":"declined"}'::jsonb,
  default_when_absent='0'::jsonb, fanout_policy='one',
  max_staleness='24 hours'
 WHERE feature_key='merchant_decline_burst';

-- ---- guard: no spec may be left half-written ----------------------------
DO $$
DECLARE missing TEXT;
BEGIN
    SELECT string_agg(feature_key, ', ') INTO missing
      FROM feature_catalog
     WHERE source_kind IS NULL
        OR (source_kind <> 'sequence' AND (source_relation IS NULL OR aggregation IS NULL));
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'feature_catalog rows without a computable spec: %', missing;
    END IF;
END $$;

COMMIT;
