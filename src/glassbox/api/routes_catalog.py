"""catalog.v1 — the control plane, read.

Open, like every other read here. A console needs the rule list and the catalog
before it can decide whether to render an authoring screen at all, and the rows
behind these endpoints are definitions of synthetic detection logic, not
decisions about people. What is authenticated is what leaves a mark: session 2
gates `/simulate/rule` on the admin role, session 3 gates the writes.

`/reference` exists so the UI's dropdowns and the validator's rejections come
from the same place. Offering an operator `rules/validate.py` will refuse is
D4 wearing a nicer interface.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..contract.catalog import (
    FeatureView,
    ReferenceVocabulary,
    RuleDetail,
    RuleSummary,
    read_features,
    read_reference,
    read_rule,
    read_rules,
)
from ..db import connect

router = APIRouter()


@router.get("/rules", response_model=list[RuleSummary])
def get_rules(status: str | None = None, subject_type: str | None = None):
    """Every rule, including `inactive` ones.

    This is the control plane, not the engine's view of it: a rule that has been
    retired is exactly what an admin needs to see in order to un-retire it, and
    it cannot be deleted anyway — `decisions.action_source_rule` and
    `alert_signals.source_rule_id` reference it with no ON DELETE.
    """
    with connect() as conn:
        return read_rules(conn, status=status, subject_type=subject_type)


@router.get("/rules/{rule_id}", response_model=RuleDetail)
def get_rule(rule_id: str):
    """One rule, its conditions, and what each condition has actually DONE.

    The performance block is `v_condition_performance` — §10's finding, on the
    screen where an admin is editing the price it is a finding about. A condition
    that has never been evaluated carries `performance: null` and a sentence
    saying why, because zeroes there would read as a measurement rather than as
    an absence of one.
    """
    with connect() as conn:
        rule = read_rule(conn, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail=f"no rule {rule_id}")
    return rule


@router.get("/features", response_model=list[FeatureView])
def get_features(status: str | None = "active",
                 inline_capable: bool | None = Query(default=None)):
    """The catalog, computation specs and all.

    `inline_capable` is a filter rather than a decoration because it is a
    constraint on authoring: §2.1 makes a rule inline only if every feature it
    reads is inline-capable, and the validator rejects one that is not.
    """
    with connect() as conn:
        return read_features(conn, status=status, inline_capable=inline_capable)


@router.get("/reference", response_model=ReferenceVocabulary)
def get_reference():
    """Operators, actions and their severity ladder, subject types, reason codes.

    From the `ref_*` rows — the same rows the foreign keys enforce and the
    validator checks against — except for the operator and reducer lists, which
    come from the interpreter itself because they are not data.
    """
    with connect() as conn:
        return read_reference(conn)
