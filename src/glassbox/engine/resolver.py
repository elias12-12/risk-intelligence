"""Subject -> entity resolution (§3.2). The crux.

The scorer this replaces joined feature values on `feature_key + entity_type`
and nothing else, so every card's value matched every transaction of every rule.
Here a subject reaches the entity a feature keys on by walking a graph of stored
edges, along a route the catalog names.

Representation is a graph AND a route selector, because neither alone works:

  * A per-feature path string is under-powered — the same feature is reached
    from different subject types. ip_is_datacenter keys on a transaction;
    R-114's subject IS a transaction, but S-077's subject is an account.
  * A graph alone is under-specified — card->account->customer and
    transaction->customer both exist, and which one is meant is semantic.

The `trigger` root is the load-bearing addition. §2.2 says an evaluation is
"one pass of one lane over one subject" but never makes the triggering row
addressable, and it has to be: S-077's subject is ACC-2201, and its
ip_is_datacenter condition means "the transfer that TRIGGERED this evaluation
came from a datacenter IP" — not "any transaction ever on this account".
Without it that condition is either unresolvable or it fans out over the
account's whole history, and both answers are wrong.

Routes are PLANNED without touching the database and then executed in batch, so
one evaluation cycle over ten thousand transactions costs a handful of queries
rather than forty thousand.
"""
from __future__ import annotations

from collections import deque
from functools import lru_cache
from typing import Any, Hashable, Iterable, Sequence

import psycopg

from ..db import fetch_all
from ..features.predicate import check_column, check_relation
from ..types import EvaluationRequest, FeatureSpec, Resolution, Subject

MAX_DEPTH = 3


class Unresolvable(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class Graph:
    """The resolution graph, loaded once and walked many times."""

    def __init__(self, edges: list[dict], allowlist: dict[str, frozenset[str]]):
        self.by_name: dict[tuple[str, str], dict] = {}
        self.by_edge_id: dict[int, dict] = {}
        self.out: dict[str, list[dict]] = {}
        for e in sorted(edges, key=lambda r: r["edge_id"]):
            # Relation and column names come from data, so they are checked
            # against the same allow-list the feature predicates use before any
            # of them is interpolated into SQL.
            check_relation(e["relation"], allowlist)
            check_column(e["relation"], e["key_column"], allowlist)
            check_column(e["relation"], e["value_column"], allowlist)
            for col in (e["filter_equals"] or {}):
                check_column(e["relation"], col, allowlist)
            self.by_name[(e["from_type"], e["edge_name"])] = e
            self.by_edge_id[e["edge_id"]] = e
            self.out.setdefault(e["from_type"], []).append(e)

    @lru_cache(maxsize=512)
    def shortest_route(self, from_type: str, to_type: str) -> tuple[int, ...] | None:
        """BFS, ties broken by ascending edge_id — so route selection is
        deterministic and a rebuild picks the same path."""
        if from_type == to_type:
            return ()
        seen = {from_type}
        queue: deque[tuple[str, tuple[int, ...]]] = deque([(from_type, ())])
        while queue:
            node, path = queue.popleft()
            if len(path) >= MAX_DEPTH:
                continue
            for e in self.out.get(node, []):
                if e["to_type"] == to_type:
                    return path + (e["edge_id"],)
                if e["to_type"] not in seen:
                    seen.add(e["to_type"])
                    queue.append((e["to_type"], path + (e["edge_id"],)))
        return None

    def named_route(self, from_type: str, names: Sequence[str]) -> tuple[int, ...]:
        node, route = from_type, []
        for name in names:
            edge = self.by_name.get((node, name))
            if edge is None:
                raise Unresolvable(f"no_edge:{name}@{node}")
            route.append(edge["edge_id"])
            node = edge["to_type"]
        return tuple(route)

    def route_label(self, prefix: str, route: tuple[int, ...]) -> str:
        if not route:
            return f"{prefix}:self"
        return prefix + "." + ".".join(self.by_edge_id[e]["edge_name"] for e in route)


def load_graph(conn: psycopg.Connection, allowlist) -> Graph:
    from ..catalog import load_resolution_edges
    return Graph(load_resolution_edges(conn), allowlist)


# ---------------------------------------------------------------- planning
def plan_route(request: EvaluationRequest, spec: FeatureSpec,
               graph: Graph) -> tuple[Subject, tuple[int, ...], str]:
    """Decide the root and the hops. No database access — pure policy."""
    path = (spec.resolution_path or "auto").strip()
    subject, trigger = request.subject, request.trigger

    if path == "self":
        if spec.entity_type != subject.type:
            raise Unresolvable(f"self_mismatch:{spec.entity_type}!={subject.type}")
        return subject, (), "self"

    if path == "trigger":
        if trigger is None:
            raise Unresolvable("no_trigger")
        return trigger, _route_from(graph, trigger.type, spec), graph.route_label(
            "trigger", _route_from(graph, trigger.type, spec))

    if path.startswith("subject."):
        route = graph.named_route(subject.type, path.split(".")[1:])
        return subject, route, path

    if path != "auto":
        raise Unresolvable(f"unknown_path:{path}")

    # auto: shortest path from the subject, then fall back to the trigger.
    try:
        route = _route_from(graph, subject.type, spec)
        return subject, route, graph.route_label("subject", route)
    except Unresolvable:
        if trigger is None or trigger == subject:
            raise
        route = _route_from(graph, trigger.type, spec)
        return trigger, route, graph.route_label("trigger", route)


def _route_from(graph: Graph, from_type: str, spec: FeatureSpec) -> tuple[int, ...]:
    if from_type == spec.entity_type:
        return ()
    route = graph.shortest_route(from_type, spec.entity_type)
    if route is None:
        raise Unresolvable(f"no_path:{from_type}->{spec.entity_type}")
    return route


# ---------------------------------------------------------------- execution
def _hop_batch(conn: psycopg.Connection, edge: dict,
               ids: Iterable[str]) -> dict[str, tuple[str, ...]]:
    ids = sorted(set(ids))
    if not ids:
        return {}
    filters = edge["filter_equals"] or {}
    params: dict[str, Any] = {"ids": ids}
    clauses = [f'"{edge["key_column"]}" = ANY(%(ids)s)',
               f'"{edge["value_column"]}" IS NOT NULL']
    for i, (col, val) in enumerate(sorted(filters.items())):
        params[f"f{i}"] = val
        clauses.append(f'"{col}" = %(f{i})s')
    sql = (f'SELECT DISTINCT "{edge["key_column"]}"::text AS k, '
           f'"{edge["value_column"]}"::text AS v '
           f'FROM {edge["relation"]} WHERE ' + " AND ".join(clauses) + " ORDER BY 1, 2")
    out: dict[str, list[str]] = {}
    for r in fetch_all(conn, sql, params):
        out.setdefault(r["k"], []).append(r["v"])
    return {k: tuple(v) for k, v in out.items()}


def _run_route_batch(conn: psycopg.Connection, graph: Graph, roots: Sequence[str],
                     route: tuple[int, ...]) -> dict[str, tuple[str, ...]]:
    """origin id -> the entity ids it reaches, for a whole population at once."""
    current: dict[str, tuple[str, ...]] = {r: (r,) for r in set(roots)}
    for edge_id in route:
        edge = graph.by_edge_id[edge_id]
        frontier = {i for ids in current.values() for i in ids}
        mapping = _hop_batch(conn, edge, frontier)
        current = {
            origin: tuple(dict.fromkeys(v for i in ids for v in mapping.get(i, ())))
            for origin, ids in current.items()
        }
    return current


def resolve_many(conn: psycopg.Connection, pairs: Sequence[tuple[Hashable, EvaluationRequest, FeatureSpec]],
                 graph: Graph) -> dict[Hashable, Resolution]:
    """Resolve a whole batch. Routes are grouped so each is executed once."""
    out: dict[Hashable, Resolution] = {}
    groups: dict[tuple[str, tuple[int, ...]], list[tuple[Hashable, str, FeatureSpec, str]]] = {}

    for key, request, spec in pairs:
        try:
            root, route, label = plan_route(request, spec, graph)
        except Unresolvable as exc:
            out[key] = Resolution(spec.feature_key, spec.entity_type, (),
                                  spec.resolution_path, "unresolvable", exc.reason)
            continue
        groups.setdefault((root.type, route), []).append((key, root.id, spec, label))

    for (root_type, route), members in groups.items():
        reached = _run_route_batch(conn, graph, [m[1] for m in members], route)
        for key, root_id, spec, label in members:
            out[key] = _apply_fanout(spec, reached.get(root_id, ()), label)
    return out


def resolve(conn: psycopg.Connection, request: EvaluationRequest, spec: FeatureSpec,
            graph: Graph) -> Resolution:
    """Single-shot resolution. Same code path as the batch, one member wide."""
    return resolve_many(conn, [("k", request, spec)], graph)["k"]


def _apply_fanout(spec: FeatureSpec, ids: tuple[str, ...], route: str) -> Resolution:
    """Resolution failure is NEVER a silent zero.

    'no entities' and 'more entities than the policy allows' both produce an
    unresolvable Resolution, which enters the §5 policy exactly as absence does.
    This is the single most important line in the §3.2 fix: today's failure mode
    is a partial score; the new failure mode is a recorded degradation.
    """
    if not ids:
        return Resolution(spec.feature_key, spec.entity_type, (), route,
                          "unresolvable", "no_entities")
    if spec.fanout_policy == "one" and len(ids) != 1:
        return Resolution(spec.feature_key, spec.entity_type, ids, route,
                          "unresolvable", f"fanout_one:{len(ids)}")
    if spec.fanout_policy == "error" and len(ids) > 1:
        return Resolution(spec.feature_key, spec.entity_type, ids, route,
                          "unresolvable", f"fanout_error:{len(ids)}")
    return Resolution(spec.feature_key, spec.entity_type, ids, route)
