-- =====================================================================
-- 0023_execution_hygiene_observability.sql  ·  Week 3 stored shapes.
--
-- Three Week-2 successes each left a hole this migration fills.
--
--   §9  The engine computes and stores dedup_key and nothing folds on it, so
--       re-running a lane creates a duplicate alert every time and "alert
--       volume" counts evaluation cycles. Folding needs an open-window clock;
--       the KPI needs a denominator saying what happened to the other 9,916
--       decisions that did not become an alert.
--
--   §8  action_executions is a table nothing writes. Block rate counts
--       intentions, and a challenge the customer passed is indistinguishable
--       from one they abandoned.
--
--   §10 alert_signals holds the POST-consolidation survivors of the ALERTED
--       subjects — 7 alerts, 29 signals. Nothing anywhere records which
--       conditions fired across the population, so "find the mispriced
--       conditions" has no source at all.
--
-- All additive. No Week-1 or Week-2 column changes meaning.
-- =====================================================================
BEGIN;

-- ---------------------------------------------------------------------
-- §9 · What happened to every decision on the way to a queue.
--
-- This is the alert-volume KPI's denominator. 9,923 decisions and 7 alerts:
-- without this column the other 9,916 are silent, and "alert volume" is a
-- numerator with nothing under it. It also makes suppression RECORDED rather
-- than silent, which §9 requires in as many words.
--
-- The FK is circular with alerts.decision_id (NOT NULL since 0012). That is
-- fine and deliberate: the engine inserts the decision, inserts the alert, then
-- UPDATEs the decision inside one transaction. ON DELETE SET NULL so retiring
-- an alert is never blocked by the decisions that pointed at it.
-- ---------------------------------------------------------------------
ALTER TABLE decisions
    ADD COLUMN alert_id      BIGINT REFERENCES alerts ON DELETE SET NULL,
    ADD COLUMN alert_routing TEXT;

UPDATE decisions d
   SET alert_id = a.alert_id, alert_routing = 'raised'
  FROM alerts a
 WHERE a.decision_id = d.decision_id;

UPDATE decisions SET alert_routing = 'no_authority' WHERE alert_routing IS NULL;

ALTER TABLE decisions
    ALTER COLUMN alert_routing SET NOT NULL,
    ADD CONSTRAINT ck_decisions_alert_routing CHECK (alert_routing IN
        ('raised','folded','restated','suppressed','no_authority'));

-- Deliberately NOT a CHECK on (alert_routing = 'no_authority') = (alert_id IS
-- NULL). Routing is only knowable AFTER the alert step, the engine inserts a
-- provisional value and corrects it in the same transaction, and PostgreSQL
-- cannot defer a CHECK. That invariant lives in db/views/v_decision_routing.sql
-- instead — the same three-layer pattern as v_alert_invariants.
CREATE INDEX ix_decisions_routing ON decisions (alert_routing, subject_type);

COMMENT ON COLUMN decisions.alert_routing IS
 'raised | folded | restated | suppressed | no_authority. Every evaluation says '
 'whether it reached a queue and, if not, why. Without it alert volume has no '
 'denominator and §9 suppression is invisible.';

-- ---------------------------------------------------------------------
-- §9 · Fold state, on the EVENT clock.
--
-- alerts.created_at is DEFAULT now() — wall clock. All seven fixture alerts are
-- created seconds apart, so an open_window or a recency factor measured on it is
-- a no-op that looks like it works, and it is plainly wrong under
-- `run_cycle --as-of <historical>`, where a replay of January must fold the way
-- January did. These two columns carry decisions.occurred_at.
-- ---------------------------------------------------------------------
ALTER TABLE alerts
    ADD COLUMN first_event_at  TIMESTAMPTZ,
    ADD COLUMN last_event_at   TIMESTAMPTZ,
    ADD COLUMN exposure_amount NUMERIC,
    ADD COLUMN exposure_basis  TEXT;

UPDATE alerts a
   SET first_event_at = d.occurred_at, last_event_at = d.occurred_at
  FROM decisions d
 WHERE d.decision_id = a.decision_id;

CREATE INDEX ix_alerts_dedup_open ON alerts (dedup_key, status, last_event_at DESC);

COMMENT ON COLUMN alerts.last_event_at IS
 'occurred_at of the newest evaluation folded onto this alert. The fold window '
 'and the queue recency factor are both measured on this, never on created_at, '
 'which is wall clock and makes a historical replay fold against today.';

COMMENT ON COLUMN alerts.exposure_basis IS
 'How exposure_amount was derived: trigger_txn_amount_base | account_net_flow_90d '
 '| cluster_inbound_7d. There is no accounts.available_balance (see 0015), so '
 'account exposure is DERIVED and has to say so — a money number that reorders '
 'an analyst queue must explain itself like everything else here does.';

-- ---------------------------------------------------------------------
-- §8 · Executions that can be read back.
--
-- subject_type/subject_id are COPIED from the decision so the executions read
-- surface and the §10 report join to a subject without a decisions hop.
--
-- They are NOT a feature key. For an R-114 decision subject_id holds a txn_id,
-- so "failed step-ups on this card in 30 days" is not expressible over this
-- relation; that feature reads `events` instead (0025). action_executions is
-- deliberately NOT added to predicate.ALLOWED_RELATIONS.
-- ---------------------------------------------------------------------
ALTER TABLE action_executions
    ADD COLUMN subject_type TEXT REFERENCES ref_subject_type,
    ADD COLUMN subject_id   TEXT,
    ADD COLUMN alert_id     BIGINT REFERENCES alerts ON DELETE SET NULL,
    ADD COLUMN synthetic    BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX ix_action_exec_subject
    ON action_executions (subject_type, subject_id, issued_at DESC);

COMMENT ON COLUMN action_executions.synthetic IS
 'TRUE when the outcome was settled by scripts/resolve_actions.py against '
 'transactions.synthetic_label rather than by a real customer or analyst. '
 'Published on executions.v1 so no surface can present a synthetic challenge '
 'pass rate as a measured one. Fixture artifact: must not exist in production.';

-- ---------------------------------------------------------------------
-- §10 · The pre-consolidation condition ledger.
--
-- The ONLY possible source for the condition-level report §10 asks for.
-- alert_signals cannot serve it twice over: it holds only ALERTED subjects, and
-- only the signals that SURVIVED consolidation — and what dedup discards is
-- exactly the per-condition attribution the report needs.
--
-- One row per condition of every rule applicable to the subject, fired or not:
-- ~79,000 rows per full cycle (9,844 transactions x 8 conditions + 78 accounts
-- x 4 + 1 network x 4).
--
-- SUM(contributed) PER DECISION DOES NOT EQUAL decisions.score, and a test
-- asserting that it does is wrong. Three independent reasons:
--   1. scoring.score_rule gates the whole rule on `satisfied` — an unsatisfied
--      rule's fired conditions score zero (this is TXN-48251);
--   2. consolidate dedups (feature_key, direction) keeping max(abs(points)),
--      so a signal two rules both claim is counted once (this is TXN-48300);
--   3. consolidate drops mitigator-only pools entirely, because "safer than
--      nothing" is not a claim an additive model can make.
-- The ledger is the evidence BEFORE those three policies apply. That is its
-- entire value; both columns are kept so the report can show the difference.
-- ---------------------------------------------------------------------
CREATE TABLE decision_conditions (
    decision_id       BIGINT  NOT NULL REFERENCES decisions ON DELETE CASCADE,
    condition_id      BIGINT  NOT NULL REFERENCES rule_conditions ON DELETE CASCADE,
    rule_id           TEXT    NOT NULL REFERENCES rule_definitions,
    feature_key       TEXT    NOT NULL REFERENCES feature_catalog,
    read_status       TEXT    NOT NULL,
    fired             BOOLEAN NOT NULL,
    rule_satisfied    BOOLEAN NOT NULL,
    priced_points     NUMERIC NOT NULL,   -- what the catalog charges
    contributed       NUMERIC NOT NULL,   -- what this decision actually took
    entity_type       TEXT,
    entity_ids        TEXT[],
    feature_value     JSONB,
    value_as_of       TIMESTAMPTZ,
    value_computed_at TIMESTAMPTZ,
    spec_version      INT,
    PRIMARY KEY (decision_id, condition_id),
    -- 'fanout_error' is a real status (types.FeatureStatus, produced in pit.py).
    -- Omitting it would turn a legitimate read into a constraint violation.
    CONSTRAINT ck_dc_read_status CHECK (read_status IN
        ('present','absent','stale','unresolvable','fanout_error')),
    CONSTRAINT ck_dc_fired_is_present CHECK (NOT fired OR read_status = 'present'),
    CONSTRAINT ck_dc_contributed CHECK (
        contributed = CASE WHEN fired AND rule_satisfied THEN priced_points ELSE 0 END)
);

CREATE INDEX ix_dc_condition ON decision_conditions (condition_id, fired);
CREATE INDEX ix_dc_feature   ON decision_conditions (feature_key, fired);

-- ---------------------------------------------------------------------
-- §9 · Hygiene policy as rows, keyed like score_bands.
--
-- Keyed on subject_type for the same reason score_bands is: a ring re-evaluated
-- every 15 minutes needs a different open window from a card authorisation, and
-- Week 4 recalibration should be an UPDATE rather than a code change.
--
-- There is no fold_across_lanes column. The seeded rules partition subject
-- types by lane (transaction -> inline_sync, account/network -> async), so no
-- subject is ever evaluated in two lanes and the flag would have no reachable
-- behaviour. Add it when a rule set first spans lanes, not before.
-- ---------------------------------------------------------------------
CREATE TABLE alert_policy (
    subject_type        TEXT PRIMARY KEY REFERENCES ref_subject_type,
    open_window         INTERVAL NOT NULL,
    suppress_while_open BOOLEAN  NOT NULL DEFAULT TRUE,
    priority_half_life  INTERVAL NOT NULL,
    exposure_weight     NUMERIC  NOT NULL,
    exposure_floor      NUMERIC  NOT NULL,
    calibrated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    basis               TEXT
);

COMMENT ON TABLE alert_policy IS
 'Per-subject-type dedup window and queue-priority weights. exposure_floor '
 'exists because amount_base spans orders of magnitude: undamped, an 87 with '
 '$0.50 at risk sorts below a 31 with $40,000.';

-- action is NOT an FK to ref_action, for the reason 0013 gives at length:
-- `notify` is a channel-level execution, not a rung on the severity ladder, and
-- putting it on the ladder would tie with `alert` at severity 2 and make §7.2's
-- maximum-severity resolution non-deterministic.
CREATE TABLE action_routing (
    action  TEXT NOT NULL,
    band    TEXT NOT NULL,
    channel TEXT NOT NULL,
    basis   TEXT,
    PRIMARY KEY (action, band),
    CONSTRAINT ck_ar_action  CHECK (action IN ('challenge','hold','block','notify')),
    CONSTRAINT ck_ar_channel CHECK (channel IN ('sms_otp','app_push','phone','queue'))
);

COMMIT;
