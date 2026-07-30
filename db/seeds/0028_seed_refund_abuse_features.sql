-- =====================================================================
-- 0028_seed_refund_abuse_features.sql  ·  §14's second pattern, half of it.
--
-- §14 calls "a new fraud pattern costs inserts, not code" the central claim of
-- the whole design, and its acceptance is BOTH named patterns detecting end to
-- end via INSERT only. Card testing has been a real test since Week 2
-- (test_extension_cardtesting.py, with a psycopg hook that fails the test on any
-- DDL). Refund abuse was never written. This file and
-- test_extension_refundabuse.py are the other half.
--
-- WHAT IS SEEDED HERE, AND WHAT IS NOT.
--
-- Two feature_catalog rows. No rule. That is deliberate and it mirrors
-- merchant_decline_burst, which 0009 seeds with no rule attached precisely so
-- that the extension test can supply one: if the seed shipped the rule too, the
-- test would be checking a seeded rule rather than an authored one, and §14's
-- claim would be demonstrated by the thing it is a claim about.
--
-- WHY THIS PATTERN IS A REAL SECOND TEST AND NOT A VARIATION.
--
--   * The subject is a CUSTOMER. All five demo fixtures are transaction,
--     account or network subjects, and the card-testing extension is a merchant.
--     A customer subject exercises the `_dimension_subject` planner that Week 2
--     added on the argument that §14's claim is false if the planner cannot
--     reach a subject type the schema already defines.
--   * It needs TWO conditions in two condition_groups with combine='AND', so it
--     exercises AND-across-groups rather than a single predicate.
--   * It reads columns no shipped rule reads: txn_type and, for the value half,
--     amount_base as a summed measure rather than a threshold on one row.
--
-- WHY THERE IS NO RATIO, WHICH IS THE OBVIOUS SHAPE FOR THIS PATTERN.
--
-- "Refund volume or value abnormal against purchase history" wants refunds over
-- purchases. §3.1 names a `ratio` reducer and aggregations.py deliberately does
-- not implement it — no catalogued feature used it, and a spec asking for it
-- fails loudly at compile time rather than returning a number nobody defined.
-- Implementing one here to make this pattern land would be the data-engineering
-- ticket §14 says such a pattern costs, and it would make the "INSERTs only"
-- claim false for the very feature offered as evidence for it.
--
-- So the detector is built from `count` and `sum`, both of which already exist,
-- and the README's table stays true: a new feature using an existing reducer is
-- INSERTs; a new reducer is a ticket. The honest cost is that this rule cannot
-- normalise for customer size, and a high-volume customer would need a higher
-- line. On this population it does not have to — see the thresholds below.
--
-- THRESHOLDS, chosen the same way 0027's band cutoffs were: in the empty region
-- between the populations, not at a percentile. Over the 30 days before the
-- reference instant, across 55 customers:
--
--   refunds per customer   12 (x1)  6 (x2)  5 (x1)  4 (x3)  3 (x15)  2 (x10)  1 (x13)
--   refund value, worst    $1,404 for the 12; $628 for the next
--
-- The gaps are 6->12 and $628->$1,404. The rule in the test uses >= 9 and
-- >= 1000, both midpoints. Nothing sits within three refunds or $370 of either
-- line, which is what makes the detection a separation rather than a fit.
--
-- Drivers are left at the default (the source relation, unfiltered). Narrowing
-- the driver to refund rows would reproduce Week-2 defect 4 exactly: the value
-- would only be recomputed on the days a refund happened and would read stale
-- everywhere else, which is how recent_travel_purchase came to be computed twice
-- in the entire dataset.
-- =====================================================================
BEGIN;

INSERT INTO feature_catalog
 (feature_key, display_name, description, entity_type, value_type, window_spec,
  inline_capable, is_graph, source, default_reason_code)
VALUES
 ('customer_refund_count_30d','Refunds (30d)',
  'Refunds posted against this customer''s purchases in the last 30 days',
  'customer','numeric','30d',FALSE,FALSE,'derived','REFUND_ABUSE'),
 ('customer_refund_amount_30d','Refund value (30d)',
  'Total value of refunds posted to this customer in the last 30 days',
  'customer','numeric','30d',FALSE,FALSE,'derived','REFUND_ABUSE')
ON CONFLICT (feature_key) DO NOTHING;

UPDATE feature_catalog SET
  source_kind='aggregate', source_relation='transactions',
  subject_key='customer_id', scope_key='customer_id',
  aggregation='count', value_expr='txn_id',
  -- The rule's subject IS the customer, so there is no graph hop to make.
  resolution_path='self',
  filter_predicate='{"op":"eq","col":"txn_type","value":"refund"}'::jsonb,
  -- Zero refunds is knowledge, not absence — and this is an aggravating-
  -- direction feature, so a default is permitted. Every MITIGATOR must default
  -- to NULL; test_degraded.py enforces that across the catalog.
  default_when_absent='0'::jsonb,
  refresh='incremental', fanout_policy='one', spec_version=1,
  max_staleness='24 hours'
 WHERE feature_key='customer_refund_count_30d';

UPDATE feature_catalog SET
  source_kind='aggregate', source_relation='transactions',
  subject_key='customer_id', scope_key='customer_id',
  aggregation='sum', value_expr='amount_base',
  resolution_path='self',
  filter_predicate='{"op":"eq","col":"txn_type","value":"refund"}'::jsonb,
  default_when_absent='0'::jsonb,
  refresh='incremental', fanout_policy='one', spec_version=1,
  max_staleness='24 hours'
 WHERE feature_key='customer_refund_amount_30d';

COMMIT;
