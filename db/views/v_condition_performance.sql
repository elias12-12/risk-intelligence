-- =====================================================================
-- v_condition_performance.sql  ·  §10, the half that finds false positives.
--
-- The bands being uncalibrated is the visible half of §10. The CONTRIBUTION
-- POINTS being equally uncalibrated is the half that produces false positives,
-- and it was unmeasurable until decision_conditions existed: alert_signals holds
-- the post-consolidation survivors of the ALERTED subjects, so it can say
-- nothing about a condition that fired 400 times and never led anywhere.
--
-- PRECISION IS MEASURED PER DIRECTION, and that is not a detail.
--
-- An aggravating condition is right when it fires on fraud. A MITIGATING one is
-- right when it fires on legitimate traffic — that is its entire job. Scoring a
-- mitigator by its fraud rate inverts it, and the inversion is not academic: on
-- this dataset it ranks `entry_mode_chip_pin`, which fired 9,562 times and never
-- once on fraud, as the worst condition in the catalog rather than the best.
-- That is the same inversion §5 objects to when an absent mitigator is treated
-- as a non-firing one, and it has the same cause — reading a deduction as though
-- it were an accusation.
--
-- So: precision_pct is the fraud rate for an aggravator and the LEGITIMATE rate
-- for a mitigator. Higher is better in both cases, and the two are comparable.
--
--   fire_rate_pct        · how much of the population this condition touches.
--   precision_pct        · direction-aware, against transactions.synthetic_label
--                          — exact here, meaningless outside this dataset.
--   alert_precision_pct  · of the firings on decisions that BECAME a case, how
--                          many were confirmed fraud. This is the DECISION's
--                          disposition, not the condition's own doing: a
--                          condition on a veto rule still gets a number whenever
--                          another rule raised the case it was evaluated on.
--   points_per_precision_point · what the catalog charges per unit of measured
--                          precision. This is the column §10 is asking for, and
--                          HIGH IS BAD: the price is not being earned.
--
-- Read-only, deliberately: §10 says calibration output is a recommendation to a
-- human, never an automatic write, because silently retuned weights break the
-- audit story — an analyst could not explain why last week's identical
-- transaction scored differently. Repricing is Week 4.
-- =====================================================================
-- DROP first: CREATE OR REPLACE cannot add or reorder columns, so a view whose
-- shape is still settling has to be replaced outright to stay re-appliable
-- against an existing database as well as a fresh one.
DROP VIEW IF EXISTS v_condition_performance;

CREATE VIEW v_condition_performance AS
WITH verdict AS (
    -- One row per alert. case_outcomes has no uniqueness constraint, so joining
    -- it raw would fan the fire-rate DENOMINATOR out by the number of
    -- dispositions and quietly deflate every rate in this view.
    --
    -- Session 6: latest-wins, `decided_at DESC`. This was first-wins, left behind
    -- when Week 5 moved v_kpi_cases, which meant condition precision — the column
    -- that found the country_is_new_for_customer mispricing — could not be
    -- corrected by an analyst. See v_kpi_rule_attribution.sql for the evidence.
    SELECT alert_id,
           (array_agg(disposition ORDER BY decided_at DESC, outcome_id DESC))[1]
               AS disposition
      FROM case_outcomes
     GROUP BY alert_id
),
observed AS (
    SELECT dc.condition_id,
           dc.fired,
           dc.contributed,
           dc.read_status,
           t.synthetic_label = 'fraud' AS is_fraud,
           v.disposition
      FROM decision_conditions dc
      JOIN decisions d ON d.decision_id = dc.decision_id
      LEFT JOIN transactions t ON t.txn_id = d.trigger_id
      LEFT JOIN verdict v ON v.alert_id = d.alert_id
),
agg AS (
    SELECT rc.rule_id,
           o.condition_id,
           rc.feature_key,
           rc.operator,
           COALESCE(rc.threshold_num::text, rc.threshold_text) AS threshold,
           rc.contribution_points AS priced_points,
           CASE WHEN rc.contribution_points >= 0 THEN 'aggravating'
                ELSE 'mitigating' END AS direction,
           COUNT(*)                                           AS evaluated,
           COUNT(*) FILTER (WHERE o.fired)                    AS fired,
           COUNT(*) FILTER (WHERE o.read_status <> 'present') AS degraded,
           AVG(o.contributed)                                 AS mean_contribution,
           COUNT(*) FILTER (WHERE o.fired AND o.is_fraud IS NOT NULL) AS fired_labelled,
           COUNT(*) FILTER (WHERE o.fired AND o.is_fraud)      AS fired_on_fraud,
           COUNT(*) FILTER (WHERE o.fired AND o.disposition IS NOT NULL) AS fired_on_cases,
           COUNT(*) FILTER (WHERE o.fired AND o.disposition = 'confirmed_fraud')
                                                              AS fired_on_confirmed
      FROM observed o
      JOIN rule_conditions rc ON rc.condition_id = o.condition_id
     GROUP BY rc.rule_id, o.condition_id, rc.feature_key, rc.operator,
              rc.threshold_num, rc.threshold_text, rc.contribution_points
),
scored AS (
    SELECT *,
           ROUND(100.0 * fired / evaluated, 2) AS fire_rate_pct,
           CASE WHEN fired_labelled = 0 THEN NULL
                WHEN direction = 'aggravating'
                     THEN ROUND(100.0 * fired_on_fraud / fired_labelled, 2)
                ELSE ROUND(100.0 * (fired_labelled - fired_on_fraud) / fired_labelled, 2)
           END AS precision_pct,
           CASE WHEN fired_on_cases = 0 THEN NULL
                ELSE ROUND(100.0 * fired_on_confirmed / fired_on_cases, 2)
           END AS alert_precision_pct
      FROM agg
)
SELECT rule_id, condition_id, feature_key, operator, threshold, priced_points,
       direction, evaluated, fired, fire_rate_pct, degraded,
       ROUND(mean_contribution, 3) AS mean_contribution,
       fired_labelled, fired_on_fraud, precision_pct,
       fired_on_cases, alert_precision_pct,
       -- ABS because a mitigator's price is a deduction: the magnitude charged
       -- is what has to be earned, in either direction.
       ROUND(ABS(priced_points) / NULLIF(precision_pct, 0), 3)
           AS points_per_precision_point
  FROM scored
 ORDER BY points_per_precision_point DESC NULLS LAST, rule_id, condition_id;
