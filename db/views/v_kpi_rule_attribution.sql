-- =====================================================================
-- v_kpi_rule_attribution.sql  ·  Per-rule precision, after consolidation.
--
-- One tile reads this, and it is the tile §6 predicted would be impossible
-- without a column recorded in advance.
--
-- Consolidation deduplicates signals on (feature_key, direction) keeping the
-- largest magnitude — so when two rules claim the same evidence, the losing
-- rule's attribution is exactly what the mechanism discards. `asserted_by_rules`
-- exists for this and nothing else: "without it, per-rule precision is
-- unrecoverable after dedup, because the responsible rule is exactly what dedup
-- discards."
--
-- TWO ATTRIBUTIONS, and they answer different questions:
--
--   asserted   the rule CLAIMED evidence on this case, whether or not its
--              number survived dedup. Read from alert_signals.asserted_by_rules.
--   carried    the rule chose the ACTION (decisions.action_source_rule). At
--              most one rule per case carries it, and it is the attribution an
--              analyst means when they ask "which rule held this transfer".
--
-- A rule that asserted evidence on a case another rule carried is not thereby
-- wrong, and a precision that conflates the two would punish exactly the
-- corroborating signals consolidation exists to preserve. Both columns are here
-- so kpis.py can publish the one it means and name which.
--
-- The verdict CTE is repeated from v_kpi_cases rather than selected from it. A
-- view that DROPs cannot be replaced while another view depends on it, and
-- these files have to stay individually re-appliable by hand — the same reason
-- v_condition_performance carries its own copy. Five lines of duplication buys
-- three views that never block each other.
--
-- SESSION 6: LATEST-WINS, MATCHING v_kpi_cases. This CTE said
-- `ORDER BY decided_at, outcome_id` — first wins — for eight weeks after Week 5
-- changed v_kpi_cases to latest-wins and did not change the three views that had
-- copied it. The duplication above is defensible; leaving the copies unaligned
-- was not, and v_kpi_cases.sql had already written down the exact failure: the
-- CTE is "kept identical on purpose so the two views cannot drift into
-- disagreeing about what a case's verdict is."
--
-- The evidence, from the dev database before the fix — three cases carrying an
-- analyst correction on top of the synthetic settler's verdict, and the two
-- views disagreeing on every one:
--
--   alert 4    v_kpi_cases confirmed_legit   ·  here confirmed_fraud
--   alert 5    v_kpi_cases confirmed_fraud   ·  here false_positive
--   alert 7    v_kpi_cases false_positive    ·  here confirmed_fraud
--
-- What it moved: false-positive rate, validation outcomes and median triage
-- responded to an analyst's correction while per-rule precision, prevention
-- FP/TP and condition precision stayed frozen on the script's original verdict.
-- Dispositioning a case in the console moved half the screen. test_dispositions
-- now asserts all four views agree, because a duplicated CTE that drifted once
-- will drift again.
--
-- The grain is (rule_id, alert_id): one row per rule per case. Aggregating over
-- a window and dividing is contract/kpis.py's job, because that is where a
-- window is defined.
-- =====================================================================
DROP VIEW IF EXISTS v_kpi_rule_attribution;

CREATE VIEW v_kpi_rule_attribution AS
WITH verdict AS (
    SELECT alert_id,
           (array_agg(disposition ORDER BY decided_at DESC, outcome_id DESC))[1]
               AS disposition
      FROM case_outcomes
     GROUP BY alert_id
),
asserted AS (
    -- unnest, then DISTINCT: a rule claiming three signals on one case is one
    -- attribution, not three. Counting per signal would rank a rule by how many
    -- conditions it happens to have.
    SELECT DISTINCT s.alert_id, r AS rule_id
      FROM alert_signals s
      CROSS JOIN LATERAL unnest(
          CASE WHEN COALESCE(cardinality(s.asserted_by_rules), 0) > 0
               THEN s.asserted_by_rules
               ELSE ARRAY[s.source_rule_id]
          END) AS r
     WHERE r IS NOT NULL
)
SELECT a.rule_id,
       al.alert_id,
       COALESCE(al.first_event_at, al.created_at)      AS event_at,
       al.subject_type,
       al.score,
       al.band,
       v.disposition,
       (v.disposition = 'confirmed_fraud')             AS is_true_positive,
       (v.disposition IN ('false_positive', 'confirmed_legit')) AS is_false_positive,
       (d.action_source_rule = a.rule_id)              AS carried_the_action
  FROM asserted a
  JOIN alerts al    ON al.alert_id = a.alert_id
  JOIN decisions d  ON d.decision_id = al.decision_id
  LEFT JOIN verdict v ON v.alert_id = a.alert_id;
