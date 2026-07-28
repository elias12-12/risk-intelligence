-- =====================================================================
-- 0001_reference.sql  ·  Layer 0 — reference / vocabulary (open enums)
-- Vocabularies are rows so new values are INSERTs, never migrations.
-- =====================================================================
BEGIN;
 
CREATE TABLE ref_action (
    action        TEXT PRIMARY KEY,            -- allow|monitor|alert|challenge|hold|block
    severity      SMALLINT NOT NULL,           -- 0..5, ordered
    is_preventive BOOLEAN  NOT NULL,           -- does it stop the event before it completes?
    description   TEXT
);
INSERT INTO ref_action (action, severity, is_preventive, description) VALUES
 ('allow',     0, FALSE, 'Let it through'),
 ('monitor',   1, FALSE, 'Allow but flag for passive watch'),
 ('alert',     2, FALSE, 'Raise into the async review queue (classic detection)'),
 ('challenge', 3, TRUE,  'Step-up auth (OTP / 3DS / biometric) before proceeding'),
 ('hold',      4, TRUE,  'Freeze pending manual approval'),
 ('block',     5, TRUE,  'Decline outright');
 
CREATE TABLE ref_subject_type (
    subject_type TEXT PRIMARY KEY,             -- transaction|account|card|customer|device|merchant|network
    description  TEXT
);
INSERT INTO ref_subject_type (subject_type, description) VALUES
 ('transaction',''), ('account',''), ('card',''), ('customer',''),
 ('device',''), ('merchant',''), ('network','A linked cluster of entities');
 
CREATE TABLE ref_execution_mode (
    mode        TEXT PRIMARY KEY,              -- inline_sync|async
    description TEXT
);
INSERT INTO ref_execution_mode (mode, description) VALUES
 ('inline_sync','Runs in the authorization path; ms budget; may prevent'),
 ('async','Runs after; seconds-to-minutes; raises alerts');
 
CREATE TABLE ref_event_type (
    event_type  TEXT PRIMARY KEY,              -- new behavioral kinds are new rows here
    description TEXT
);
INSERT INTO ref_event_type (event_type, description) VALUES
 ('password_reset',''), ('payee_added',''), ('login',''),
 ('mfa_change',''), ('profile_change',''), ('account_open',''),
 ('device_change',''), ('dormancy_break','');
 
CREATE TABLE ref_reason_code (
    reason_code TEXT PRIMARY KEY,
    description TEXT
);
INSERT INTO ref_reason_code (reason_code, description) VALUES
 ('VELOCITY_SPIKE','Abnormal transaction velocity'),
 ('NEW_DEVICE','First use of an unrecognized device'),
 ('GEO_ANOMALY','Location inconsistent with history'),
 ('NEW_MCC','Merchant category never used by customer'),
 ('STRUCTURING','Amounts structured under a reporting line'),
 ('PASS_THROUGH','Funds forwarded immediately; no genuine activity'),
 ('DEVICE_FANOUT','One device across many accounts'),
 ('CREDENTIAL_EVENT','Recent security event before movement'),
 ('PAYEE_DRAIN','New payee then balance drained'),
 ('DATACENTER_IP','Session from non-residential network'),
 ('TRAVEL_EXPLAINED','Location change explained by prior purchase'),
 ('SPEND_NORMAL','Amount/merchant consistent with baseline'),
 ('CARD_PRESENT','Chip-and-PIN / physically present');
 
COMMIT;