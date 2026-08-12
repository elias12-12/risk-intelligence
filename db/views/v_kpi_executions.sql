-- =====================================================================
-- v_kpi_executions.sql  ·  What was actually done to a customer, and how it went.
--
-- Two tiles read this: block / challenge rates, and prevention precision.
--
-- COUNTED OFF EXECUTIONS, NEVER OFF DECISIONS. §8 is explicit: with nothing
-- issuing anything, "block rate" counts decisions to block rather than blocks.
-- The two differ by more than a rename here — §9 issues preventive actions when
-- a case is RAISED and not when it folds, so a ring re-evaluated every fifteen
-- minutes authorises one step-up and ninety-six intentions. Off decisions, the
-- rate would be double the number of customers actually affected.
--
-- `synthetic` is carried through and never defaulted away. Nothing external
-- answers a step-up here; resolve_actions.py settles them deterministically
-- against transactions.synthetic_label. A surface that presented that as a
-- measured pass rate would be making the kind of claim §11 objects to, so the
-- flag rides on kpis.v1 exactly as it does on executions.v1.
--
-- PREVENTION PRECISION is the join §8 was built for and it is the one number on
-- this dataset that is a genuine zero rather than an absence: a preventive
-- action whose challenge PASSED on a case later dispositioned `confirmed_legit`
-- is a prevention false positive. All six preventive actions here land on
-- fraud-labelled subjects, so no challenge passes and nothing is dispositioned
-- legitimate. The join works; it has nothing to find. test_execution.py
-- exercises it on a constructed case rather than pretending otherwise.
--
-- SESSION 6: THE VERDICT IS THE LATEST DISPOSITION. This CTE is a copy of
-- v_kpi_cases', and it kept `ORDER BY decided_at, outcome_id` when Week 5 moved
-- the original to latest-wins. Prevention FP/TP therefore answered off the
-- synthetic settler's verdict forever, while the false-positive tile two rows up
-- the same screen answered off the analyst's correction. See
-- v_kpi_rule_attribution.sql for the three-case evidence; the fix is this one
-- clause, in the three views that had copied the old one.
-- =====================================================================
DROP VIEW IF EXISTS v_kpi_executions;

CREATE VIEW v_kpi_executions AS
WITH verdict AS (
    SELECT alert_id,
           (array_agg(disposition ORDER BY decided_at DESC, outcome_id DESC))[1]
               AS disposition
      FROM case_outcomes
     GROUP BY alert_id
)
SELECT x.execution_id,
       x.issued_at                                     AS event_at,
       x.decision_id,
       x.alert_id,
       x.subject_type,
       x.subject_id,
       x.action,
       x.channel,
       x.outcome,
       x.outcome_source,
       x.resolved_at,
       x.synthetic,
       -- `notify` is a channel-level execution, not a rung on the severity
       -- ladder — 0013 keeps it off ref_action for that reason. A notification
       -- costs an analyst a glance; the other three touch a customer, and only
       -- those belong in a "block rate".
       (x.action IN ('challenge', 'hold', 'block'))    AS is_preventive,
       (x.resolved_at IS NOT NULL)                     AS is_settled,
       CASE WHEN x.resolved_at IS NULL THEN NULL
            ELSE EXTRACT(EPOCH FROM (x.resolved_at - x.issued_at))
       END                                             AS latency_seconds,
       v.disposition,
       (x.action = 'challenge' AND x.outcome = 'passed'
        AND v.disposition = 'confirmed_legit')         AS is_prevention_false_positive,
       (x.action IN ('challenge', 'hold', 'block')
        AND v.disposition = 'confirmed_fraud')         AS is_prevention_true_positive
  FROM action_executions x
  LEFT JOIN verdict v ON v.alert_id = x.alert_id;
