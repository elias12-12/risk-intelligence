-- =====================================================================
-- v_decision_routing.sql  ·  The invariant 0023 could not express as a CHECK.
--
-- alert_routing is inserted provisionally and corrected in the same transaction
-- once the alert step has run, so a CHECK on (routing, alert_id) would fire mid
-- transaction and PostgreSQL cannot defer one. The rule lives here instead, the
-- same way §12's sum invariant lives in v_alert_invariants: enforced in a layer
-- that reads finished rows.
--
-- Three things must hold for every decision:
--   * no_authority  <=>  no alert. Anything else is a decision claiming to have
--     reached a queue it never reached, or an orphaned alert.
--   * raised        =>  the alert points BACK at this decision. A raised alert
--     whose decision_id is someone else's is a restatement mislabelled.
--   * restated      =>  the alert points back at this decision too — that is
--     what restatement means: the alert now shows THIS evaluation's numbers.
--   * folded/suppressed => the alert belongs to an EARLIER decision. If it
--     pointed here, the fold silently became a raise.
--
-- Expected result: zero rows. Anything returned is a failure, named.
-- =====================================================================
DROP VIEW IF EXISTS v_decision_routing;

CREATE VIEW v_decision_routing AS
SELECT d.decision_id,
       d.subject_type,
       d.subject_id,
       d.execution_mode,
       d.alert_routing,
       d.alert_id,
       a.decision_id AS alert_points_at,
       CASE
         WHEN d.alert_routing = 'no_authority' AND d.alert_id IS NOT NULL
              THEN 'no_authority with an alert'
         WHEN d.alert_routing <> 'no_authority' AND d.alert_id IS NULL
              THEN d.alert_routing || ' with no alert'
         WHEN d.alert_routing IN ('raised','restated') AND a.decision_id <> d.decision_id
              THEN d.alert_routing || ' but the alert points at another decision'
         WHEN d.alert_routing IN ('folded','suppressed') AND a.decision_id = d.decision_id
              THEN d.alert_routing || ' onto its own alert'
         WHEN d.alert_id IS NOT NULL AND a.alert_id IS NULL
              THEN 'alert_id points at a row that does not exist'
       END AS failure
  FROM decisions d
  LEFT JOIN alerts a ON a.alert_id = d.alert_id
 WHERE CASE
         WHEN d.alert_routing = 'no_authority' THEN d.alert_id IS NOT NULL
         WHEN d.alert_id IS NULL THEN TRUE
         WHEN d.alert_routing IN ('raised','restated') THEN a.decision_id <> d.decision_id
         WHEN d.alert_routing IN ('folded','suppressed') THEN a.decision_id = d.decision_id
         ELSE FALSE
       END
    OR (d.alert_id IS NOT NULL AND a.alert_id IS NULL);
