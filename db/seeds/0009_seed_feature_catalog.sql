-- =====================================================================
-- 0009_seed_feature_catalog.sql  ·  SEED — the extension seam
-- Everything a rule may reference. inline_capable => can back a preventive
-- (blocking) rule; is_graph => resolves against entity_links.
-- NOTE: behavioral biometrics (behavior_deviation) intentionally omitted
--       (parked as future work).
-- =====================================================================
BEGIN;

INSERT INTO feature_catalog
 (feature_key, display_name, description, entity_type, value_type, window_spec, inline_capable, is_graph, source, default_reason_code) VALUES
 -- ---- CNP burst (R-114) ----
 ('card_cnp_count','CNP charges in window','Count of card-not-present charges on the card in the window','card','numeric','90s',TRUE,FALSE,'derived','VELOCITY_SPIKE'),
 ('card_cnp_pace_ratio','CNP pace vs baseline','Window CNP rate divided by the card''s 30-day baseline rate','card','numeric','90s',TRUE,FALSE,'derived','VELOCITY_SPIKE'),
 ('device_first_seen_min','Device age (minutes)','Minutes since this device fingerprint was first seen','device','numeric',NULL,TRUE,FALSE,'event','NEW_DEVICE'),
 ('session_geo_jump_km','Session geo jump (km)','Great-circle distance from the previous session location','card','numeric',NULL,TRUE,FALSE,'external','GEO_ANOMALY'),
 ('mcc_is_new_for_customer','New MCC for customer','Merchant category never used before by this customer','customer','boolean',NULL,TRUE,FALSE,'derived','NEW_MCC'),
 -- ---- Mule ring (L-203) ----
 ('accounts_per_device','Accounts opened on device','Distinct accounts opened on this device in the window','device','numeric','7d',FALSE,TRUE,'derived','DEVICE_FANOUT'),
 ('structuring_flag','Structuring flag','Inbound amounts sit just under the reporting line','account','boolean','24h',FALSE,FALSE,'derived','STRUCTURING'),
 ('pass_through_ratio','Pass-through ratio','Funds forwarded out / funds in, within the window','account','numeric','24h',FALSE,TRUE,'derived','PASS_THROUGH'),
 ('passthrough_time_min','Pass-through time (minutes)','Minutes between funds arriving and being forwarded','account','numeric',NULL,FALSE,FALSE,'derived','PASS_THROUGH'),
 ('activity_is_passthrough_only','Pass-through only','No payroll, card, or bill activity on the account','account','boolean','7d',FALSE,FALSE,'derived','PASS_THROUGH'),
 ('ring_cohesion','Ring cohesion','Density of the linked-account cluster','network','numeric',NULL,FALSE,TRUE,'derived','DEVICE_FANOUT'),
 -- ---- Account takeover (S-077) ----
 ('min_since_password_reset','Minutes since password reset','Minutes between the last password reset and this movement','account','numeric',NULL,TRUE,FALSE,'event','CREDENTIAL_EVENT'),
 ('new_payee_then_drain','New payee then drain','A newly added payee was paid the full balance','account','boolean',NULL,FALSE,FALSE,'derived','PAYEE_DRAIN'),
 ('ip_is_datacenter','Datacenter IP','Session IP belongs to a datacenter, not a residential ISP','transaction','boolean',NULL,TRUE,FALSE,'external','DATACENTER_IP'),
 ('amount_over_avail_balance_pct','Percent of available balance','Transfer amount as a percent of available balance','account','numeric',NULL,TRUE,FALSE,'derived','PAYEE_DRAIN'),
 -- ---- Travel false-positive (T-021) ----
 ('country_is_new_for_customer','New country for customer','First transaction in this country for the customer','customer','boolean',NULL,TRUE,FALSE,'derived','GEO_ANOMALY'),
 ('recent_travel_purchase','Recent travel purchase','A flight/travel purchase on file explains the location','customer','boolean','7d',TRUE,FALSE,'derived','TRAVEL_EXPLAINED'),
 ('amount_vs_baseline_z','Amount z-score','Standard deviations from the customer''s usual amount','customer','numeric',NULL,TRUE,FALSE,'derived','SPEND_NORMAL'),
 ('entry_mode_chip_pin','Chip-and-PIN present','Card physically present via chip-and-PIN','transaction','boolean',NULL,TRUE,FALSE,'derived','CARD_PRESENT'),
 -- ---- Extras (prove growth = catalog rows, not code) ----
 ('card_txn_count_24h','Card txns (24h)','Total transactions on the card in 24h','card','numeric','24h',TRUE,FALSE,'derived','VELOCITY_SPIKE'),
 ('merchant_decline_burst','Merchant decline burst','Declined authorizations at a merchant in the window (card-testing)','merchant','numeric','10m',TRUE,FALSE,'derived','VELOCITY_SPIKE');

COMMIT;