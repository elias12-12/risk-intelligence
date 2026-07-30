"""Connection helpers. psycopg 3, dict rows everywhere."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row

from . import config


@contextmanager
def connect(dsn: str | None = None, autocommit: bool = False) -> Iterator[psycopg.Connection]:
    conn = psycopg.connect(dsn or config.dsn(), row_factory=dict_row, autocommit=autocommit)
    try:
        yield conn
    finally:
        conn.close()


def fetch_all(conn: psycopg.Connection, sql: str, params: Any = None) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def fetch_one(conn: psycopg.Connection, sql: str, params: Any = None) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def fetch_value(conn: psycopg.Connection, sql: str, params: Any = None) -> Any:
    row = fetch_one(conn, sql, params)
    if row is None:
        return None
    return next(iter(row.values()))
