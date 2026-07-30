-- =====================================================================
-- 0021_seed_veto_requiredness.sql  ·  Correcting how T-021 is required.
--
-- 0017 followed the plan's judgment call 3: T-021's +50 aggravator gets
-- is_required = FALSE and its three mitigators TRUE, "so the veto is
-- established by exonerating evidence being present".
--
-- That establishes the veto correctly and breaks the SCORE. Satisfaction gates
-- contribution, so a missing mitigator makes T-021 unsatisfied and it
-- contributes NOTHING — removing recent_travel_purchase takes TXN-48251 from
-- 31 to 0. §5's acceptance criterion says the opposite in as many words:
-- removing that mitigator must leave the score HIGHER, because the deduction
-- is what disappeared. Both cannot be true while one flag controls both.
--
-- So they are separated. T-021 has NO required conditions: it is a mitigation
-- rule, and it always contributes whatever fired. A rule with no required
-- conditions is satisfied when it has something to say. Veto establishment is
-- defined independently, over the veto rule's MITIGATING conditions — which is
-- what "established by exonerating evidence" meant all along.
--
-- Now:  TXN-48251                       50-9-6-4 = 31   veto established
--       TXN-48251 minus travel evidence 50  -6-4 = 40   veto indeterminate
--       TXN-48300 (with R-114's 87)     87-9-6-4 = 68   veto established, capped
--       any ordinary chip-and-PIN charge          =  0   nothing to explain
-- =====================================================================
BEGIN;

UPDATE rule_conditions SET is_required = FALSE
 WHERE rule_id IN (SELECT rule_id FROM rule_definitions WHERE is_veto);

COMMIT;
