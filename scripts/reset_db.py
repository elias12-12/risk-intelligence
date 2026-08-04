#!/usr/bin/env python3
"""Drop and rebuild a database from scratch: migrate, seed, load fixtures.

Used by bootstrap.ps1 and by tests/conftest.py, so there is exactly one
definition of "a freshly built GlassBox database".
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import psycopg  # noqa: E402

from glassbox import config  # noqa: E402

import migrate as migrate_mod  # noqa: E402


def _admin_dsn(dsn: str) -> tuple[str, str]:
    """(dsn pointing at 'postgres', target database name)."""
    parts = urlsplit(dsn)
    dbname = parts.path.lstrip("/")
    return urlunsplit(parts._replace(path="/postgres")), dbname


def recreate(dsn: str) -> None:
    admin, dbname = _admin_dsn(dsn)
    with psycopg.connect(admin, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (dbname,),
        )
        cur.execute(f'DROP DATABASE IF EXISTS "{dbname}"')
        cur.execute(f'CREATE DATABASE "{dbname}"')


def load_fixtures(dsn: str, path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"{path} — run scripts/generate_synthetic.py first")
    sql = path.read_text(encoding="utf-8")
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(sql)


def build(dsn: str, fixtures: bool = True, features: bool = True,
          verbose: bool = True) -> None:
    recreate(dsn)
    migrate_mod.migrate(dsn, include_seeds=True, verbose=verbose)
    if fixtures:
        load_fixtures(dsn, config.FIXTURES_DIR / "synthetic_data.sql")
    if features:
        from glassbox.db import connect
        from glassbox.features.runner import IncrementalRunner
        from glassbox.graph.builder import build as build_graph
        from glassbox.ingest import watermark

        with connect(dsn) as conn:
            build_graph(conn)
            conn.commit()
            runner = IncrementalRunner(conn)
            total = sum(r.rows_written for r in runner.run_population(config.reference_now()))
            # The two stages this function actually ran, marked as consumed up
            # to the newest event on record. The LANES are deliberately left
            # unset: nothing here evaluated anything, so a watermark claiming
            # otherwise would make the first cycle skip the whole population.
            frontier = watermark.frontier(conn)
            watermark.advance(conn, watermark.GRAPH, frontier)
            watermark.advance(conn, watermark.FEATURES, frontier)
            conn.commit()
        if verbose:
            print(f"  {total} feature values")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=None)
    ap.add_argument("--no-fixtures", action="store_true")
    ap.add_argument("--no-features", action="store_true")
    args = ap.parse_args()
    dsn = args.dsn or config.dsn()
    print(f"rebuilding {dsn.rsplit('@', 1)[-1]}")
    build(dsn, fixtures=not args.no_fixtures, features=not args.no_features)
    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
