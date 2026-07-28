"""Turn a catalog row into a parameterised query.

The shape is always:

    SELECT <reducer(aggregation, value_expr)>
    FROM   <source_relation>
    WHERE  <scope_key or subject_key> = <scope>
      AND  <time_col> >  <as_of> - <window>     -- omitted when window_spec IS NULL
      AND  <time_col> <= <as_of>                -- the PIT bound, ALWAYS present
      AND  <filter_predicate>

The `<= as_of` clause is what makes the runner point-in-time correct AT WRITE
TIME, which is the necessary companion to §4's correctness at read time. Neither
alone suffices: a runner that sees the future writes a poisoned value, and no
read bound can un-poison it.

Compiled SQL carries three markers — {as_of}, {scope_id}, {subject_id}, plus
{self_<col>} for predicate self-references. render_single() turns them into
bound parameters; render_batch() turns them into references to a work-list
column so one statement can compute thousands of (entity, instant) pairs. The
markers are internal, never user input, so substituting them is not a boundary.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from ..types import FeatureSpec
from . import predicate as pred_mod
from .aggregations import REDUCERS, Ctx, UnknownReducer, UnsupportedSourceKind
from .predicate import (
    ParamBag,
    PredicateError,
    TIME_COLUMN,
    check_column,
    check_relation,
)

MARKER_RE = re.compile(r"\{(as_of|scope_id|subject_id|self_[a-z0-9_]+)\}")


@dataclass
class CompiledFeature:
    feature_key: str
    sql: str                       # still holds {markers}
    params: dict[str, Any]
    relation: str
    subject_key: str | None
    scope_key: str | None
    self_columns: tuple[str, ...] = ()
    driver_relation: str = ""
    driver_key: str = ""
    driver_where: str = "TRUE"
    driver_params: dict[str, Any] = field(default_factory=dict)

    def render_single(self) -> str:
        return MARKER_RE.sub(lambda m: f"%({m.group(1)})s", self.sql)

    def render_batch(self, alias: str = "w") -> str:
        return MARKER_RE.sub(lambda m: f"{alias}.{m.group(1)}", self.sql)


class _WhereBuilder:
    """Builds the WHERE body, so a reducer can ask for variants of it."""

    def __init__(self, spec: FeatureSpec, relation: str, allowlist, bag: ParamBag,
                 time_col: str | None, scope_col: str | None, subject_col: str | None):
        self.spec = spec
        self.relation = relation
        self.allowlist = allowlist
        self.bag = bag
        self.time_col = time_col
        self.scope_col = scope_col
        self.subject_col = subject_col
        self.baseline = spec.baseline_spec or {}

    def build(self, alias: str = "t", window: timedelta | None = ..., extra: str | None = None,
              pit: bool = True) -> str:
        parts: list[str] = []
        if self.scope_col:
            parts.append(f'{alias}."{self.scope_col}" = {{scope_id}}')

        if self.time_col:
            tref = f'{alias}."{self.time_col}"'
            if pit:
                lag = self.baseline.get("baseline_lag")
                if lag:
                    # A novelty baseline must not include today's own activity:
                    # the first of five identical charges would otherwise make
                    # the fifth "not new", and the burst self-establishes.
                    parts.append(f"{tref} <= {{as_of}} - {self._iv(lag)}")
                else:
                    parts.append(f"{tref} <= {{as_of}}")
            win = self.spec.window if window is ... else window
            if win is not None:
                parts.append(f"{tref} > {{as_of}} - {self._iv(win)}")

        if self.baseline.get("exclude_self") and self.subject_col:
            parts.append(f'{alias}."{self.subject_col}" IS DISTINCT FROM {{subject_id}}')

        parts.append(
            pred_mod.compile(self.spec.filter_predicate, self.relation, self.allowlist,
                             self.bag, self_row=_SELF_MARKERS, alias=alias)
        )
        if extra:
            parts.append(f"({extra})")
        return " AND ".join(p for p in parts if p and p != "TRUE") or "TRUE"

    def _iv(self, value) -> str:
        from ..types import parse_window
        td = value if isinstance(value, timedelta) else parse_window(value)
        return self.bag.bind(td)


class _SelfMarkers(dict):
    """Self-references bind to a {self_<col>} marker rather than a literal, so
    the value can come from a per-row work list in the batch path."""

    def __contains__(self, key) -> bool:
        return bool(re.match(r"^[a-z_][a-z0-9_]*$", str(key)))

    def __getitem__(self, key):
        return _RawMarker(f"{{self_{key}}}")


class _RawMarker(str):
    """Marks a value that must be emitted as SQL text, not bound."""


_SELF_MARKERS = _SelfMarkers()


class _MarkerAwareBag(ParamBag):
    """A ParamBag that passes _RawMarker values through as SQL instead of
    binding them — the ONLY values allowed through are markers this module
    generated itself."""

    def bind(self, value: Any) -> str:
        if isinstance(value, _RawMarker):
            return str(value)
        return super().bind(value)


def compile_spec(spec: FeatureSpec, allowlist: dict[str, frozenset[str]]) -> CompiledFeature:
    if spec.source_kind == "sequence":
        raise UnsupportedSourceKind(
            f"{spec.feature_key}: source_kind='sequence' — the sequence runner is Week 3"
        )
    if not spec.source_relation:
        raise PredicateError(f"{spec.feature_key}: no source_relation")
    if spec.aggregation not in REDUCERS:
        raise UnknownReducer(
            f"{spec.feature_key}: no reducer named {spec.aggregation!r}"
        )

    relation = check_relation(spec.source_relation, allowlist)
    bag = _MarkerAwareBag()
    cols = allowlist[relation]

    subject_col = check_column(relation, spec.subject_key, allowlist) if spec.subject_key in cols else None
    scope_col = check_column(relation, spec.scope_key, allowlist) if spec.scope_key in cols else None
    value_col = check_column(relation, spec.value_expr, allowlist) if spec.value_expr in cols else None
    time_col = TIME_COLUMN.get(relation)

    wb = _WhereBuilder(spec, relation, allowlist, bag, time_col, scope_col, subject_col)
    where = wb.build()

    ctx = Ctx(
        relation=relation,
        value_ref=f't."{value_col}"' if value_col else None,
        value_col=value_col,
        time_ref=f't."{time_col}"' if time_col else None,
        scope_ref=f't."{scope_col}"' if scope_col else None,
        subject_ref=f't."{subject_col}"' if subject_col else None,
        where=where,
        build_where=wb.build,
        as_of="{as_of}",
        scope_id="{scope_id}",
        subject_id="{subject_id}",
        bag=bag,
        window=spec.window,
        baseline=spec.baseline_spec or {},
    )
    sql = REDUCERS[spec.aggregation](ctx).strip()

    driver_rel, driver_key, driver_where, driver_params = _driver(spec, allowlist, relation, subject_col)

    return CompiledFeature(
        feature_key=spec.feature_key,
        sql=sql,
        params=bag.params,
        relation=relation,
        subject_key=subject_col,
        scope_key=scope_col,
        self_columns=tuple(sorted(pred_mod.self_refs(spec.filter_predicate))),
        driver_relation=driver_rel,
        driver_key=driver_key,
        driver_where=driver_where,
        driver_params=driver_params,
    )


def _driver(spec: FeatureSpec, allowlist, relation: str,
            subject_col: str | None) -> tuple[str, str, str, dict]:
    """Which rows make a feature worth recomputing, and at what instant.

    Usually the source relation itself. A dimension-sourced feature needs an
    explicit driver because its value is an AGE: the devices row never changes,
    but device_first_seen_min changes every second, so the instants that matter
    are the ones where the device is USED.

    The driver is deliberately NOT filtered by the feature's own predicate.
    That inference is only valid for monotone accumulators, and it is wrong for
    every windowed feature: recent_travel_purchase driven by travel purchases
    alone is computed twice in the whole dataset, so by the time T-021 reads it
    the value is days stale and the mitigator degrades for the wrong reason.
    A feature that genuinely needs a narrowed driver — accounts_per_device,
    because entity_links is heterogeneous and from_id can be a device or an
    account — declares `driver_filter` in baseline_spec, in the same AST.
    """
    baseline = spec.baseline_spec or {}
    rel = check_relation(baseline.get("driver_relation", relation), allowlist)
    key = check_column(rel, baseline.get("driver_key") or spec.subject_key, allowlist)

    where, params = "TRUE", {}
    if baseline.get("driver_filter"):
        bag = ParamBag(prefix="d")
        where = pred_mod.compile(baseline["driver_filter"], rel, allowlist, bag, alias="t")
        params = bag.params
    return rel, key, where, params
