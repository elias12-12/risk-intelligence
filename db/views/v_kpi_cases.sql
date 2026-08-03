-- =====================================================================
-- v_kpi_cases.sql  ·  One row per case, with its verdict and its clock.
--
-- Three tiles read this: validation outcomes, false-positive rate, and median
-- triage time.
--
-- ONE ROW PER ALERT, and that is load-bearing. `case_outcomes` has no uniqueness
-- constraint, so a second analyst adding a disposition would fan every case out
-- and deflate every rate computed over it — quietly, and in the direction that
-- flatters the system. v_condition_performance hit the same trap and solves it
-- the same way; this is that CTE, kept identical on purpose so the two views
-- cannot drift into disagreeing about what a case's verdict is.
--
-- THE TRIAGE CLOCK runs from `first_event_at` to `case_outcomes.decided_at`.
--
--   * not `alerts.created_at`, which is DEFAULT now() — wall clock. All seven
--     fixture alerts are created seconds apart, so a triage time measured on it
--     looks plausible and is wrong for every historical replay (0023, W3.6 #2).
--   * `case_outcomes.decided_at` is written explicitly by outcomes.py rather
--     than defaulted, which is the only reason this tile is computable at all.
--
-- On the shipped fixtures every case is settled by one synthetic pass, so the
-- triage times are a property of the generator's clock and not of any analyst.
-- kpis.py flags the tile accordingly — as of Week 5 by reading
-- `disposition_source` below rather than by asserting it in a string. A median
-- that describes nobody's working day is worse than no median if it is not
-- labelled.
--
-- WEEK 5: THE VERDICT IS THE LATEST DISPOSITION, NOT THE FIRST.
--
-- This was `ORDER BY decided_at, outcome_id` — first wins — which was right
-- while the only writer was a deterministic script that wrote each case exactly
-- once. With a human writer, a second disposition on a case means an analyst
-- corrected themselves, or corrected the settler, and first-wins would publish
-- the verdict most likely to be wrong while storing the right one. Dispositions
-- are append-only, so nothing is lost either way; this only chooses which row
-- speaks for the case.
--
-- THE CLOCK STILL RUNS TO THE FIRST DISPOSITION — `min(decided_at)`, unchanged.
-- "How long until this case was worked" and "what did we conclude" are
-- different questions, and a correction hours later does not mean triage took
-- hours longer. Mixed on purpose, and stated here so it does not read as an
-- oversight.
-- =====================================================================
DROP VIEW IF EXISTS v_kpi_cases;

CREATE VIEW v_kpi_cases AS
WITH verdict AS (
    SELECT alert_id,
           (array_agg(disposition ORDER BY decided_at DESC, outcome_id DESC))[1]
               AS disposition,
           (array_agg(source ORDER BY decided_at DESC, outcome_id DESC))[1]
               AS disposition_source,
           min(decided_at)                                             AS decided_at,
           count(*)                                                    AS dispositions
      FROM case_outcomes
     GROUP BY alert_id
)
SELECT a.alert_id,
       COALESCE(a.first_event_at, a.created_at)        AS event_at,
       a.subject_type,
       a.subject_id,
       a.score,
       a.band,
       a.status,
       a.dedup_key,
       a.triggering_events,
       a.exposure_amount,
       a.exposure_basis,
       d.action_taken,
       d.action_source_rule,
       d.execution_mode,
       v.disposition,
       -- 'analyst' or 'synthetic', from the row that carried the verdict. Every
       -- tile derived from a disposition reads this instead of asserting whose
       -- judgement it was (0029).
       v.disposition_source,
       v.decided_at,
       COALESCE(v.dispositions, 0)                     AS dispositions,
       CASE WHEN v.decided_at IS NULL THEN NULL
            ELSE EXTRACT(EPOCH FROM (v.decided_at - COALESCE(a.first_event_at,
                                                             a.created_at)))
       END                                             AS triage_seconds,
       -- A case an analyst closed as legitimate is the false positive §11 is
       -- asking about. `inconclusive` is neither, and folding it into either
       -- side is how a precision number becomes an opinion.
       (v.disposition = 'confirmed_fraud')             AS is_true_positive,
       (v.disposition IN ('false_positive', 'confirmed_legit')) AS is_false_positive
  FROM alerts a
  JOIN decisions d ON d.decision_id = a.decision_id
  LEFT JOIN verdict v ON v.alert_id = a.alert_id;
