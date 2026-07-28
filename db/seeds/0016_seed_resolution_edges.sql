-- =====================================================================
-- 0016_seed_resolution_edges.sql  ·  The resolution graph.
--
-- A rule's SUBJECT is not the entity its features key on. R-114's subject is a
-- transaction; card_cnp_count keys on a card; device_first_seen_min keys on a
-- device. Today the scorer joins on feature_key + entity_type and nothing else,
-- so every card's value matches every transaction of every rule — that is
-- defect #1, and this table is the fix.
--
-- 'card -> customer' deliberately has NO row: BFS finds it in two hops through
-- account. Ties break by ascending edge_id, so route selection is deterministic
-- and reproducible.
-- =====================================================================
BEGIN;

INSERT INTO resolution_edges
 (from_type, edge_name, to_type, kind, relation, key_column, value_column, filter_equals, cardinality, description) VALUES
 -- a transaction reaches its own dimensions in one column read
 ('transaction','card',    'card',    'column','transactions','txn_id','card_id',    NULL,'one','the card the charge was made on'),
 ('transaction','account', 'account', 'column','transactions','txn_id','account_id', NULL,'one','the account debited or credited'),
 ('transaction','customer','customer','column','transactions','txn_id','customer_id',NULL,'one','the customer who owns the account'),
 ('transaction','device',  'device',  'column','transactions','txn_id','device_id',  NULL,'one','the device fingerprint on the session'),
 ('transaction','merchant','merchant','column','transactions','txn_id','merchant_id',NULL,'one','the acceptor'),

 ('card','account','account','column','cards','card_id','account_id',NULL,'one','the account the card draws on'),
 ('account','customer','customer','column','accounts','account_id','customer_id',NULL,'one','the account holder'),

 -- a device reaches the accounts opened on it, through the link layer
 ('device','opened_account','account','link','entity_links','from_id','to_id',
  '{"link_type":"opened_on","from_type":"device"}'::jsonb,'many','accounts opened on this device'),

 -- a network reaches its members, through the cluster layer
 ('network','member_account','account','cluster','cluster_members','cluster_id','subject_id',
  '{"subject_type":"account"}'::jsonb,'many','member accounts of the cluster'),
 ('network','member_device','device','cluster','cluster_members','cluster_id','subject_id',
  '{"subject_type":"device"}'::jsonb,'many','member devices of the cluster'),
 ('network','member_card','card','cluster','cluster_members','cluster_id','subject_id',
  '{"subject_type":"card"}'::jsonb,'many','member cards of the cluster')
ON CONFLICT (from_type, edge_name) DO NOTHING;

COMMIT;
