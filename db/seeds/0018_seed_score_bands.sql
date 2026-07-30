-- =====================================================================
-- 0018_seed_score_bands.sql  ·  Today's global cutoffs, as data.
--
-- §6 is explicit that 70/45 cannot survive consolidation: a subject scored by
-- three rules is not comparable to one scored by one, so a single global line
-- will drift. These rows preserve Week 1's behaviour exactly while making
-- Week 4's calibration an UPDATE rather than a code change.
-- =====================================================================
BEGIN;

INSERT INTO score_bands (subject_type, band, min_score, basis)
SELECT st.subject_type, b.band, b.min_score,
       'week-1 global cutoff, uncalibrated; per-subject-type calibration is Week 4'
  FROM ref_subject_type st
 CROSS JOIN (VALUES ('high', 70), ('elevated', 45), ('low', 0)) AS b(band, min_score)
ON CONFLICT (subject_type, band) DO NOTHING;

COMMIT;
