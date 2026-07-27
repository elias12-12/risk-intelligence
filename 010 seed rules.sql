-- =====================================================================
-- 0010_seed_rules.sql  ·  SEED — the four demo rules (rules-as-data)
-- Four rule SHAPES: row-level (R-114, T-021), graph/cross-entity (L-203),
-- sequence (S-077), and mitigating contributions (T-021).
-- Condition points sum to the demo scores: 87 / 64 / 58 / 31.
--
-- Deviations from the demo, all deliberate:
--  * S-077 4th signal uses amount_over_avail_balance_pct (+8) instead of the
--    dropped behavioral-biometrics signal — keeps the score at 58 and matches
--    the demo's own "paid the full available balance" wording.
--  * T-021's aggravator is sized (+50) so the points sum to its displayed 31;
--    the demo's low-risk example did not have its points sum to its score.
-- =====================================================================
BEGIN;

INSERT INTO rule_definitions
 (rule_id, name, subject_type, execution_mode, action, review_threshold, recommended_action_text, clear_text, created_by) VALUES
 ('R-114','Card-not-present burst','transaction','inline_sync','challenge',70,
   'Hold the pending charges and trigger step-up verification by phone. Pattern matches tested-card fraud.',
   'A passed step-up verification from a known device would drop the device and location signals, putting the score at 34 — below the review line.',
   'system'),
 ('L-203','Possible mule ring — 4 linked accounts','network','async','hold',55,
   'Open a network case covering all four accounts and freeze outbound transfers pending review. Do not contact holders yet.',
   'Legitimate shared-household activity (bills, payroll, card use) on any account would break the pass-through pattern and dissolve the ring score.',
   'system'),
 ('S-077','Account takeover pattern','account','async','hold',55,
   'Pause the transfer and confirm with the account owner on the registered phone number, not in-app.',
   'Owner confirmation on the registered number releases the transfer and whitelists the payee for 90 days.',
   'system'),
 ('T-021','Foreign POS after flight purchase','transaction','inline_sync','allow',70,
   'Release. The location change is explained by the ticket purchase — blocking this is exactly the false positive that erodes customer trust.',
   'Already below the line. Marking it a false positive teaches the travel heuristic and protects this customer from future declines abroad.',
   'system');

INSERT INTO rule_conditions
 (rule_id, feature_key, operator, threshold_num, threshold_text, contribution_points, reason_code, signal_template) VALUES
 -- R-114  (87 = 34+21+18+14)
 ('R-114','card_cnp_count','>=',5,NULL,34,'VELOCITY_SPIKE','{n} card-not-present charges in 90 seconds — 12x this card''s normal pace'),
 ('R-114','device_first_seen_min','<=',6,NULL,21,'NEW_DEVICE','New device fingerprint, first seen {v} minutes ago'),
 ('R-114','session_geo_jump_km','>',1400,NULL,18,'GEO_ANOMALY','IP location {v} km from last known session'),
 ('R-114','mcc_is_new_for_customer','=',NULL,'true',14,'NEW_MCC','Merchant category (gift cards) never used on this account'),
 -- L-203  (64 = 22+17+15+10)
 ('L-203','accounts_per_device','>',3,NULL,22,'DEVICE_FANOUT','Same device used to open {v} accounts this week'),
 ('L-203','structuring_flag','=',NULL,'true',17,'STRUCTURING','Inbound transfers structured just under the $2,500 reporting line'),
 ('L-203','pass_through_ratio','>',0.9,NULL,15,'PASS_THROUGH','Funds forwarded out within minutes of arrival on all 4 accounts'),
 ('L-203','activity_is_passthrough_only','=',NULL,'true',10,'PASS_THROUGH','No payroll, card, or bill activity — accounts are pass-through only'),
 -- S-077  (58 = 19+18+13+8)
 ('S-077','min_since_password_reset','<=',11,NULL,19,'CREDENTIAL_EVENT','Password reset {v} minutes before this transfer'),
 ('S-077','new_payee_then_drain','=',NULL,'true',18,'PAYEE_DRAIN','New payee added, then immediately paid the full available balance'),
 ('S-077','ip_is_datacenter','=',NULL,'true',13,'DATACENTER_IP','Session routed through a datacenter IP, not a residential network'),
 ('S-077','amount_over_avail_balance_pct','>=',95,NULL,8,'PAYEE_DRAIN','Transfer drained {v}% of the available balance'),
 -- T-021  (31 = 50-9-6-4)
 ('T-021','country_is_new_for_customer','=',NULL,'true',50,'GEO_ANOMALY','First transaction in Portugal — location changed since yesterday'),
 ('T-021','recent_travel_purchase','=',NULL,'true',-9,'TRAVEL_EXPLAINED','Airline ticket LIS purchased on this card 3 days ago'),
 ('T-021','amount_vs_baseline_z','<',1.0,NULL,-6,'SPEND_NORMAL','Amount and merchant type (restaurant) match normal spending'),
 ('T-021','entry_mode_chip_pin','=',NULL,'true',-4,'CARD_PRESENT','Chip-and-PIN present — card physically used');

COMMIT;