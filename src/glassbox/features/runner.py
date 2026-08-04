"""The incremental feature runner.

Walks the rows that arrived since a watermark, derives the entities each feature
is affected on, and computes the value AT THE ARRIVING ROW'S INSTANT — never at
"now". That is what makes a stored value replayable: the as_of is a property of
the data, not of when the job happened to run.

Writes are append-only INSERTs with computed_at = clock_timestamp() and the
spec_version that produced them. NEVER an UPSERT: an ON CONFLICT ... DO UPDATE
on feature_values silently defeats migration 0014 and destroys the value a past
decision was made on. clock_timestamp() rather than the DEFAULT because the
default is transaction_timestamp(), so two recomputations in one transaction
would collide on the widened key.

A query that returns nothing writes default_when_absent if the catalog declares
one, and writes NOTHING if it does not — so absence stays observable, which is
exactly what §5 needs to distinguish "we checked, no" from "we do not know".

Batch/incremental consistency testing is Week 3 (§17). run_population against a
historical as_of already gives the batch behaviour, so there is no second
implementation to build when that week arrives.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

import psycopg

from ..catalog import load_feature_specs
from ..types import FeatureSpec
from .aggregations import UnknownReducer, UnsupportedSourceKind
from .compiler import CompiledFeature, compile_spec
from .predicate import TIME_COLUMN, load_allowlist


@dataclass
class RunReport:
    feature_key: str
    rows_written: int
    skipped: str | None = None


@dataclass
class DriverSplit:
    """The catalog, split by whether an arriving row in one relation drives it."""
    driven: list[str] = field(default_factory=list)
    elsewhere: dict[str, str] = field(default_factory=dict)     # driven by another relation
    uncomputable: dict[str, str] = field(default_factory=dict)  # the compiler refuses it


class IncrementalRunner:
    def __init__(self, conn: psycopg.Connection):
        self.conn = conn
        self.allowlist = load_allowlist(conn)
        self.specs: dict[str, FeatureSpec] = load_feature_specs(conn)
        self._compiled: dict[str, CompiledFeature] = {}

    # ---------------------------------------------------------------- compile
    def compiled(self, feature_key: str) -> CompiledFeature:
        """Compiled once per feature and cached for the process lifetime."""
        if feature_key not in self._compiled:
            self._compiled[feature_key] = compile_spec(self.specs[feature_key], self.allowlist)
        return self._compiled[feature_key]

    # ---------------------------------------------------------------- work list
    def _work_sql(self, spec: FeatureSpec, cf: CompiledFeature) -> str:
        """(entity_id, as_of, scope_id, subject_id, self_*) for everything the
        arriving rows could have changed."""
        drv = cf.driver_relation
        cols = self.allowlist[drv]
        time_col = TIME_COLUMN.get(drv)
        if time_col is None:
            raise UnknownReducer(
                f"{spec.feature_key}: driver relation {drv} has no time column, so no "
                f"as_of can be derived — give it an explicit driver_relation"
            )

        scope_col = spec.scope_key if spec.scope_key in cols else cf.driver_key
        subject_col = spec.subject_key if spec.subject_key in cols else cf.driver_key
        selects = [
            f'tt."{cf.driver_key}" AS entity_id',
            f'tt."{time_col}" AS as_of',
            f'tt."{scope_col}" AS scope_id',
            f'tt."{subject_col}" AS subject_id',
        ]
        for c in cf.self_columns:
            if c not in cols:
                raise UnknownReducer(
                    f"{spec.feature_key}: self.{c} is not a column of the driver relation {drv}"
                )
            selects.append(f'tt."{c}" AS self_{c}')

        where = cf.driver_where.replace('t."', 'tt."')
        return f"""
SELECT DISTINCT {', '.join(selects)}
  FROM {drv} tt
 WHERE tt."{cf.driver_key}" IS NOT NULL
   AND tt."{time_col}" <= %(_bound)s
   AND (%(_since)s::timestamptz IS NULL OR tt."{time_col}" > %(_since)s)
   AND ({where})"""

    # ---------------------------------------------------------------- defaults
    @staticmethod
    def _default_pair(spec: FeatureSpec) -> tuple[Any, Any]:
        if not spec.has_default:
            return None, None
        v = spec.default_when_absent
        if isinstance(v, bool):
            return None, v
        if isinstance(v, (int, float)):
            return v, None
        return None, None

    # ---------------------------------------------------------------- run
    def run_feature(self, feature_key: str, as_of: datetime,
                    since: datetime | None = None) -> RunReport:
        spec = self.specs[feature_key]
        try:
            cf = self.compiled(feature_key)
        except (UnsupportedSourceKind, UnknownReducer) as exc:
            return RunReport(feature_key, 0, skipped=str(exc))

        work = self._work_sql(spec, cf)
        dnum, dbool = self._default_pair(spec)
        keep = "TRUE" if spec.has_default else "(r.value_num IS NOT NULL OR r.value_bool IS NOT NULL)"

        sql = f"""
INSERT INTO feature_values
    (feature_key, entity_type, entity_id, as_of, value_num, value_bool,
     computed_at, spec_version)
SELECT %(_fk)s, %(_et)s, w.entity_id, w.as_of,
       CASE WHEN r.value_num IS NULL AND r.value_bool IS NULL
            THEN %(_dnum)s::numeric ELSE r.value_num END,
       CASE WHEN r.value_num IS NULL AND r.value_bool IS NULL
            THEN %(_dbool)s::boolean ELSE r.value_bool END,
       clock_timestamp(), %(_sv)s
  FROM ({work}) w
  LEFT JOIN LATERAL ({cf.render_batch()}) r ON TRUE
 WHERE {keep}"""

        params: dict[str, Any] = {
            "_fk": feature_key, "_et": spec.entity_type, "_sv": spec.spec_version,
            "_dnum": dnum, "_dbool": dbool, "_bound": as_of, "_since": since,
        }
        params.update(cf.params)
        params.update(cf.driver_params)

        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return RunReport(feature_key, cur.rowcount)

    def run_for_entity(self, feature_key: str, entity_id: str, as_of: datetime,
                       scope_id: str | None = None, subject_id: str | None = None,
                       self_values: dict | None = None) -> dict | None:
        """Compute one value without storing it. Used by tests and by the
        acceptance path that needs to show a number next to a stored one."""
        spec = self.specs[feature_key]
        cf = self.compiled(feature_key)
        params: dict[str, Any] = {
            "as_of": as_of,
            "scope_id": scope_id if scope_id is not None else entity_id,
            "subject_id": subject_id if subject_id is not None else entity_id,
        }
        for c in cf.self_columns:
            params[f"self_{c}"] = (self_values or {}).get(c)
        params.update(cf.params)
        with self.conn.cursor() as cur:
            cur.execute(cf.render_single(), params)
            return cur.fetchone()

    def run_population(self, as_of: datetime, since: datetime | None = None,
                       features: Iterable[str] | None = None) -> list[RunReport]:
        keys = list(features) if features is not None else sorted(self.specs)
        return [self.run_feature(k, as_of, since) for k in keys]

    # ---------------------------------------------------------------- drivers
    def driven_by(self, relation: str) -> "DriverSplit":
        """Which features an arriving row in `relation` would recompute.

        Derived from the compiled spec's DRIVER relation, not from
        `source_relation`, and the two differ exactly where it matters: a
        transaction arriving recomputes `min_since_password_reset`, whose source
        is `events` but whose driver is `transactions`, because the feature means
        "minutes between the last reset and THIS MOVEMENT".

        The two ways of not being driven are kept APART rather than merged into
        one "skipped" map, because they are different claims about the answer: a
        feature driven by the link layer was read at a stored value that is
        correct and current, while one the compiler refuses has no computed value
        at all. A caller publishing a scoped pass has to be able to say which,
        and classifying by sniffing a reason string afterwards would be a second
        definition of the distinction.
        """
        split = DriverSplit()
        for key in sorted(self.specs):
            try:
                cf = self.compiled(key)
            except (UnsupportedSourceKind, UnknownReducer) as exc:
                split.uncomputable[key] = str(exc)
                continue
            if cf.driver_relation == relation:
                split.driven.append(key)
            else:
                split.elsewhere[key] = (
                    f"driven by {cf.driver_relation}, not {relation} — an "
                    f"arriving {relation} row does not change it")
        return split
