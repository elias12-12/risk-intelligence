#!/usr/bin/env python3
"""Apply db/migrations and db/seeds in filename order against a ledger.

Each file runs with autocommit=True: every Week-1 file wraps itself in
BEGIN;/COMMIT;, and wrapping them again produces a nested-COMMIT warning and a
silent no-op. The ledger row is written as a separate statement afterwards, so a
file that fails mid-way leaves no ledger entry and can be re-run.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import psycopg  # noqa: E402

from glassbox import config  # noqa: E402

LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


def discover(include_seeds: bool = True) -> list[Path]:
    files = sorted(config.MIGRATIONS_DIR.glob("*.sql"))
    if include_seeds:
        files += sorted(config.SEEDS_DIR.glob("*.sql"))
    # Migrations and seeds interleave by number on purpose: 0009's seed must
    # land after 0008's DDL and before 0011 alters the table it populates.
    ordered = sorted(files, key=lambda p: p.name)
    # Views read the finished schema, so they go last.
    return ordered + sorted(config.VIEWS_DIR.glob("*.sql"))


def is_view(path: Path) -> bool:
    """Views are DEFINITIONS, not migrations, and are re-applied every run.

    Each view file opens with DROP VIEW IF EXISTS and is idempotent by
    construction, so replaying it costs nothing. Honouring the ledger for them
    was a latent defect rather than a policy: a view is edited in place — there
    is no v_kpi_cases_2.sql — so the ledger entry from its first run meant that
    every later edit applied to a fresh database and silently did not apply to an
    existing one. That went unnoticed while no view had ever been changed. Week 5
    changed `v_kpi_cases` (0029's disposition_source), which is what surfaced it.
    """
    return path.parent == config.VIEWS_DIR


def applied_versions(conn: psycopg.Connection) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT version FROM schema_migrations")
        return {r[0] for r in cur.fetchall()}


def apply_file(conn: psycopg.Connection, path: Path) -> None:
    sql = path.read_text(encoding="utf-8")
    with conn.cursor() as cur:
        cur.execute(sql)
        cur.execute(
            "INSERT INTO schema_migrations (version) VALUES (%s) ON CONFLICT DO NOTHING",
            (path.name,),
        )


def migrate(dsn: str, include_seeds: bool = True, verbose: bool = True) -> list[str]:
    applied: list[str] = []
    # row_factory left at default: the ledger read is positional.
    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(LEDGER_DDL)
        done = applied_versions(conn)
        for path in discover(include_seeds):
            if path.name in done and not is_view(path):
                if verbose:
                    print(f"  skip  {path.name}")
                continue
            try:
                apply_file(conn, path)
            except Exception as exc:  # noqa: BLE001 — the message is the product here
                print(f"  FAIL  {path.name}\n        {exc}", file=sys.stderr)
                raise
            applied.append(path.name)
            if verbose:
                print(f"  apply {path.name}")
    return applied


def main() -> int:
    ap = argparse.ArgumentParser(description="Apply GlassBox migrations and seeds.")
    ap.add_argument("--dsn", default=None)
    ap.add_argument("--no-seeds", action="store_true", help="migrations only")
    ap.add_argument("--status", action="store_true", help="list pending files and exit")
    args = ap.parse_args()

    target = args.dsn or config.dsn()
    if args.status:
        with psycopg.connect(target, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(LEDGER_DDL)
            done = applied_versions(conn)
        for path in discover(not args.no_seeds):
            state = ("always " if is_view(path)
                     else "applied" if path.name in done else "PENDING")
            print(f"  {state}  {path.name}")
        return 0

    print(f"migrating {target.rsplit('@', 1)[-1]}")
    applied = migrate(target, include_seeds=not args.no_seeds)
    print(f"done — {len(applied)} file(s) applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
