"""Structural guarantees about the schema itself."""
from __future__ import annotations

import re
from pathlib import Path

from glassbox import config
from glassbox.db import fetch_all, fetch_value

import migrate as migrate_mod

REPO = config.REPO_ROOT


def test_feature_values_pk_has_five_columns(conn):
    """0014 widened the key to include computed_at. Without that column a
    recomputation at an already-used as_of DESTROYS the value a past decision
    was made on, and replay silently disagrees with the audit trail."""
    cols = [
        r["attname"]
        for r in fetch_all(
            conn,
            """
            SELECT a.attname
              FROM pg_index i
              JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
             WHERE i.indrelid = 'feature_values'::regclass AND i.indisprimary
             ORDER BY a.attname
            """,
        )
    ]
    assert cols == ["as_of", "computed_at", "entity_id", "entity_type", "feature_key"]


def test_computed_at_is_not_nullable(conn):
    assert fetch_value(
        conn,
        "SELECT attnotnull FROM pg_attribute "
        "WHERE attrelid='feature_values'::regclass AND attname='computed_at'",
    ) is True


def test_no_on_conflict_targets_feature_values():
    """An ON CONFLICT ... DO UPDATE on feature_values silently defeats 0014.
    The store is append-only; a recomputation is an INSERT."""
    offenders = []
    for path in list((REPO / "db").rglob("*.sql")) + list((REPO / "src").rglob("*.py")) \
            + list((REPO / "scripts").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"INSERT\s+INTO\s+feature_values.*?(?:;|\"\"\")", text,
                                 re.IGNORECASE | re.DOTALL):
            if re.search(r"ON\s+CONFLICT", match.group(0), re.IGNORECASE):
                offenders.append(str(path.relative_to(REPO)))
    assert not offenders, f"ON CONFLICT on feature_values in: {offenders}"


def test_runner_passes_clock_timestamp_explicitly():
    """computed_at DEFAULT now() is transaction_timestamp(), so two
    recomputations in one transaction would collide on the widened key."""
    text = (REPO / "src" / "glassbox" / "features" / "runner.py").read_text(encoding="utf-8")
    assert "clock_timestamp()" in text


def test_every_file_is_applied_exactly_once(conn):
    applied = {r["version"] for r in fetch_all(conn, "SELECT version FROM schema_migrations")}
    on_disk = {p.name for p in migrate_mod.discover()}
    assert on_disk == applied, f"unapplied: {sorted(on_disk - applied)}"


def test_migrations_are_idempotent_by_ledger(built_database):
    """A second run applies nothing. The ledger, not the SQL, is what makes
    re-running safe — none of the Week-1 files are individually idempotent."""
    assert migrate_mod.migrate(built_database, verbose=False) == []


def test_decisions_uniqueness_is_enforced(conn):
    """§6's 'one decision per (subject, lane, evaluation)' was a claim with no
    enforcement until 0012 added this index."""
    assert fetch_value(
        conn,
        "SELECT count(*) FROM pg_indexes WHERE tablename='decisions' "
        "AND indexname='ux_decisions_eval'",
    ) == 1


def test_alerts_decision_id_is_not_null(conn):
    assert fetch_value(
        conn,
        "SELECT attnotnull FROM pg_attribute "
        "WHERE attrelid='alerts'::regclass AND attname='decision_id'",
    ) is True
