-- =====================================================================
-- 0026_reprice_country_novelty.sql  ·  §10, the half that was still a
--                                       recommendation.
--
-- Week 3 built the report. This is the first time its finding is applied, and
-- it is the first time anything in this project has moved a signed-off score.
--
-- THE EVIDENCE, quoted from `python scripts/condition_report.py` over the
-- 9,923-decision population (cohort base rate 1.61%, 158/9,844 labelled fraud):
--
--   rule    feature                        dir  priced  fired  fire%  prec%  pts/pp
--   T-021   country_is_new_for_customer    agg      50    398   4.04   6.78     7.4
--   R-114   session_geo_jump_km            agg      18    317   3.22  10.73     1.7
--   R-114   mcc_is_new_for_customer        agg      14    802   8.15   8.60     1.6
--
-- pts/pp is |contribution| / precision_pct — what the catalog charges per unit
-- of measured precision, and high is bad. At +50 this condition charges 7.4,
-- which is 4.4x the next aggravator and 34x the best-sampled one.
--
-- WHY IT WAS +50, stated plainly because the reason for a price belongs next to
-- the price: 0010's own header admits it — "T-021's aggravator is sized (+50) so
-- the points sum to its displayed 31; the demo's low-risk example did not have
-- its points sum to its score." The number was reverse-engineered from a
-- screenshot, not derived from anything. A false-positive engine was sitting
-- inside the rule whose stated purpose is demonstrating false-positive
-- avoidance, which is exactly the finding §10 exists to produce.
--
-- THE NEW PRICE: +12.
--
--   6.78% precision x 1.7 pts/pp = 11.5, rounded to 12.
--
-- The anchor is `session_geo_jump_km`, deliberately, and NOT the top of the
-- pts/pp table. `device_first_seen_min` earns 0.2 — but on 36 firings
-- concentrated on the planted fixtures, where 97% precision is a property of
-- the fixture and not a measurement. `session_geo_jump_km` is the only other
-- aggravator that fires at population scale (317 firings, 3.22% of the
-- population, single-digit-to-low-teens precision), so it is the only like-for-
-- like comparison available. Pricing against an anecdote would be the same
-- uncalibrated confidence this file exists to correct.
--
-- WHAT THIS MOVES, in full — the blast radius is one number and it is a demo
-- narrative, not an invariant:
--
--   TXN-48251 (T-021 alone):  50 - 9 - 6 - 4 = 31   ->   12 - 9 - 6 - 4 = -7
--
-- A net-negative pool is a mitigator-only pool, and consolidation drops those
-- entirely: "safer than nothing" is not a claim an additive model can make (see
-- Week-2 defect 6). So TXN-48251 now scores 0 with an empty signal set, band
-- `low`, action `allow`, and still raises no alert. The story on screen is
-- unchanged in substance — mitigating evidence keeps it out of the queue — and
-- the score bar for that one case is now empty rather than showing a +50 nobody
-- could defend.
--
-- TXN-48300's 68 is UNAFFECTED: this condition does not fire there (the
-- customer's country is not new on that transaction; it is the card that is
-- being tested). The other three fixtures never touch T-021.
--
-- Applied as a new seed rather than an edit to 0010: the migration ledger is
-- append-only, and a price whose justification lives in a deleted diff is a
-- price nobody can explain later. That is the same audit argument §10 uses to
-- forbid an automatic write.
-- =====================================================================
BEGIN;

UPDATE rule_conditions
   SET contribution_points = 12,
       -- The template said "First transaction in Portugal", a fixture detail
       -- that was never true of the other 397 firings. A signal an analyst
       -- reads on a background transaction must describe that transaction.
       signal_template = 'First transaction in this country for this customer'
 WHERE rule_id = 'T-021'
   AND feature_key = 'country_is_new_for_customer';

COMMIT;
