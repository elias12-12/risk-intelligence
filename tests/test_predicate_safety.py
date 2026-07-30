"""§3.1's injection boundary. No admin-authored string reaches SQL text."""
from __future__ import annotations

import pytest

from glassbox.catalog import load_feature_specs
from glassbox.features import predicate as P
from glassbox.features.compiler import compile_spec


@pytest.fixture
def allowlist(conn):
    return P.load_allowlist(conn)


INJECTION_SHAPES = [
    ("column name carrying SQL",
     {"op": "eq", "col": "channel; DROP TABLE transactions--", "value": "x"}),
    ("column that does not exist",
     {"op": "eq", "col": "definitely_not_a_column", "value": "x"}),
    ("operator that is not in the frozen dict",
     {"op": "exec", "col": "channel", "value": "x"}),
    ("operator smuggled as SQL",
     {"op": "channel = 1 OR 1=1 --", "col": "channel", "value": "x"}),
    ("column referencing another table",
     {"op": "eq", "col": "transactions.channel", "value": "x"}),
    ("quoted column trying to close the identifier",
     {"op": "eq", "col": 'channel" OR "1"="1', "value": "x"}),
]


@pytest.mark.parametrize("label,shape", INJECTION_SHAPES, ids=[s[0] for s in INJECTION_SHAPES])
def test_injection_shapes_are_rejected(label, shape, allowlist):
    with pytest.raises(P.PredicateError):
        P.compile(shape, "transactions", allowlist, P.ParamBag())


def test_relation_outside_the_allowlist_is_rejected(allowlist):
    with pytest.raises(P.PredicateError):
        P.check_relation("pg_shadow", allowlist)
    with pytest.raises(P.PredicateError):
        P.check_relation("schema_migrations", allowlist)


def test_values_are_bound_never_interpolated(allowlist):
    """The classic payload compiles fine — because it becomes a parameter."""
    payload = "' OR 1=1--"
    bag = P.ParamBag()
    sql = P.compile({"op": "eq", "col": "channel", "value": payload},
                    "transactions", allowlist, bag)
    assert payload not in sql
    assert payload in bag.params.values()
    assert sql == '(t."channel" = %(p0)s)'


def test_in_and_between_bind_too(allowlist):
    bag = P.ParamBag()
    sql = P.compile({"op": "and", "args": [
        {"op": "in", "col": "channel", "value": ["pos", "'; DELETE FROM alerts --"]},
        {"op": "between", "col": "amount", "value": [1, 2]},
    ]}, "transactions", allowlist, bag)
    assert "DELETE" not in sql
    assert sql.count("%(") == 3          # one ANY array + two bounds


def test_depth_and_arity_are_capped(allowlist):
    deep = {"op": "eq", "col": "channel", "value": "x"}
    for _ in range(P.MAX_DEPTH + 2):
        deep = {"op": "and", "args": [deep]}
    with pytest.raises(P.PredicateError):
        P.compile(deep, "transactions", allowlist, P.ParamBag())

    wide = {"op": "or", "args": [{"op": "eq", "col": "channel", "value": str(i)}
                                 for i in range(P.MAX_ARGS + 1)]}
    with pytest.raises(P.PredicateError):
        P.compile(wide, "transactions", allowlist, P.ParamBag())


def test_no_catalogued_spec_emits_a_literal(conn, allowlist):
    """Every real spec compiles to SQL whose only variable parts are markers
    and placeholders — the seeded filter values never appear as text."""
    specs = load_feature_specs(conn)
    for spec in specs.values():
        if spec.source_kind == "sequence":
            continue
        compiled = compile_spec(spec, allowlist)
        for value in compiled.params.values():
            if isinstance(value, str) and len(value) > 2:
                assert value not in compiled.sql, (
                    f"{spec.feature_key}: {value!r} was interpolated, not bound")


def test_self_reference_to_an_unknown_shape_is_rejected(allowlist):
    with pytest.raises(P.PredicateError):
        P.compile({"op": "eq", "col": "mcc", "value": {"ref": "other.mcc"}},
                  "transactions", allowlist, P.ParamBag(), self_row={"mcc": "1"})
    with pytest.raises(P.PredicateError):
        P.compile({"op": "eq", "col": "mcc", "value": {"lookup": "self.mcc"}},
                  "transactions", allowlist, P.ParamBag(), self_row={"mcc": "1"})
