-- =====================================================================
-- 0024_seed_alert_policy.sql  ·  §9 hygiene policy, and where actions go.
--
-- Two tables of policy, both rows rather than code for the same reason
-- score_bands is: these numbers are wrong today (they are chosen, not measured)
-- and correcting them in Week 4 should be an UPDATE.
--
-- The open windows are sized to the CADENCE OF THE SUBJECT, which is the whole
-- point of keying on subject_type:
--
--   network  · the graph cycle runs every 15 minutes (§2.2), so a ring would
--              raise the same alert 96 times a day. A 7-day window makes a ring
--              one case for as long as it keeps meeting its rule.
--   account  · takeover investigations run over days, not minutes.
--   transaction and the four dimension types · a card under test trips the same
--              rule set repeatedly within a day; that is one case with many
--              triggering events.
--
-- The priority formula these weights feed (contract/queue.py) is:
--
--     score_factor    = score
--     exposure_factor = 1 + exposure_weight * log10(1 + max(exposure, floor))
--     recency_factor  = 0.5 ^ (age_hours / priority_half_life)
--     priority        = score_factor * exposure_factor * recency_factor
--
-- log10 rather than the raw amount because amount_base spans orders of
-- magnitude: undamped, exposure alone decides the order and the score stops
-- mattering. The floor exists so a missing or zero exposure damps the priority
-- instead of zeroing it — an unpriced alert must still be reachable in a queue.
--
-- Sized against §9's own example: a 72 with $40,000 at risk must outrank an 88
-- with $30. It does — 72 * 3.30 = 238 against 88 * 1.74 = 153.
-- =====================================================================
BEGIN;

INSERT INTO alert_policy (subject_type, open_window, suppress_while_open,
                          priority_half_life, exposure_weight, exposure_floor, basis)
VALUES
 ('network',     '7 days',  TRUE, '48 hours', 0.5, 1.00,
  'graph cycle is 15 min (§2.2); without a long window a ring alerts 96x/day'),
 ('account',     '3 days',  TRUE, '24 hours', 0.5, 1.00,
  'takeover investigations run over days'),
 ('customer',    '3 days',  TRUE, '24 hours', 0.5, 1.00,
  'takeover investigations run over days'),
 ('transaction', '24 hours', TRUE, '12 hours', 0.5, 1.00,
  'a card under test trips one rule set many times in a day: one case, many events'),
 ('card',        '24 hours', TRUE, '12 hours', 0.5, 1.00,
  'a card under test trips one rule set many times in a day: one case, many events'),
 ('device',      '24 hours', TRUE, '12 hours', 0.5, 1.00,
  'a card under test trips one rule set many times in a day: one case, many events'),
 ('merchant',    '24 hours', TRUE, '12 hours', 0.5, 1.00,
  'a card under test trips one rule set many times in a day: one case, many events')
ON CONFLICT (subject_type) DO NOTHING;

-- Every subject type must have a policy or the engine has no window to fold in.
-- Cheaper to fail here than to discover a silently-unfolded subject type later.
DO $$
DECLARE missing TEXT;
BEGIN
    SELECT string_agg(st.subject_type, ', ') INTO missing
      FROM ref_subject_type st
      LEFT JOIN alert_policy p ON p.subject_type = st.subject_type
     WHERE p.subject_type IS NULL;
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'subject types with no alert_policy row: %', missing;
    END IF;
END $$;

-- ---------------------------------------------------------------------
-- Where an action is delivered. Severity-routed, which is Feature IV of the
-- scope document arriving as rows rather than as a side channel.
--
-- `notify` appears here and NOT in ref_action, exactly as 0013 explains: it is
-- a channel-level execution, not a rung on the severity ladder.
--
-- A step-up on a high-band decision goes to SMS OTP rather than an app push
-- because the app session is the thing under suspicion. Holds and blocks route
-- to the analyst queue: nothing about them is customer-facing.
-- ---------------------------------------------------------------------
INSERT INTO action_routing (action, band, channel, basis) VALUES
 ('challenge','high',    'sms_otp', 'out-of-band: the app session is what is suspect'),
 ('challenge','elevated','app_push','in-band step-up is proportionate below the high band'),
 ('challenge','low',     'app_push','in-band step-up is proportionate below the high band'),
 ('hold',     'high',    'queue',   'analyst decides; nothing customer-facing'),
 ('hold',     'elevated','queue',   'analyst decides; nothing customer-facing'),
 ('hold',     'low',     'queue',   'analyst decides; nothing customer-facing'),
 ('block',    'high',    'queue',   'analyst decides; nothing customer-facing'),
 ('block',    'elevated','queue',   'analyst decides; nothing customer-facing'),
 ('block',    'low',     'queue',   'analyst decides; nothing customer-facing'),
 ('notify',   'high',    'phone',   'high band pages a human'),
 ('notify',   'elevated','queue',   'queued for the next triage pass'),
 ('notify',   'low',     'queue',   'queued for the next triage pass')
ON CONFLICT (action, band) DO NOTHING;

COMMIT;
