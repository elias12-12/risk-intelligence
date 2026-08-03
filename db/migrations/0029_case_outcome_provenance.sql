-- =====================================================================
-- 0029_case_outcome_provenance.sql  ·  Week 5 — who decided this case.
--
-- `action_executions` carries a `synthetic` boolean, and `executions.v1` puts it
-- on the wire, so no surface can present a settled challenge as a measured one.
-- `case_outcomes` never got the same treatment: the settler distinguishes itself
-- by writing the literal `analyst_id = 'synthetic-analyst'`, which is a naming
-- convention rather than a column, and nothing can be JOINed against it.
--
-- That was harmless while the only writer was a script. Week 5 adds a human
-- writer, and two things break the moment one exists:
--
--   * `contract/kpis.py` HARDCODES "On this dataset every disposition was
--     written by scripts/resolve_actions.py, not by an analyst" as the caveat on
--     the validation-outcomes tile, and a near-identical sentence on median
--     triage time. The first real disposition makes both tiles assert something
--     false — in a payload whose stated contract is that it never asserts a
--     capability the system does not have.
--   * The review queue cannot tell a case a person has worked from a case a
--     fixture script closed, so it cannot decide what to show.
--
-- One column fixes both, and it has to exist BEFORE the first human write:
-- retrofitting provenance means guessing at rows already stored, which is the
-- same argument Part II makes for every other record-now column.
--
-- DEFAULT 'analyst' is deliberate. The synthetic path names itself explicitly
-- (engine/outcomes.py), so the default belongs to the human path — a future
-- writer that forgets to say what it is gets recorded as a person, which is the
-- claim that invites scrutiny rather than the one that deflects it.
-- =====================================================================
BEGIN;

ALTER TABLE case_outcomes
    ADD COLUMN source TEXT NOT NULL DEFAULT 'analyst'
        CHECK (source IN ('analyst', 'synthetic'));

-- Every row that exists at this point was written by engine/outcomes.py, which
-- is the only writer there has ever been.
UPDATE case_outcomes SET source = 'synthetic' WHERE analyst_id = 'synthetic-analyst';

-- The vocabulary was a COMMENT in 0008 and nothing enforced it, which was safe
-- only because the single writer was a script with four literals in it. A
-- disposition arriving over HTTP is checked by a Pydantic Literal; this is the
-- second layer, on the same argument §1 makes for enforcing the sum invariant in
-- three places rather than one. v_kpi_cases classifies on these exact four
-- strings, so a fifth would not raise — it would quietly land in neither
-- is_true_positive nor is_false_positive and deflate every rate over it.
ALTER TABLE case_outcomes
    ADD CONSTRAINT case_outcomes_disposition_check
        CHECK (disposition IN ('confirmed_fraud', 'false_positive',
                               'confirmed_legit', 'inconclusive'));

COMMENT ON COLUMN case_outcomes.source IS
 'Who decided: analyst (a person, through the API) or synthetic (settled by '
 'engine/outcomes.py against transactions.synthetic_label). Read by '
 'v_kpi_cases, which carries it onto every tile derived from a disposition, and '
 'by contract/queue.py, which keeps a synthetically-closed case in the queue '
 'because no person has worked it.';

COMMIT;
