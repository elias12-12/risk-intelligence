"""§3.3 — a network subject is derived, not written down."""
from __future__ import annotations

import re

from glassbox import config
from glassbox.db import fetch_all, fetch_value
from glassbox.graph.builder import build

SUBJECT_ID_PATTERNS = [
    re.compile(r"\bRING-\d+\b"),
    re.compile(r"\bTXN-\d+\b"),
    re.compile(r"\bACC-\d[\w-]*\b"),
    re.compile(r"\bCARD-[\w-]+\b"),
    re.compile(r"\bDEV-[\w-]+\b"),
    re.compile(r"\bCUST-[\w-]+\b"),
    re.compile(r"\bMER-[\w-]+\b"),
]


def test_alert_subjects_set_equals_the_cluster_members(conn):
    covered = fetch_all(
        conn,
        """
        SELECT s.subject_type, s.subject_id
          FROM alert_subjects s
          JOIN alerts a ON a.alert_id = s.alert_id
         WHERE a.subject_type = 'network' AND a.subject_id = 'RING-1187'
        """,
    )
    members = fetch_all(
        conn,
        "SELECT subject_type, subject_id FROM cluster_members WHERE cluster_id='RING-1187'",
    )
    assert {(r["subject_type"], r["subject_id"]) for r in covered} == \
           {(r["subject_type"], r["subject_id"]) for r in members}
    assert len(members) == 5      # four accounts plus the device they share


def test_deleting_a_member_changes_the_alert(conn):
    """The coverage is derived, so removing a member removes coverage. Under
    the old hardcoded INSERT ... SELECT this test could not fail."""
    from glassbox.engine.evaluation import EngineContext, run_lane

    with conn.cursor() as cur:
        cur.execute("DELETE FROM cluster_members "
                    "WHERE cluster_id='RING-1187' AND subject_id='ACC-7745'")
        # §9: the session fixture already left an OPEN alert on RING-1187, and a
        # re-evaluation now folds onto it rather than raising a second one — which
        # is the entire point of alert hygiene. Coverage is derived when a case is
        # RAISED, so this test needs the open case closed to observe it. Folding
        # deliberately never rewrites alert_subjects: a member the builder has
        # since retired was still part of the case an analyst has been reading.
        cur.execute("UPDATE alerts SET status='resolved' WHERE subject_id='RING-1187'")

    run_lane(conn, "async", config.reference_now(), run_id="deleted",
             subject_ids=["RING-1187"], ctx=EngineContext.load(conn))
    covered = {r["subject_id"] for r in fetch_all(
        conn,
        """
        SELECT s.subject_id FROM alert_subjects s
          JOIN alerts a ON a.alert_id = s.alert_id
          JOIN decisions d ON d.decision_id = a.decision_id
         WHERE a.subject_id='RING-1187' AND d.evaluation_id LIKE 'ev_deleted_%'
        """)}
    assert covered and "ACC-7745" not in covered


def test_a_cluster_that_stops_qualifying_is_retired(conn):
    """Drop the fanout below the builder's threshold and the cluster stops
    covering accounts. The row survives because alert history points at it."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM entity_links "
                    "WHERE from_id='DEV-F90D2' AND to_id='ACC-7745' AND link_type='opened_on'")
    build(conn)
    assert fetch_value(
        conn, "SELECT count(*) FROM cluster_members WHERE cluster_id='RING-1187'") == 0
    assert fetch_value(
        conn, "SELECT member_count FROM clusters WHERE cluster_id='RING-1187'") == 0


def test_a_rebuild_keeps_the_same_cluster_id(conn):
    """natural_key is how a rebuild finds the same cluster, so alert history
    survives re-running the builder."""
    before = fetch_all(conn, "SELECT cluster_id, natural_key FROM clusters ORDER BY cluster_id")
    rebuilt = build(conn)
    after = fetch_all(conn, "SELECT cluster_id, natural_key FROM clusters ORDER BY cluster_id")
    assert before == after
    assert [c.cluster_id for c in rebuilt] == [r["cluster_id"] for r in after]
    assert not any(c.created for c in rebuilt)


def test_cluster_members_join_when_the_evidence_says_they_did(conn):
    """joined_at is the LINK's first_seen, not now(). The feature runner reads
    that instant, so a value can be placed at a point in time a past decision
    could actually have seen."""
    rows = fetch_all(
        conn,
        """
        SELECT cm.subject_id, cm.joined_at, el.first_seen
          FROM cluster_members cm
          JOIN entity_links el ON el.to_id = cm.subject_id AND el.link_type='opened_on'
         WHERE cm.cluster_id='RING-1187' AND cm.subject_type='account'
        """,
    )
    assert rows and all(r["joined_at"] == r["first_seen"] for r in rows)


def test_no_file_under_db_names_a_fixture():
    """§3.3's acceptance criterion. The previous scorer failed it in four
    places, including `WHERE el.from_id = 'DEV-F90D2'`."""
    offenders = []
    for path in sorted((config.DB_DIR).rglob("*.sql")):
        if path.parent.name == "seeds":
            continue          # seeds legitimately name rules and features
        # Comments explain the fixtures; CODE must not reference them. Strip
        # both whole-line and trailing comments before looking.
        code = "\n".join(line.split("--", 1)[0]
                         for line in path.read_text(encoding="utf-8").splitlines())
        for pattern in SUBJECT_ID_PATTERNS:
            for hit in pattern.findall(code):
                offenders.append(f"{path.relative_to(config.REPO_ROOT)}: {hit}")
    assert not offenders, "literal subject ids under db/: " + ", ".join(offenders)


def test_ring_1187_is_allocated_by_natural_key_not_by_a_literal(conn):
    assert fetch_value(
        conn, "SELECT natural_key FROM clusters WHERE cluster_id='RING-1187'"
    ) == "device_fanout:DEV-F90D2"
