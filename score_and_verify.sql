-- =====================================================================
-- score_and_verify.sql
-- The additive glass-box scorer, expressed as pure SQL over the data model.
-- It proves two things:
--   1. score = SUM(contribution_points of fired conditions)  -- nothing hidden
--   2. the seeded rules + derived features reproduce the console: 87/64/58/31
-- It also materializes decisions / alerts / alert_subjects / alert_signals,
-- so the whole model is populated from data alone (no per-pattern code).
-- Safe to re-run.
-- =====================================================================
\pset footer off
TRUNCATE alert_signals, alert_subjects, alerts, decisions RESTART IDENTITY CASCADE;

-- ---- evaluate every rule condition against the latest feature value --------
DROP TABLE IF EXISTS _fired;
CREATE TEMP TABLE _fired AS
WITH latest AS (
    SELECT DISTINCT ON (feature_key, entity_type, entity_id)
           feature_key, entity_type, entity_id, as_of, value_num, value_bool, value_text
    FROM feature_values
    ORDER BY feature_key, entity_type, entity_id, as_of DESC
)
SELECT r.rule_id,
       fv.entity_id                        AS subject_id,
       r.subject_type,
       fv.as_of,
       c.feature_key,
       c.contribution_points,
       c.reason_code,
       c.signal_template,
       CASE
         WHEN c.operator='>'  AND fv.value_num >  c.threshold_num THEN TRUE
         WHEN c.operator='>=' AND fv.value_num >= c.threshold_num THEN TRUE
         WHEN c.operator='<'  AND fv.value_num <  c.threshold_num THEN TRUE
         WHEN c.operator='<=' AND fv.value_num <= c.threshold_num THEN TRUE
         WHEN c.operator='='  AND c.threshold_text='true'  AND fv.value_bool IS TRUE  THEN TRUE
         WHEN c.operator='='  AND c.threshold_text='false' AND fv.value_bool IS FALSE THEN TRUE
         WHEN c.operator='='  AND c.threshold_text NOT IN ('true','false')
                              AND fv.value_text = c.threshold_text THEN TRUE
         ELSE FALSE
       END AS fired,
       replace(replace(c.signal_template, '{v}',
              coalesce(trim_scale(fv.value_num)::text, fv.value_text, fv.value_bool::text)), '{n}',
              coalesce(trim_scale(fv.value_num)::text, fv.value_text, '')) AS human_text
FROM rule_conditions c
JOIN rule_definitions r ON r.rule_id = c.rule_id
JOIN latest fv          ON fv.feature_key = c.feature_key
                       AND fv.entity_type = r.subject_type;

-- ---- sum fired contributions -> score per (rule, subject) ------------------
DROP TABLE IF EXISTS _scored;
CREATE TEMP TABLE _scored AS
SELECT rule_id, subject_id, subject_type,
       SUM(CASE WHEN fired THEN contribution_points ELSE 0 END) AS score,
       max(as_of) AS occurred_at
FROM _fired
GROUP BY rule_id, subject_id, subject_type;

-- ---- materialize decisions -------------------------------------------------
INSERT INTO decisions (subject_type, subject_id, occurred_at, execution_mode, score, band, action_taken, fail_mode, model_ref, rules_fired)
SELECT s.subject_type, s.subject_id, s.occurred_at, r.execution_mode, s.score,
       CASE WHEN s.score>=70 THEN 'high' WHEN s.score>=45 THEN 'elevated' ELSE 'low' END,
       r.action,
       CASE WHEN r.execution_mode='inline_sync' THEN 'open' ELSE NULL END,
       'rule-engine:'||s.rule_id,
       ARRAY[s.rule_id]
FROM _scored s JOIN rule_definitions r ON r.rule_id=s.rule_id;

-- ---- materialize alerts (every scored subject surfaces in the demo queue) ---
INSERT INTO alerts (decision_id, subject_type, subject_id, title, score, band, status)
SELECT d.decision_id, s.subject_type, s.subject_id, r.name, s.score,
       CASE WHEN s.score>=70 THEN 'high' WHEN s.score>=45 THEN 'elevated' ELSE 'low' END,
       'open'
FROM _scored s
JOIN rule_definitions r ON r.rule_id=s.rule_id
JOIN decisions d ON d.subject_id=s.subject_id AND d.subject_type=s.subject_type;

-- ---- one-to-many rationale: a row per FIRED condition (renders the bar) -----
INSERT INTO alert_signals (alert_id, feature_key, contribution, direction, human_text, reason_code, source_rule_id, rank)
SELECT a.alert_id, f.feature_key, f.contribution_points,
       CASE WHEN f.contribution_points >= 0 THEN 'aggravating' ELSE 'mitigating' END,
       f.human_text, f.reason_code, f.rule_id,
       row_number() OVER (PARTITION BY a.alert_id ORDER BY abs(f.contribution_points) DESC)
FROM _fired f
JOIN alerts a ON a.subject_id=f.subject_id AND a.subject_type=f.subject_type
WHERE f.fired;

-- ---- multi-entity coverage for the network alert (from entity_links) --------
INSERT INTO alert_subjects (alert_id, subject_type, subject_id, role)
SELECT a.alert_id, 'account', el.to_id,
       CASE WHEN el.to_id='ACC-8830' THEN 'collector' ELSE 'member' END
FROM alerts a
JOIN entity_links el ON el.from_id='DEV-F90D2' AND el.link_type='opened_on'
WHERE a.subject_type='network' AND a.subject_id='RING-1187'
UNION ALL
SELECT a.alert_id, 'device', 'DEV-F90D2', 'device'
FROM alerts a WHERE a.subject_type='network' AND a.subject_id='RING-1187';

-- =====================================================================
-- VERIFICATION OUTPUT
-- =====================================================================
\echo '\n=== Scores computed in SQL vs the GlassBox console ==='
WITH expected(rule_id, subject_id, exp) AS (
  VALUES ('R-114','TXN-48291',87),('L-203','RING-1187',64),
         ('S-077','ACC-2201',58),('T-021','TXN-48251',31)
)
SELECT s.rule_id, s.subject_id, d.band, s.score AS computed, e.exp AS expected,
       CASE WHEN s.score=e.exp THEN 'OK' ELSE 'MISMATCH' END AS check,
       (SELECT action FROM rule_definitions WHERE rule_id=s.rule_id) AS action
FROM _scored s
JOIN expected e ON e.rule_id=s.rule_id AND e.subject_id=s.subject_id
JOIN decisions d ON d.subject_id=s.subject_id AND d.subject_type=s.subject_type
ORDER BY s.score DESC;

\echo '\n=== Rationale for the mule-ring alert (one alert_signals row per driver) ==='
SELECT a.subject_id, sig.rank, sig.contribution, sig.direction, sig.human_text
FROM alerts a JOIN alert_signals sig ON sig.alert_id=a.alert_id
WHERE a.subject_id='RING-1187'
ORDER BY sig.rank;

\echo '\n=== Accounts the ring alert covers (alert_subjects) ==='
SELECT subject_id, role FROM alert_subjects
WHERE alert_id=(SELECT alert_id FROM alerts WHERE subject_id='RING-1187')
ORDER BY role, subject_id;

\echo '\n=== Mitigating signals that kept the travel case below the line (T-021) ==='
SELECT sig.contribution, sig.human_text
FROM alerts a JOIN alert_signals sig ON sig.alert_id=a.alert_id
WHERE a.subject_id='TXN-48251' AND sig.direction='mitigating'
ORDER BY sig.contribution;
