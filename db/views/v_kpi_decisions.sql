-- =====================================================================
-- v_kpi_decisions.sql  ·  §11's substrate for everything counted per decision.
--
-- Four of the nine tiles read this one: alert volume, score distribution,
-- false-negative rate, and the fail-open rate.
--
-- It is deliberately NOT aggregated. Every KPI in §11 has to name its window and
-- compare against the preceding equal-length one, so the aggregation cannot live
-- here — a view takes no parameters. What lives here is the CLASSIFICATION: what
-- counts as a case, what counts as ground truth, what counts as degraded. The
-- windowing is one parameterised query in contract/kpis.py, which is the only
-- place a window is ever defined.
--
-- THE TIME COLUMN IS `occurred_at`, NEVER `decided_at` OR `now()`. decided_at is
-- when the engine ran, which on a replay of January is today; a tile windowed on
-- it would report every historical decision as belonging to the present. Same
-- argument as alerts.created_at in migration 0023, and the same trap.
--
-- `became_case` is the alert-volume numerator and `alert_routing` is its
-- denominator's decomposition. Before 0023 the other 9,916 decisions were
-- silent and "alert volume" was a numerator with nothing under it — a count of
-- evaluation cycles that rose when nothing changed.
--
-- `is_fraud` is SYNTHETIC GROUND TRUTH and the column name should not let anyone
-- forget it. In production, recall is not measurable: you only get labels for
-- what you alerted on, and the only fix is a random-sample audit of unalerted
-- traffic. Here we know the answer because we planted it, so the false-negative
-- rate is exact — and meaningless outside this dataset. Both halves belong on
-- the tile, which is why `kpis.v1` carries a `synthetic` flag on the wire.
-- =====================================================================
DROP VIEW IF EXISTS v_kpi_decisions;

CREATE VIEW v_kpi_decisions AS
SELECT d.decision_id,
       d.occurred_at                                   AS event_at,
       d.subject_type,
       d.subject_id,
       d.execution_mode,
       d.score,
       d.band,
       d.action_taken,
       d.alert_routing,
       d.alert_id,
       -- A case is raised or restated. A FOLDED evaluation is the same case
       -- seen again — counting it would put alert volume back to counting
       -- cycles, which is exactly what §9 was built to stop.
       (d.alert_routing IN ('raised', 'restated'))     AS became_case,
       (d.alert_routing = 'suppressed')                AS was_suppressed,
       -- Populated with the LANE'S POLICY, not an observed failure: nothing has
       -- failed here because nothing real has run. A tile reading this as a
       -- measured fail-open rate would be asserting a resilience result the
       -- system has not earned, so kpis.py publishes it with that caveat.
       d.fail_mode,
       (COALESCE(cardinality(d.degraded_features), 0) > 0) AS degraded,
       t.synthetic_label = 'fraud'                     AS is_fraud,
       (t.synthetic_label IS NOT NULL)                 AS is_labelled
  FROM decisions d
  LEFT JOIN transactions t ON t.txn_id = d.trigger_id;
