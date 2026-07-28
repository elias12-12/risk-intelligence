"""Load the catalog, the rules and the resolution graph out of the database.

Nothing here interprets anything — it turns rows into the dataclasses the rest
of the engine works with. The point is that these are the ONLY definitions: if
a rule changes, it changes here, in data, and no Python is edited.
"""
from __future__ import annotations

from datetime import timedelta
from functools import lru_cache
from typing import Any

import psycopg

from .db import fetch_all
from .types import FeatureSpec, Rule, RuleCondition

_UNSET = object()


def _json_default(raw: Any) -> tuple[Any, bool]:
    """Split default_when_absent into (value, has_default).

    SQL NULL means 'no default — write nothing, let absence stay observable'.
    Any JSONB value, including 0 and false, is a real default. That distinction
    is the whole of §5, so it gets its own function rather than a truthiness
    check somewhere downstream.
    """
    if raw is None:
        return None, False
    return raw, True


def load_feature_specs(conn: psycopg.Connection) -> dict[str, FeatureSpec]:
    rows = fetch_all(
        conn,
        """
        SELECT feature_key, display_name, entity_type, value_type, window_spec,
               source_kind, source_relation, subject_key, scope_key,
               resolution_path, filter_predicate, value_expr, aggregation,
               baseline_spec, refresh, max_staleness, default_when_absent,
               fanout_policy, spec_version, default_reason_code, inline_capable
          FROM feature_catalog
         WHERE status = 'active'
         ORDER BY feature_key
        """,
    )
    specs: dict[str, FeatureSpec] = {}
    for r in rows:
        default, has_default = _json_default(r["default_when_absent"])
        specs[r["feature_key"]] = FeatureSpec(
            feature_key=r["feature_key"],
            display_name=r["display_name"],
            entity_type=r["entity_type"],
            value_type=r["value_type"],
            window_spec=r["window_spec"],
            source_kind=r["source_kind"],
            source_relation=r["source_relation"],
            subject_key=r["subject_key"],
            scope_key=r["scope_key"],
            resolution_path=r["resolution_path"],
            filter_predicate=r["filter_predicate"],
            value_expr=r["value_expr"],
            aggregation=r["aggregation"],
            baseline_spec=r["baseline_spec"],
            refresh=r["refresh"],
            max_staleness=r["max_staleness"],
            default_when_absent=default,
            has_default=has_default,
            fanout_policy=r["fanout_policy"],
            spec_version=r["spec_version"],
            default_reason_code=r["default_reason_code"],
            inline_capable=r["inline_capable"],
        )
    return specs


def load_rules(conn: psycopg.Connection, lane: str | None = None) -> list[Rule]:
    sql = """
        SELECT rule_id, name, subject_type, execution_mode, action,
               review_threshold, combine, status, version, is_veto,
               prevent_threshold, evaluation_lag,
               recommended_action_text, clear_text
          FROM rule_definitions
         WHERE status IN ('active','shadow')
    """
    params: list[Any] = []
    if lane is not None:
        sql += " AND execution_mode = %s"
        params.append(lane)
    sql += " ORDER BY rule_id"

    cond_rows = fetch_all(
        conn,
        """
        SELECT condition_id, rule_id, condition_group, feature_key, operator,
               threshold_num, threshold_text, contribution_points, reason_code,
               signal_template, is_required
          FROM rule_conditions
         ORDER BY rule_id, condition_group, condition_id
        """,
    )
    by_rule: dict[str, list[RuleCondition]] = {}
    for c in cond_rows:
        by_rule.setdefault(c["rule_id"], []).append(
            RuleCondition(
                condition_id=c["condition_id"],
                rule_id=c["rule_id"],
                condition_group=c["condition_group"],
                feature_key=c["feature_key"],
                operator=c["operator"],
                threshold_num=c["threshold_num"],
                threshold_text=c["threshold_text"],
                contribution_points=c["contribution_points"],
                reason_code=c["reason_code"],
                signal_template=c["signal_template"],
                is_required=c["is_required"],
            )
        )

    return [
        Rule(
            rule_id=r["rule_id"],
            name=r["name"],
            subject_type=r["subject_type"],
            execution_mode=r["execution_mode"],
            action=r["action"],
            review_threshold=r["review_threshold"],
            combine=r["combine"] or "AND",
            status=r["status"],
            version=r["version"],
            is_veto=r["is_veto"],
            prevent_threshold=r["prevent_threshold"],
            evaluation_lag=r["evaluation_lag"] or timedelta(0),
            recommended_action_text=r["recommended_action_text"],
            clear_text=r["clear_text"],
            conditions=tuple(by_rule.get(r["rule_id"], ())),
        )
        for r in fetch_all(conn, sql, params or None)
    ]


def load_resolution_edges(conn: psycopg.Connection) -> list[dict]:
    return fetch_all(
        conn,
        """
        SELECT edge_id, from_type, edge_name, to_type, kind, relation,
               key_column, value_column, filter_equals, cardinality
          FROM resolution_edges
         ORDER BY edge_id
        """,
    )


def load_action_severity(conn: psycopg.Connection) -> dict[str, tuple[int, bool]]:
    return {
        r["action"]: (r["severity"], r["is_preventive"])
        for r in fetch_all(conn, "SELECT action, severity, is_preventive FROM ref_action")
    }


def load_score_bands(conn: psycopg.Connection) -> dict[str, list[tuple[str, Any]]]:
    """subject_type -> [(band, min_score)] ordered by min_score DESC."""
    bands: dict[str, list[tuple[str, Any]]] = {}
    for r in fetch_all(
        conn,
        "SELECT subject_type, band, min_score FROM score_bands ORDER BY subject_type, min_score DESC",
    ):
        bands.setdefault(r["subject_type"], []).append((r["band"], r["min_score"]))
    return bands
