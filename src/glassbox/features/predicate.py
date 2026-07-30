"""Compile a JSONB filter AST into SQL. THE injection boundary.

No admin-authored string ever reaches SQL text. Three rules, no exceptions:

  1. `op` is looked up in a frozen dict. Anything else raises. No fall-through.
  2. `col` is validated against an allow-list built from information_schema,
     itself restricted to ALLOWED_RELATIONS. Unknown columns raise; they are
     never quoted-and-hoped.
  3. `value` is ALWAYS bound as a parameter — including `in` and `between`.

Depth and arity are capped as well. That guard is about runtime, not injection:
a 400-deep AND is not an attack, it is a query plan nobody wants.
"""
from __future__ import annotations

import re
from typing import Any

import psycopg

MAX_DEPTH = 6
MAX_ARGS = 16

# Relations a feature spec or resolution edge may read. Anything outside this
# set is not addressable from data — adding one is a code change, on purpose.
ALLOWED_RELATIONS: frozenset[str] = frozenset({
    "transactions", "events", "entity_links", "cluster_members", "clusters",
    "devices", "cards", "accounts", "customers", "merchants", "feature_values",
})

_IDENT_RE = re.compile(r"^[a-z_][a-z0-9_]*$")

_LOGICAL = {"and": " AND ", "or": " OR "}
_COMPARISON = {"eq": "=", "ne": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
_NULLARY = {"is_null": "IS NULL", "not_null": "IS NOT NULL"}
OPS: frozenset[str] = frozenset(
    set(_LOGICAL) | {"not"} | set(_COMPARISON) | set(_NULLARY) | {"in", "between"}
)

# The time column of each relation, derived from the RELATION rather than the
# spec — a spec author cannot point the point-in-time bound at the wrong column.
TIME_COLUMN: dict[str, str | None] = {
    "transactions": "occurred_at",
    "events": "occurred_at",
    "entity_links": "first_seen",
    "cluster_members": "joined_at",
    "clusters": "built_at",
    "feature_values": "as_of",
    "devices": None,        # dimensions have neither window nor bound
    "cards": None,
    "accounts": None,
    "customers": None,
    "merchants": None,
}


class PredicateError(ValueError):
    """A filter AST that cannot be compiled. Always fatal — never degraded to
    'match everything', which would silently widen a feature's population."""


def load_allowlist(conn: psycopg.Connection) -> dict[str, frozenset[str]]:
    """relation -> its real column names, straight from the catalog."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_name, column_name
              FROM information_schema.columns
             WHERE table_schema = 'public' AND table_name = ANY(%s)
            """,
            (sorted(ALLOWED_RELATIONS),),
        )
        rows = cur.fetchall()
    out: dict[str, set[str]] = {}
    for r in rows:
        name = r["table_name"] if isinstance(r, dict) else r[0]
        col = r["column_name"] if isinstance(r, dict) else r[1]
        out.setdefault(name, set()).add(col)
    return {k: frozenset(v) for k, v in out.items()}


def check_relation(relation: str, allowlist: dict[str, frozenset[str]]) -> str:
    if relation not in ALLOWED_RELATIONS or relation not in allowlist:
        raise PredicateError(f"relation not permitted: {relation!r}")
    if not _IDENT_RE.match(relation):
        raise PredicateError(f"relation is not a plain identifier: {relation!r}")
    return relation


def check_column(relation: str, col: Any, allowlist: dict[str, frozenset[str]]) -> str:
    if not isinstance(col, str) or not _IDENT_RE.match(col):
        raise PredicateError(f"column is not a plain identifier: {col!r}")
    cols = allowlist.get(relation)
    if cols is None:
        raise PredicateError(f"relation not permitted: {relation!r}")
    if col not in cols:
        raise PredicateError(f"column {col!r} does not exist on {relation}")
    return col


class ParamBag:
    """Collects bound values and hands back placeholder text."""

    def __init__(self, prefix: str = "p"):
        self.prefix = prefix
        self.params: dict[str, Any] = {}
        self._n = 0

    def bind(self, value: Any) -> str:
        name = f"{self.prefix}{self._n}"
        self._n += 1
        self.params[name] = value
        return f"%({name})s"


def _resolve_value(value: Any, self_row: dict | None, bag: ParamBag) -> str:
    """Bind a literal, or a {"ref": "self.<col>"} reference to the triggering row.

    A self-ref is how an event-contextual feature compares against the current
    transaction ("an MCC this customer has never used" needs THIS mcc) without
    templating anything into SQL: the value is looked up in Python and bound.
    """
    if isinstance(value, dict):
        if set(value) != {"ref"}:
            raise PredicateError(f"unsupported value object: {value!r}")
        ref = value["ref"]
        if not isinstance(ref, str) or not ref.startswith("self."):
            raise PredicateError(f"unsupported reference: {ref!r}")
        col = ref[len("self."):]
        if not _IDENT_RE.match(col):
            raise PredicateError(f"reference is not a plain identifier: {ref!r}")
        if self_row is None or col not in self_row:
            raise PredicateError(f"reference {ref!r} has no value in the triggering row")
        return bag.bind(self_row[col])
    return bag.bind(value)


def compile(                     # noqa: A001 - the plan names this function compile
    pred: Any,
    relation: str,
    allowlist: dict[str, frozenset[str]],
    bag: ParamBag,
    self_row: dict | None = None,
    alias: str = "t",
    depth: int = 0,
) -> str:
    """Compile a filter AST to a SQL boolean expression. Returns 'TRUE' for None."""
    if pred is None:
        return "TRUE"
    if depth > MAX_DEPTH:
        raise PredicateError(f"predicate nested deeper than {MAX_DEPTH}")
    if not isinstance(pred, dict):
        raise PredicateError(f"predicate node is not an object: {pred!r}")

    op = pred.get("op")
    if op not in OPS:
        raise PredicateError(f"unknown operator: {op!r}")

    if op in _LOGICAL:
        args = pred.get("args")
        if not isinstance(args, list) or not args:
            raise PredicateError(f"{op!r} needs a non-empty args list")
        if len(args) > MAX_ARGS:
            raise PredicateError(f"{op!r} has {len(args)} args, limit is {MAX_ARGS}")
        parts = [compile(a, relation, allowlist, bag, self_row, alias, depth + 1) for a in args]
        return "(" + _LOGICAL[op].join(parts) + ")"

    if op == "not":
        args = pred.get("args")
        if not isinstance(args, list) or len(args) != 1:
            raise PredicateError("'not' takes exactly one arg")
        return "(NOT " + compile(args[0], relation, allowlist, bag, self_row, alias, depth + 1) + ")"

    col = check_column(relation, pred.get("col"), allowlist)
    ref = f'{alias}."{col}"'

    if op in _NULLARY:
        return f"({ref} {_NULLARY[op]})"

    if "value" not in pred:
        raise PredicateError(f"{op!r} on {col!r} needs a value")
    value = pred["value"]

    if op in _COMPARISON:
        return f"({ref} {_COMPARISON[op]} {_resolve_value(value, self_row, bag)})"

    if op == "in":
        if not isinstance(value, list) or not value:
            raise PredicateError("'in' needs a non-empty list")
        if len(value) > 512:
            raise PredicateError(f"'in' list has {len(value)} entries, limit is 512")
        return f"({ref} = ANY({bag.bind(list(value))}))"

    if op == "between":
        if not isinstance(value, list) or len(value) != 2:
            raise PredicateError("'between' needs exactly two bounds")
        lo = _resolve_value(value[0], self_row, bag)
        hi = _resolve_value(value[1], self_row, bag)
        return f"({ref} BETWEEN {lo} AND {hi})"

    raise PredicateError(f"operator {op!r} reached the end of compile()")  # pragma: no cover


def self_refs(pred: Any, found: set[str] | None = None) -> set[str]:
    """Every self.<col> a predicate depends on, so the caller can fetch them."""
    found = found if found is not None else set()
    if isinstance(pred, dict):
        value = pred.get("value")
        if isinstance(value, dict) and "ref" in value and str(value["ref"]).startswith("self."):
            found.add(str(value["ref"])[len("self."):])
        elif isinstance(value, list):
            for v in value:
                self_refs({"value": v}, found)
        for a in pred.get("args", []) or []:
            self_refs(a, found)
    return found
