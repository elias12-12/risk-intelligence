-- =====================================================================
-- verify_scores.sql  ·  The surviving half of the old score_and_verify.sql.
--
-- What it no longer does: score anything. Lines 12-97 of the original built a
-- second scorer in SQL — one that could not express AND semantics, the §5
-- degraded policy, veto precedence or prevention asymmetry without becoming a
-- program in the wrong language, and that had hardcoded subject ids in a file
-- whose header claimed "no per-pattern code". §3.1's argument applies to it
-- directly: two implementations of the same logic will diverge, and the
-- divergence is invisible.
--
-- What it still does, and why it is worth keeping: it is a human-readable,
-- demo-able proof that the numbers in the database are the numbers on the
-- console. READ-ONLY. No temp tables. No hardcoded ids — ring members come
-- from cluster_members, expectations from expectations below (mirrored in
-- fixtures/expected_scores.json, which the test suite reads).
--
--   docker exec -i glassbox_pg psql -U glassbox -d glassbox < db/acceptance/verify_scores.sql
-- =====================================================================
\pset footer off
\pset border 2

\echo '\n=== 1. Scores computed by the engine vs the signed-off console ==='
-- The expectations live in fixtures/, generated from expected_scores.json, so
-- that no file under db/ names a fixture. Run psql from the repository root.
\i fixtures/expected_scores.sql

\echo '\n=== 2. Rationale for the mule-ring alert (one row per driver) ==='
SELECT a.subject_id, sig.rank, sig.contribution, sig.direction, sig.human_text
  FROM alerts a
  JOIN alert_signals sig ON sig.alert_id = a.alert_id
 WHERE a.subject_type = 'network'
 ORDER BY a.subject_id, sig.rank;

\echo '\n=== 3. Accounts the ring alert covers — DERIVED from cluster_members ==='
SELECT s.subject_type, s.subject_id, s.role
  FROM alert_subjects s
  JOIN alerts a ON a.alert_id = s.alert_id
 WHERE a.subject_type = 'network'
 ORDER BY s.role, s.subject_id;

-- Not "the travel case" any more. Seed 0026 repriced its aggravator, the pool
-- stopped netting positive, and consolidation drops a pool the mitigators have
-- consumed — so TXN-48251 has no signals to show at all. What survives here are
-- the mitigators on cases that DID alert, which is the more useful demonstration
-- anyway: evidence against acting, sitting inside a case that was raised.
\echo '\n=== 4. Mitigating signals on cases that still reached the queue ==='
SELECT a.subject_id, sig.contribution, sig.human_text
  FROM alerts a
  JOIN alert_signals sig ON sig.alert_id = a.alert_id
 WHERE sig.direction = 'mitigating'
 ORDER BY a.subject_id, sig.contribution;

\echo '\n=== 5. The veto: a high score visibly held back by exonerating evidence ==='
SELECT d.subject_id, d.score, d.band,
       d.action_taken   AS took,
       r.action         AS rule_wanted,
       d.vetoed_by,
       d.prevent_threshold_met
  FROM decisions d
  LEFT JOIN rule_definitions r ON r.rule_id = d.action_source_rule
 WHERE d.vetoed_by IS NOT NULL AND d.action_source_rule IS NOT NULL
 ORDER BY d.score DESC;

\echo '\n=== 6. §12 invariants across EVERY alert (must be 0 failures) ==='
SELECT count(*) FILTER (WHERE NOT sum_ok)         AS score_bar_does_not_add_up,
       count(*) FILTER (WHERE NOT source_rule_ok) AS action_with_no_source_rule,
       count(*)                                   AS alerts_checked
  FROM v_alert_invariants;

\echo '\n=== 7. Evidence recorded as degraded (§5) ==='
SELECT subject_type, subject_id, score, action_taken, degraded_features
  FROM decisions
 WHERE degraded_features IS NOT NULL AND cardinality(degraded_features) > 0
 ORDER BY score DESC
 LIMIT 10;
