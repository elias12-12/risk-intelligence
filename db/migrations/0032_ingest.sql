-- =====================================================================
-- 0032_ingest.sql  ·  Week 5 — arrival. Rows that were not in the fixture.
--
-- Until now there was exactly one way for a transaction to exist: regenerate
-- the entire dataset and rebuild the database. That made every demo a REBUILD
-- rather than a CATCH — the engine detects fraud perfectly well, but only
-- fraud that was already in the file it was handed. Nothing could arrive.
--
-- Two things are recorded here, and both are of the Part II kind: cheap now,
-- impossible to reconstruct later.
--
-- ---------------------------------------------------------------------
-- 1 · PROVENANCE.  `source` on every relation that can now be written to at
--     runtime.
--
-- The moment a row can arrive by HTTP, the database stops being able to
-- distinguish "the system received this" from "somebody typed it during a
-- demo". For a project whose entire proposition is that its claims are
-- inspectable, that is not a small loss — and it is exactly the loss `0029`
-- closed for dispositions, where `case_outcomes.source` is what lets a KPI
-- tile say whether a verdict came from an analyst or from a script.
--
-- Same answer, same shape:
--
--     generated  the synthetic generator wrote it (every fixture row)
--     ingested   it arrived over the ingest API
--     authorized it arrived as an authorization request and the ENGINE
--                decided its auth_result
--
-- The DEFAULT is 'generated' so `fixtures/synthetic_data.sql` — which names
-- its columns explicitly and knows nothing about this migration — keeps
-- loading unchanged and lands on the honest value.
--
-- ---------------------------------------------------------------------
-- 2 · A WATERMARK, so a cycle can be incremental.
--
-- `IncrementalRunner.run_feature(as_of, since)` has been watermark-driven
-- since Week 2 and there has never been anywhere to KEEP the watermark:
-- `run_features.py` takes `--since` as an argument and a human supplies it.
-- That is fine for a batch rebuild and useless for a scheduler, which has to
-- answer "what has changed since I last looked" without being told.
--
-- One row per (relation, lane-ish name). Advanced only after the work that
-- consumed it committed, so a crashed tick re-reads the same window rather
-- than skipping it. At-least-once, deliberately: the feature runner is
-- append-only and re-running a window rewrites the same values with a later
-- computed_at, and §9's folding means a re-evaluated subject produces the same
-- alert count. Both halves of the pipeline are already idempotent, which is
-- what makes the cheap choice the correct one.
-- =====================================================================
BEGIN;

-- ---- 1. provenance -----------------------------------------------------
ALTER TABLE transactions  ADD COLUMN source TEXT NOT NULL DEFAULT 'generated';
ALTER TABLE events        ADD COLUMN source TEXT NOT NULL DEFAULT 'generated';
ALTER TABLE entity_links  ADD COLUMN source TEXT NOT NULL DEFAULT 'generated';
ALTER TABLE devices       ADD COLUMN source TEXT NOT NULL DEFAULT 'generated';

ALTER TABLE transactions
  ADD CONSTRAINT ck_txn_source
  CHECK (source IN ('generated', 'ingested', 'authorized'));
ALTER TABLE events
  ADD CONSTRAINT ck_event_source CHECK (source IN ('generated', 'ingested'));
ALTER TABLE entity_links
  ADD CONSTRAINT ck_link_source  CHECK (source IN ('generated', 'ingested'));
-- A device is OBSERVED rather than opened, so an authorization presenting an
-- unrecognised fingerprint registers it. That is the one dimension row the
-- ingest path may create, and 'authorized' is how you tell which ones it made.
ALTER TABLE devices
  ADD CONSTRAINT ck_device_source
  CHECK (source IN ('generated', 'ingested', 'authorized'));

COMMENT ON COLUMN transactions.source IS
  'Who wrote this row: the generator, the ingest API, or the authorization '
  'path (in which case auth_result was chosen by the engine, not by a caller).';

-- The authorization path decides `auth_result` itself, so the values it may
-- choose stop being a comment on 0003 and become a constraint. Left permissive
-- for NULL: 0003 allows it and the fixtures do not all set it.
ALTER TABLE transactions
  ADD CONSTRAINT ck_txn_auth_result
  CHECK (auth_result IS NULL OR auth_result IN ('approved', 'declined'));

-- ---- 2. the watermark --------------------------------------------------
CREATE TABLE ingest_watermark (
    stream       TEXT PRIMARY KEY,       -- 'features' | 'inline_sync' | 'async' | 'graph'
    watermark_at TIMESTAMPTZ,            -- NULL = never run; the next pass is a full one
    last_run_at  TIMESTAMPTZ,            -- wall clock, for observability only
    runs         BIGINT NOT NULL DEFAULT 0,
    note         TEXT
);

COMMENT ON TABLE ingest_watermark IS
  'How far each stage of the background cycle has consumed. watermark_at is '
  'EVENT time (occurred_at), never wall clock: the fixtures are pinned to '
  'GLASSBOX_NOW and a wall-clock watermark would place every ingested row '
  'seven months after the history it is supposed to be read against.';

INSERT INTO ingest_watermark (stream, note) VALUES
 ('features',    'IncrementalRunner.run_population(as_of, since)'),
 ('graph',       'graph.builder.build — only entity_links move it'),
 ('inline_sync', 'run_lane(inline_sync) for rows that did not arrive by /authorize'),
 ('async',       'run_lane(async) — the lane with an evaluation_lag');

-- Arrival order matters to an incremental pass and nothing indexed it: every
-- window predicate the runner writes is `occurred_at > since AND <= bound`.
CREATE INDEX ix_txn_occurred    ON transactions (occurred_at);
CREATE INDEX ix_events_occurred ON events (occurred_at);

COMMIT;
