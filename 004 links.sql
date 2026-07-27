-- =====================================================================
-- 0004_links.sql  ·  Layer 3 — link / edge layer
-- Lets the system KNOW a set of entities forms one cluster before it can
-- score the cluster. Promoted to first-class by the mule-ring case.
-- =====================================================================
BEGIN;

CREATE TABLE entity_links (
    link_id    BIGSERIAL PRIMARY KEY,
    from_type  TEXT REFERENCES ref_subject_type,
    from_id    TEXT NOT NULL,
    to_type    TEXT REFERENCES ref_subject_type,
    to_id      TEXT NOT NULL,
    link_type  TEXT NOT NULL,                   -- shares_device|shares_email|opened_on|transfer_to|same_customer
    first_seen TIMESTAMPTZ,
    last_seen  TIMESTAMPTZ,
    weight     NUMERIC,
    attributes JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX ix_links_from ON entity_links (from_type, from_id);
CREATE INDEX ix_links_to   ON entity_links (to_type,   to_id);

COMMIT;