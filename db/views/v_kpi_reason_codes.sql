-- =====================================================================
-- v_kpi_reason_codes.sql  ·  The emerging-trends tile, and its baseline.
--
-- §11 asks for "reason-code time series" and then makes a demand of it that the
-- console's existing tiles do not meet: "the current tiles show -41% and -28%
-- against nothing. Define the comparison as the preceding equal-length window
-- within the dataset, or render the tile without a delta. A delta with no
-- baseline is the one kind of KPI that is worse than no KPI."
--
-- So this view emits one row per (case, reason code) with the case's EVENT time,
-- and contract/kpis.py takes the window and the window immediately before it.
-- The baseline is not a parameter anyone can pick to flatter a number: it is
-- always the preceding window of the same length, and when the dataset does not
-- reach back that far the delta is NULL and the tile renders without one.
--
-- One row per (alert, reason_code) rather than per signal: three signals citing
-- GEO_ANOMALY on one case is one case with a geographic story, and counting
-- three would make the trend a function of how many conditions a rule happens to
-- carry rather than of what is happening in the traffic.
-- =====================================================================
DROP VIEW IF EXISTS v_kpi_reason_codes;

CREATE VIEW v_kpi_reason_codes AS
SELECT DISTINCT
       s.reason_code,
       a.alert_id,
       COALESCE(a.first_event_at, a.created_at)  AS event_at,
       a.subject_type,
       a.band,
       s.direction
  FROM alert_signals s
  JOIN alerts a ON a.alert_id = s.alert_id
 WHERE s.reason_code IS NOT NULL;
