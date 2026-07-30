-- =====================================================================
-- 0017_seed_rule_policy.sql  ·  Satisfaction, veto, prevention, lag.
--
-- SEMANTICS, stated once so nobody has to guess:
--   * conditions within a condition_group are OR'd
--   * groups are combined by rule_definitions.combine
--   * only is_required = TRUE conditions participate in SATISFACTION
--   * contribution is summed over conditions that FIRED, required or not
--   * a rule whose required conditions are unsatisfied contributes NOTHING —
--     there is no partial score
--
-- Today all 16 conditions sit in the default condition_group = 1, which IS the
-- partial-firing bug rather than a separate one: every condition ORs into one
-- group, so any single condition satisfies the rule.
-- =====================================================================
BEGIN;

-- ---- one condition per group => combine='AND' means all four must hold -----
UPDATE rule_conditions c SET condition_group = s.grp
  FROM (SELECT condition_id,
               row_number() OVER (PARTITION BY rule_id ORDER BY condition_id) AS grp
          FROM rule_conditions) s
 WHERE c.condition_id = s.condition_id;

UPDATE rule_definitions SET combine = 'AND';

-- ---- T-021 is the veto rule ------------------------------------------------
-- Its +50 aggravator is NOT required: with AND semantics, requiring it would
-- force the mitigators to fire before the rule could be satisfied, which
-- inverts what a mitigator is. Satisfaction is over the three EXONERATING
-- conditions, so the veto is established by exonerating evidence being
-- present — exactly §7.0's "confirmed travel, trusted payee, allow-lists".
UPDATE rule_definitions SET is_veto = TRUE WHERE rule_id = 'T-021';

UPDATE rule_conditions SET is_required = FALSE
 WHERE rule_id = 'T-021' AND contribution_points > 0;

-- ---- point-in-time: the async lane needs to see what happened AFTER ---------
-- L-203's evaluation is triggered by the last inbound transfer into the ring.
-- At that instant the members have not yet forwarded the funds, so
-- pass_through_ratio is 0 and the rule correctly does not fire. Fifteen minutes
-- later the pattern is complete and it scores 64. That gap is what
-- evaluation_lag is for; using decided_at instead would make a replay read
-- "now" and reintroduce lookahead.
UPDATE rule_definitions SET evaluation_lag = INTERVAL '15 minutes' WHERE rule_id = 'L-203';

-- ---- prevention thresholds --------------------------------------------------
-- Seeded so the four signed-off demo outcomes are UNCHANGED. `hold` is
-- preventive, so naively seeding L-203 at 70 against its score of 64 would
-- demote the mule ring from hold to alert and silently rewrite the demo.
-- Demotion is demonstrated in test_precedence.py by raising a threshold inside
-- a rolled-back transaction instead.
UPDATE rule_definitions SET prevent_threshold = review_threshold
 WHERE rule_id IN ('L-203','S-077');
UPDATE rule_definitions SET prevent_threshold = 85 WHERE rule_id = 'R-114';

COMMIT;
