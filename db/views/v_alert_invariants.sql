-- =====================================================================
-- v_alert_invariants.sql  ·  §12's two invariants, checkable from SQL.
--
-- The same two properties are enforced in the Pydantic validator and asserted
-- in the test suite. Three layers is deliberate: §1 says an explainable score
-- is the property everything else serves, so it is worth checking from the
-- database, from the server, and from the tests independently.
--
-- This is what verify_scores.sql and the demo query, and what
-- test_invariants_hold_for_every_alert expects to return zero failing rows.
-- =====================================================================
CREATE OR REPLACE VIEW v_alert_invariants AS
SELECT a.alert_id,
       a.subject_type,
       a.subject_id,
       a.score,
       coalesce(s.signal_sum, 0)                       AS signal_sum,
       d.action_taken,
       d.action_source_rule,
       (a.score = coalesce(s.signal_sum, 0))           AS sum_ok,
       (d.action_taken = 'allow' OR d.action_source_rule IS NOT NULL) AS source_rule_ok
  FROM alerts a
  JOIN decisions d ON d.decision_id = a.decision_id
  LEFT JOIN LATERAL (
       SELECT sum(contribution) AS signal_sum
         FROM alert_signals sg
        WHERE sg.alert_id = a.alert_id
  ) s ON TRUE;

COMMENT ON VIEW v_alert_invariants IS
 'sum_ok: the signals shown on the score bar add up to the score. '
 'source_rule_ok: any action other than allow names the rule that chose it. '
 'Both must be true for every row, always.';
