"""The named reducers.

§3.1 lists seven: count, sum, distinct_count, ratio, zscore, bool_exists,
min_gap. The 21 catalogued features need more than that, so eleven are added
here. This does NOT break the design's safety property: a named reducer is a
Python function, not an expression language, so no admin-authored text becomes
SQL. It does sharpen the honest framing of §14's claim — a pattern expressible
with an existing reducer costs INSERTs; a pattern that needs a new one is a
data-engineering ticket. The README says exactly that.

Every reducer returns SQL producing exactly one row of (value_num, value_bool).
Both NULL means "the computation had nothing to work with", which the runner
turns into the catalog's default_when_absent — or into nothing at all, if the
catalog declines to invent one.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Callable

from .predicate import ParamBag


@dataclass
class Ctx:
    """Everything a reducer may reference. All identifiers are pre-validated."""
    relation: str
    value_ref: str | None          # e.g. t."amount_base"
    value_col: str | None
    time_ref: str | None           # e.g. t."occurred_at"
    scope_ref: str | None          # e.g. t."customer_id"
    subject_ref: str | None        # e.g. t."txn_id"
    where: str                     # scope + PIT bound + window + predicate
    build_where: Callable[..., str]
    as_of: str                     # '{as_of}' marker
    scope_id: str                  # '{scope_id}' marker
    subject_id: str                # '{subject_id}' marker
    bag: ParamBag
    window: timedelta | None
    baseline: dict[str, Any]

    def iv(self, value: str | timedelta) -> str:
        from ..types import parse_window
        td = value if isinstance(value, timedelta) else parse_window(value)
        return self.bag.bind(td)


class UnknownReducer(ValueError):
    pass


class UnsupportedSourceKind(ValueError):
    """Raised for source_kind='sequence' — the sequence runner is Week 3."""


_NUM = "::numeric AS value_num, NULL::boolean AS value_bool"
_BOOL = "NULL::numeric AS value_num, "


# ---------------------------------------------------------------- §3.1's seven
def count(c: Ctx) -> str:
    return f"SELECT count(*){_NUM} FROM {c.relation} t WHERE {c.where}"


def sum_(c: Ctx) -> str:
    return f"SELECT sum({c.value_ref}){_NUM} FROM {c.relation} t WHERE {c.where}"


def distinct_count(c: Ctx) -> str:
    return f"SELECT count(DISTINCT {c.value_ref}){_NUM} FROM {c.relation} t WHERE {c.where}"


def bool_exists(c: Ctx) -> str:
    return (f"SELECT {_BOOL}EXISTS (SELECT 1 FROM {c.relation} t WHERE {c.where}) "
            f"AS value_bool")


def bool_not_exists(c: Ctx) -> str:
    return (f"SELECT {_BOOL}NOT EXISTS (SELECT 1 FROM {c.relation} t WHERE {c.where}) "
            f"AS value_bool")


def zscore(c: Ctx) -> str:
    return (f"SELECT ((max({c.value_ref}) - avg({c.value_ref})) "
            f"/ NULLIF(stddev_samp({c.value_ref}), 0)){_NUM} "
            f"FROM {c.relation} t WHERE {c.where}")


def min_gap(c: Ctx) -> str:
    """Minutes between funds arriving and the first forward-out after them."""
    ins = c.build_where(extra="t.\"direction\" = 'inbound'")
    outs = c.build_where(extra="t.\"direction\" = 'outbound'")
    return f"""
SELECT (SELECT min(EXTRACT(EPOCH FROM (o.ts - i.ts)) / 60.0)
          FROM (SELECT {c.time_ref} AS ts FROM {c.relation} t WHERE {ins}) i
          JOIN (SELECT {c.time_ref} AS ts FROM {c.relation} t WHERE {outs}) o
            ON o.ts >= i.ts){_NUM}"""


# ---------------------------------------------------------------- the eleven
def age_minutes(c: Ctx) -> str:
    """Age of a dimension timestamp at as_of. The value changes with as_of even
    though the source row never does, which is why its driver is usage."""
    return f"""
SELECT (EXTRACT(EPOCH FROM ({c.as_of} - {c.value_ref})) / 60.0){_NUM}
  FROM {c.relation} t
 WHERE {c.where} AND {c.value_ref} IS NOT NULL AND {c.value_ref} <= {c.as_of}"""


def age_minutes_latest(c: Ctx) -> str:
    """Minutes since the most recent matching row at or before as_of."""
    return f"""
SELECT (EXTRACT(EPOCH FROM ({c.as_of} - max({c.time_ref}))) / 60.0){_NUM}
  FROM {c.relation} t WHERE {c.where}"""


def geo_jump_km(c: Ctx) -> str:
    """Great-circle km from the previous session LOCATION.

    A session is a place, not a row: five charges from one origin are one
    session, so this walks back to the most recent location that DIFFERS from
    the newest one. Taking the literal previous row would report 0 km for
    exactly the burst the rule exists to catch.
    """
    where = c.build_where(extra='t."txn_lat" IS NOT NULL AND t."txn_lon" IS NOT NULL')
    return f"""
WITH locs AS (
    SELECT t."txn_lat" AS la, t."txn_lon" AS lo,
           row_number() OVER (ORDER BY {c.time_ref} DESC, t."txn_lat" DESC, t."txn_lon" DESC) AS rn
      FROM {c.relation} t WHERE {where}
), cur AS (SELECT la, lo FROM locs WHERE rn = 1),
   prev AS (SELECT l.la, l.lo FROM locs l CROSS JOIN cur c
             WHERE (l.la, l.lo) IS DISTINCT FROM (c.la, c.lo)
             ORDER BY l.rn LIMIT 1)
SELECT (2 * 6371.0 * asin(least(1.0, sqrt(
          power(sin(radians(p.la - c.la) / 2), 2)
        + cos(radians(c.la)) * cos(radians(p.la))
        * power(sin(radians(p.lo - c.lo) / 2), 2))))){_NUM}
  FROM cur c CROSS JOIN prev p"""


def rate_ratio(c: Ctx) -> str:
    """Window rate over baseline rate, both as events per second."""
    bw = c.baseline.get("baseline_window", "30d")
    from ..types import parse_window
    base_td = parse_window(bw)
    base_where = c.build_where(window=base_td)
    win_secs = c.window.total_seconds() if c.window else 1.0
    return f"""
WITH w AS (SELECT count(*)::numeric AS n FROM {c.relation} t WHERE {c.where}),
     b AS (SELECT count(*)::numeric AS n FROM {c.relation} t WHERE {base_where})
SELECT ((w.n / {win_secs}) / NULLIF(b.n / {base_td.total_seconds()}, 0)){_NUM}
  FROM w CROSS JOIN b"""


def out_over_in_ratio(c: Ctx) -> str:
    """Funds forwarded out over funds in. Transfers only — card spend is not
    pass-through, and counting it would make every ordinary account look like a
    mule account."""
    return f"""
SELECT (COALESCE(sum(CASE WHEN t."direction" = 'outbound' THEN {c.value_ref} ELSE 0 END), 0)
      / NULLIF(COALESCE(sum(CASE WHEN t."direction" = 'inbound' THEN {c.value_ref} ELSE 0 END), 0), 0)){_NUM}
  FROM {c.relation} t WHERE {c.where}"""


def pct_of_running_balance(c: Ctx) -> str:
    """The latest outbound transfer as a percent of the balance standing before it.

    There is no balance column and no ledger, so the balance is derived:
    credits minus debits over a baseline window, counting only approved rows.
    """
    bw = c.baseline.get("balance_window", "90d")
    where = c.build_where(window=None, extra=f"""t."occurred_at" > {c.as_of} - {c.iv(bw)}
                          AND t."auth_result" = 'approved'""")
    return f"""
WITH win AS (
    SELECT {c.time_ref} AS ts, t."direction" AS dir, {c.value_ref} AS amt
      FROM {c.relation} t WHERE {where}
), last_out AS (
    SELECT ts, amt FROM win WHERE dir = 'outbound' ORDER BY ts DESC LIMIT 1
), bal AS (
    SELECT COALESCE(sum(CASE WHEN w.dir IN ('inbound','credit')  THEN  w.amt
                             WHEN w.dir IN ('outbound','debit')  THEN -w.amt
                             ELSE 0 END), 0) AS b
      FROM win w CROSS JOIN last_out l WHERE w.ts < l.ts
)
SELECT (l.amt / NULLIF(bal.b, 0) * 100){_NUM}
  FROM last_out l CROSS JOIN bal"""


def zscore_of_self(c: Ctx) -> str:
    """How far THIS row's value sits from the scope's baseline, in std devs."""
    base = c.build_where()
    return f"""
WITH self AS (SELECT {c.value_ref} AS v FROM {c.relation} t WHERE {c.subject_ref} = {c.subject_id}),
     base AS (SELECT avg({c.value_ref}) AS m, stddev_samp({c.value_ref}) AS s
                FROM {c.relation} t WHERE {base})
SELECT (abs(self.v - base.m) / NULLIF(base.s, 0)){_NUM}
  FROM self CROSS JOIN base"""


def cluster_density(c: Ctx) -> str:
    """Realised links among cluster members over possible pairs."""
    return f"""
WITH m AS (SELECT subject_type, subject_id FROM cluster_members
            WHERE cluster_id = {c.scope_id}),
     n AS (SELECT count(*)::numeric AS c FROM m),
     e AS (SELECT count(*)::numeric AS c FROM entity_links el
            WHERE EXISTS (SELECT 1 FROM m WHERE m.subject_id = el.from_id AND m.subject_type = el.from_type)
              AND EXISTS (SELECT 1 FROM m WHERE m.subject_id = el.to_id   AND m.subject_type = el.to_type))
SELECT (CASE WHEN n.c < 2 THEN 0 ELSE e.c / (n.c * (n.c - 1) / 2) END){_NUM}
  FROM n CROSS JOIN e"""


def in_reference_set(c: Ctx) -> str:
    """Membership of an external reference set. The set lives in baseline_spec
    because there is no feed here; in production this reads the feed's table.

    split_part strips the netmask: an `inet` column renders as '185.220.101.7/32'
    under ::text, so a plain string set never matches it. That mismatch is
    invisible — it returns FALSE rather than erroring — and it silently cost
    S-077 the 13 points its signed-off score of 58 depends on.
    """
    members = [str(x) for x in (c.baseline.get("set") or [])]
    return f"""
SELECT {_BOOL}(split_part({c.value_ref}::text, '/', 1) = ANY({c.bag.bind(members)})) AS value_bool
  FROM {c.relation} t WHERE {c.subject_ref} = {c.subject_id}"""


def eq_const(c: Ctx) -> str:
    return f"""
SELECT {_BOOL}({c.value_ref} = {c.bag.bind(c.baseline.get('const'))}) AS value_bool
  FROM {c.relation} t WHERE {c.subject_ref} = {c.subject_id}"""


def bool_not_exists_alias(c: Ctx) -> str:  # pragma: no cover - registry symmetry
    return bool_not_exists(c)


REDUCERS: dict[str, Callable[[Ctx], str]] = {
    "count": count,
    "sum": sum_,
    "distinct_count": distinct_count,
    "bool_exists": bool_exists,
    "bool_not_exists": bool_not_exists,
    "zscore": zscore,
    "min_gap": min_gap,
    "age_minutes": age_minutes,
    "age_minutes_latest": age_minutes_latest,
    "geo_jump_km": geo_jump_km,
    "rate_ratio": rate_ratio,
    "out_over_in_ratio": out_over_in_ratio,
    "pct_of_running_balance": pct_of_running_balance,
    "zscore_of_self": zscore_of_self,
    "cluster_density": cluster_density,
    "in_reference_set": in_reference_set,
    "eq_const": eq_const,
}

# 'ratio' is named by §3.1 but no catalogued feature uses it, so it is
# deliberately absent rather than guessed at. A spec that asks for it fails
# loudly at compile time instead of returning a number nobody defined.
