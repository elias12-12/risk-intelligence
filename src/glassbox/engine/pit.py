"""The point-in-time read (§4).

Two ceilings, and they are not the same thing:

  bound_at    — how far into the DATA the evaluation may see. It is
                occurred_at (inline) or occurred_at + rule.evaluation_lag
                (async). NEVER decided_at: on a replay decided_at is *now*, so
                using it silently reintroduces exactly the lookahead the
                as_of column exists to prevent.
  replay_as_of— how far into our OWN KNOWLEDGE the evaluation may see, a
                computed_at ceiling. Live reads leave it open and get today's
                best answer; a replay pins it and gets what the engine actually
                saw when it decided. To replay a stored decision, pass that
                decision's decided_at: it is the moment its knowledge stopped.
                Pinning to one feature's computed_at instead would exclude
                every feature the runner happened to write after it.

Then staleness, then fan-out. A reduction runs over STORED FEATURE VALUES,
after the read — not over raw rows — and value_as_of records min(as_of) across
contributors, so staleness cannot be laundered by the reduction.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Hashable, Sequence

import psycopg

from ..types import FeatureRead, FeatureSpec, Resolution


@dataclass(frozen=True)
class PitRequest:
    key: Hashable
    spec: FeatureSpec
    resolution: Resolution
    bound_at: datetime
    replay_as_of: datetime | None = None


_LOOKUP = """
SELECT w.i, w.entity_id,
       v.value_num, v.value_bool, v.as_of, v.computed_at, v.spec_version
  FROM (VALUES {values}) AS w(i, feature_key, entity_type, entity_id, bound, replay)
  LEFT JOIN LATERAL (
       SELECT f.value_num, f.value_bool, f.as_of, f.computed_at, f.spec_version
         FROM feature_values f
        WHERE f.feature_key = w.feature_key
          AND f.entity_type = w.entity_type
          AND f.entity_id   = w.entity_id
          AND f.as_of      <= w.bound
          AND (w.replay IS NULL OR f.computed_at <= w.replay)
        ORDER BY f.as_of DESC, f.computed_at DESC
        LIMIT 1
  ) v ON TRUE
"""


def read_many(conn: psycopg.Connection, requests: Sequence[PitRequest]) -> dict[Hashable, FeatureRead]:
    """One query for every (feature, entity) pair in a batch of evaluations."""
    out: dict[Hashable, FeatureRead] = {}
    lookups: list[tuple] = []
    index: list[PitRequest] = []

    for req in requests:
        if req.resolution.status != "ok":
            reason = req.resolution.reason or "unresolvable"
            out[req.key] = FeatureRead(
                feature_key=req.spec.feature_key,
                status="fanout_error" if reason.startswith("fanout") else "unresolvable",
                entity_type=req.spec.entity_type,
                entity_ids=req.resolution.entity_ids,
                reason=reason,
            )
            continue
        i = len(index)
        index.append(req)
        for eid in req.resolution.entity_ids:
            lookups.append((i, req.spec.feature_key, req.spec.entity_type, eid,
                            req.bound_at, req.replay_as_of))

    if not lookups:
        return out

    # The first row carries the casts; psycopg binds every value.
    head = ("(%s::int, %s::text, %s::text, %s::text, %s::timestamptz, %s::timestamptz)")
    rest = ",".join(["(%s, %s, %s, %s, %s, %s)"] * (len(lookups) - 1))
    values = head + ("," + rest if rest else "")
    flat: list[Any] = [x for row in lookups for x in row]

    rows_by_i: dict[int, list[dict]] = {}
    with conn.cursor() as cur:
        cur.execute(_LOOKUP.format(values=values), flat)
        for r in cur.fetchall():
            rows_by_i.setdefault(r["i"], []).append(r)

    for i, req in enumerate(index):
        out[req.key] = _assemble(req, rows_by_i.get(i, []))
    return out


def _assemble(req: PitRequest, rows: list[dict]) -> FeatureRead:
    spec, res = req.spec, req.resolution
    found = [r for r in rows if r["as_of"] is not None]

    if not found:
        return FeatureRead(spec.feature_key, "absent", entity_type=spec.entity_type,
                           entity_ids=res.entity_ids, reason="no_value_at_bound")

    # A reduction must not hide a stale contributor, so staleness is judged on
    # the OLDEST value that went into it, not on the newest.
    oldest_as_of = min(r["as_of"] for r in found)
    if spec.max_staleness is not None:
        staleness: timedelta = req.bound_at - oldest_as_of
        if staleness > spec.max_staleness:
            return FeatureRead(spec.feature_key, "stale", entity_type=spec.entity_type,
                               entity_ids=res.entity_ids, as_of=oldest_as_of,
                               reason=f"stale_by:{staleness - spec.max_staleness}")

    values = [_value_of(spec, r) for r in found]
    if any(v is None for v in values):
        return FeatureRead(spec.feature_key, "absent", entity_type=spec.entity_type,
                           entity_ids=res.entity_ids, as_of=oldest_as_of,
                           reason="null_value")

    # A policy that expects one entity but resolved to several is caught in the
    # resolver; anything reaching here with N > 1 has a reducing policy.
    reduced = _reduce(spec.fanout_policy, values)
    if reduced is None:
        return FeatureRead(spec.feature_key, "fanout_error", entity_type=spec.entity_type,
                           entity_ids=res.entity_ids, as_of=oldest_as_of,
                           reason=f"no_reduction:{spec.fanout_policy}")

    return FeatureRead(
        feature_key=spec.feature_key,
        status="present",
        value=reduced,
        as_of=oldest_as_of,
        computed_at=max(r["computed_at"] for r in found),
        spec_version=found[0]["spec_version"],
        entity_type=spec.entity_type,
        entity_ids=res.entity_ids,
    )


def _value_of(spec: FeatureSpec, row: dict) -> Any:
    if spec.value_type == "boolean":
        return row["value_bool"]
    if row["value_num"] is not None:
        return row["value_num"]
    return row["value_bool"]


def _reduce(policy: str, values: list[Any]) -> Any:
    if len(values) == 1 and policy in ("one", "error"):
        return values[0]
    if policy in ("one", "error"):
        return None
    if policy == "max":
        return max(values)
    if policy == "min":
        return min(values)
    if policy == "sum":
        return sum(values)
    if policy == "mean":
        return sum(values) / Decimal(len(values))
    if policy == "any_true":
        return any(bool(v) for v in values)
    if policy == "all_true":
        return all(bool(v) for v in values)
    if policy == "count_distinct":
        return Decimal(len(set(values)))
    return None


def bound_for(occurred_at: datetime, evaluation_lag: timedelta) -> datetime:
    """The data ceiling. Deliberately a function so nobody reaches for
    decided_at by accident."""
    return occurred_at + (evaluation_lag or timedelta(0))
