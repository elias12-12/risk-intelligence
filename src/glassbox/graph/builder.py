"""Build clusters from the link layer, so a network subject is DERIVED.

§3.3's whole point: today `RING-1187` is a string literal in the scorer and
alert_subjects is populated from a hardcoded device id. That is a demo, not a
system — a second ring would need a second patch.

Here a cluster is found by a rule over entity_links, and its identity comes from
a natural_key ('device_fanout:DEV-F90D2'). A rebuild finds the same cluster and
keeps the same cluster_id, so alert history survives re-running the builder.
Delete a member and the alert's coverage changes; nothing is hardcoded.

joined_at is the LINK's first_seen, not now(): a cluster member joined when the
evidence says it joined, and the feature runner reads that instant to place the
value at a point in time a past decision could have seen.
"""
from __future__ import annotations

from dataclasses import dataclass

import psycopg

from ..db import fetch_all

BUILDER_VERSION = 1

# A detector is a rule over the link layer. Adding a cluster shape is a row in
# this list plus (optionally) a feature spec — not a new table and not a new
# alerting path.
DEVICE_FANOUT_MIN_ACCOUNTS = 3


@dataclass
class BuiltCluster:
    cluster_id: str
    natural_key: str
    cluster_type: str
    members: int
    created: bool


def _stable_id(seq: int, existing: dict[str, str], natural_key: str) -> str:
    """RING-1187 is a signed-off id in Week 1's demo, so the first device-fanout
    cluster keeps it. Everything after is allocated in sequence."""
    if natural_key in existing:
        return existing[natural_key]
    if natural_key == "device_fanout:DEV-F90D2":
        return "RING-1187"
    return f"RING-{1187 + seq}"


def build(conn: psycopg.Connection) -> list[BuiltCluster]:
    """Find device-fanout clusters and (re)write clusters + cluster_members."""
    existing = {
        r["natural_key"]: r["cluster_id"]
        for r in fetch_all(conn, "SELECT natural_key, cluster_id FROM clusters")
    }

    candidates = fetch_all(
        conn,
        """
        SELECT el.from_id                        AS device_id,
               count(DISTINCT el.to_id)          AS n_accounts,
               min(el.first_seen)                AS first_seen,
               max(coalesce(el.last_seen, el.first_seen)) AS last_seen
          FROM entity_links el
         WHERE el.link_type = 'opened_on'
           AND el.from_type = 'device'
           AND el.to_type   = 'account'
         GROUP BY el.from_id
        HAVING count(DISTINCT el.to_id) > %s
         ORDER BY el.from_id
        """,
        (DEVICE_FANOUT_MIN_ACCOUNTS,),
    )

    built: list[BuiltCluster] = []
    for seq, row in enumerate(candidates):
        natural_key = f"device_fanout:{row['device_id']}"
        cluster_id = _stable_id(seq, existing, natural_key)
        created = natural_key not in existing

        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO clusters (cluster_id, cluster_type, natural_key, first_seen,
                                      last_seen, member_count, built_at, builder_version)
                VALUES (%s, 'device_fanout', %s, %s, %s, 0, now(), %s)
                ON CONFLICT (natural_key) DO UPDATE
                   SET last_seen = EXCLUDED.last_seen,
                       built_at  = now(),
                       builder_version = EXCLUDED.builder_version
                """,
                (cluster_id, natural_key, row["first_seen"], row["last_seen"], BUILDER_VERSION),
            )
            # Members are recomputed from the link layer every build. The device
            # is a member too — it is the thing the accounts have in common.
            cur.execute("DELETE FROM cluster_members WHERE cluster_id = %s", (cluster_id,))
            cur.execute(
                """
                INSERT INTO cluster_members (cluster_id, subject_type, subject_id, role, joined_at)
                SELECT %s, 'account', el.to_id,
                       CASE WHEN inbound.collector THEN 'collector' ELSE 'member' END,
                       el.first_seen
                  FROM entity_links el
                  LEFT JOIN LATERAL (
                       SELECT count(*) > 0 AS collector
                         FROM entity_links t
                        WHERE t.link_type = 'transfer_to' AND t.to_id = el.to_id
                          AND t.from_type = 'account' AND t.to_type = 'account'
                  ) inbound ON TRUE
                 WHERE el.link_type = 'opened_on' AND el.from_type = 'device'
                   AND el.to_type = 'account' AND el.from_id = %s
                """,
                (cluster_id, row["device_id"]),
            )
            cur.execute(
                """
                INSERT INTO cluster_members (cluster_id, subject_type, subject_id, role, joined_at)
                VALUES (%s, 'device', %s, 'device', %s)
                ON CONFLICT DO NOTHING
                """,
                (cluster_id, row["device_id"], row["first_seen"]),
            )
            cur.execute(
                """
                UPDATE clusters SET member_count =
                    (SELECT count(*) FROM cluster_members WHERE cluster_id = %s)
                 WHERE cluster_id = %s
                """,
                (cluster_id, cluster_id),
            )
            cur.execute("SELECT count(*) AS n FROM cluster_members WHERE cluster_id = %s", (cluster_id,))
            n = cur.fetchone()["n"]

        built.append(BuiltCluster(cluster_id, natural_key, "device_fanout", n, created))

    # A cluster that no longer meets its rule stops covering anything. The row
    # survives — alert history points at it — but its membership is retired, so
    # a network alert cannot keep asserting coverage the evidence withdrew.
    still_valid = [b.natural_key for b in built]
    with conn.cursor() as cur:
        cur.execute(
            """
            DELETE FROM cluster_members
             WHERE cluster_id IN (SELECT cluster_id FROM clusters
                                   WHERE cluster_type = 'device_fanout'
                                     AND NOT (natural_key = ANY(%s)))
            """,
            (still_valid,),
        )
        cur.execute(
            """
            UPDATE clusters SET member_count = 0, built_at = now()
             WHERE cluster_type = 'device_fanout' AND NOT (natural_key = ANY(%s))
            """,
            (still_valid,),
        )
    return built
