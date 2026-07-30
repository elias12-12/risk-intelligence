-- =====================================================================
-- 0027_calibrate_score_bands.sql  ·  §10's other half, applied by hand.
--
-- `0018` seeded 70/45/0 across all seven subject types and said so in its own
-- `basis` column: "week-1 global cutoff, uncalibrated; per-subject-type
-- calibration is Week 4". This is Week 4, and the output of
-- `python scripts/calibrate_bands.py` is below, transcribed rather than
-- summarised.
--
-- THE DISTRIBUTION (9,923 decisions, after 0026):
--
--   transaction   9,844 decisions,  402 scoring above zero (4.1%)
--                 observed: 2x153  3x5  8x217  12x22  68x1  81x1  87x3
--   account          78 decisions,    1 scoring above zero
--   network           1 decision,     1 scoring above zero
--
-- METHOD — maximum gap, not percentile, and the difference matters here.
--
-- The obvious rule (high at p99, elevated at p95) is wrong on this data and
-- wrong in a way that would have looked reasonable in a summary table. The
-- transaction distribution is bimodal with a 56-point empty region in the
-- middle. p95 of the scoring subjects is 12, which would put every transaction
-- where a single condition fired into `elevated`; p99 is 68, which would promote
-- the veto fixture out of the band it was signed off in. A percentile follows
-- the density, and the density here is entirely in one mode.
--
-- A cutoff's job is to separate populations. These populations are already
-- separated by an empty region, so the cutoff belongs inside it, where every
-- available value produces an identical partition — which is what makes the
-- choice robust rather than tuned:
--
--   gaps:  12 -> 68 (56 wide)   68 -> 81 (13)   81 -> 87 (6)
--   cuts:  elevated at the midpoint of the widest, 40
--          high     at the midpoint of the second, 75
--
-- WHAT THIS CHANGES, stated plainly: nothing, on this dataset. Under 70/45 the
-- partition is {81, 87x3} high, {68} elevated; under 75/40 it is the same. The
-- lines moved AWAY from the observed data — the nearest score to `high` is 6
-- points away instead of 2, and the nearest to `elevated` is 28 instead of 33.
-- This is a defensive recalibration, not a corrective one, and claiming
-- otherwise would be the kind of overstatement §16 warns about.
--
-- account and network are NOT calibrated, deliberately. One scoring subject each
-- is not a distribution, and a cutoff derived from n=1 is n=1 wearing a
-- calibration's clothes. They keep the inherited cutoff and their `basis` says
-- which one and why. The other four subject types have no decisions at all.
--
-- WHAT MAXIMUM-GAP CAN AND CANNOT SUPPORT, since `basis` is read by a human who
-- did not run the script: it supports "no observed subject sits near this line."
-- It does not support any claim that these bands encode a risk appetite. That
-- needs dispositions at volume, and §8's denominators here are single digits.
-- =====================================================================
BEGIN;

UPDATE score_bands SET
    min_score     = 75,
    calibrated_at = now(),
    basis         = 'maximum-gap over 402 scoring transaction decisions '
                    '(2x153 3x5 8x217 12x22 68x1 81x1 87x3); midpoint of the '
                    '68->81 gap. Separates R-114 full-burst cases from the '
                    'vetoed 68. Not a risk appetite: see 0027 header.'
 WHERE subject_type = 'transaction' AND band = 'high';

UPDATE score_bands SET
    min_score     = 40,
    calibrated_at = now(),
    basis         = 'maximum-gap over the same 402; midpoint of the 12->68 gap, '
                    'the widest empty region in the distribution. p95 would have '
                    'been 12 and banded every single-condition firing elevated.'
 WHERE subject_type = 'transaction' AND band = 'elevated';

-- `low` is the floor, not a cutoff: a score of 0 has to land somewhere. Its
-- basis is corrected only so that no row in this table still carries 0018's
-- "calibration is Week 4" text after Week 4.
UPDATE score_bands SET
    calibrated_at = now(),
    basis         = 'floor, not a cutoff — every score >= 0 has to band '
                    'somewhere. Nothing to calibrate.'
 WHERE band = 'low';

-- Not calibrated, and recorded as NOT calibrated. A silently inherited number is
-- how an uncalibrated cutoff gets mistaken for a calibrated one six months
-- later, and this table is read by engine/bands.py on every decision.
--
-- The counts are LITERALS, deliberately. Deriving them from `decisions` would
-- read zero on a fresh build — seeds run before any cycle has produced a
-- decision — and the basis would then claim there was no population when the
-- truth is that there was not one YET. A basis records what was observed at
-- calibration time; it is not a live query.
UPDATE score_bands SET
    calibrated_at = now(),
    basis         = 'UNCALIBRATED — inherited 0018 global cutoff. 1 scoring '
                    'subject on the Week-4 dataset (ACC-2201 at 58). n=1 is not '
                    'a distribution. Recalibrate when this subject type has a '
                    'population.'
 WHERE subject_type = 'account' AND band IN ('high', 'elevated');

UPDATE score_bands SET
    calibrated_at = now(),
    basis         = 'UNCALIBRATED — inherited 0018 global cutoff. 1 scoring '
                    'subject on the Week-4 dataset (RING-1187 at 64), which is '
                    'also the only network subject that exists. Recalibrate when '
                    'the graph builder produces a population.'
 WHERE subject_type = 'network' AND band IN ('high', 'elevated');

UPDATE score_bands SET
    calibrated_at = now(),
    basis         = 'UNCALIBRATED — inherited 0018 global cutoff. No rule names '
                    'this subject type, so it has no decisions at all on the '
                    'Week-4 dataset. The row exists because score_bands is keyed '
                    'on ref_subject_type; it has never been read.'
 WHERE subject_type IN ('card', 'customer', 'device', 'merchant')
   AND band IN ('high', 'elevated');

COMMIT;
