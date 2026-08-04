"""The control plane, written — and the first endpoints that can change what
the engine does to a customer.

Everything here is **admin only**, and everything here goes through
`rules/validate.py` first — the same function `POST /simulate/rule` calls, on the
same body, so the rule an admin tested is the rule an admin publishes
(WEEK5-PLAN decision 6). The routes contain no validation of their own; a second
copy would be the drift D4 exists to prevent.

Four things these routes deliberately do not do.

**They do not activate anything.** A published rule lands in `shadow`, where the
engine scores it, records its conditions and records the action it would have
taken, and lets it act on nothing (migration 0030). Going live is
`POST /rules/{id}/promote`, which is a separate call because it is a separate
decision, and because the actor who makes it is recorded on the version it
publishes.

**They do not overwrite a definition.** Every write goes through
`publish_rule_version`, which snapshots the definition into `rule_versions` and
bumps `rule_definitions.version` when — and only when — the definition actually
moved. That is D1: before this, a stored `rule_version_set` named a number no
definition sat behind, and a rule repriced between two decisions looked
identical from both.

**They do not delete.** `DELETE` retires (status `inactive`), because
`decisions.action_source_rule`, `decisions.vetoed_by` and
`alert_signals.source_rule_id` reference `rule_definitions` with no ON DELETE and
Postgres already refuses to remove a rule that ever acted. `?purge=true` exists
for the one honest case — a draft that was never published and never acted — and
refuses everything else.

**They do not commit until the whole publish succeeded.** A rule saved without
its snapshot is a rule whose next decision points at a version that does not
exist, which is worse than the rule not being saved at all.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from ..contract.catalog import FeatureView, RuleDetail, RuleDraft, read_features, read_rule
from ..db import connect
from ..rules.publish import (
    PublishRefused,
    delete_rule,
    promote_rule,
    publish_feature,
    publish_rule,
    retire_rule,
    stored_rule,
)
from ..rules.validate import RuleInvalid, ensure_valid, normalised
from .auth import Principal, require_role

router = APIRouter()


def _validated(conn, body: RuleDraft) -> RuleDraft:
    draft = normalised(body)
    try:
        ensure_valid(conn, draft)
    except RuleInvalid as exc:
        # Every rejection, not the first: an author fixing errors one round trip
        # at a time is an author who stops using the validator.
        raise HTTPException(status_code=422,
                            detail=[r.as_dict() for r in exc.rejections]) from exc
    return draft


def _refused(exc: PublishRefused) -> HTTPException:
    """409, not 422. The draft is fine; the transition is not."""
    return HTTPException(status_code=409, detail=str(exc))


def _detail(conn, rule_id: str) -> RuleDetail:
    rule = read_rule(conn, rule_id)
    if rule is None:  # pragma: no cover - the write just succeeded
        raise HTTPException(status_code=500, detail=f"{rule_id} vanished after publish")
    return rule


@router.post("/rules", response_model=RuleDetail, status_code=201)
def create_rule(body: RuleDraft, who: Principal = Depends(require_role("admin"))):
    """Author a new rule. It lands in shadow and acts on nothing.

    Returns the full `RuleDetail`, including `versions` — which now resolves,
    because the publish that just ran is the first row in it.
    """
    with connect() as conn:
        draft = _validated(conn, body)
        if stored_rule(conn, draft.rule_id) is not None:
            raise HTTPException(
                status_code=409,
                detail=f"rule {draft.rule_id} already exists; "
                       f"PUT /rules/{draft.rule_id} to edit it")
        try:
            publish_rule(conn, draft, actor=who.actor)
        except PublishRefused as exc:
            raise _refused(exc) from exc
        detail = _detail(conn, draft.rule_id)
        conn.commit()
        return detail


@router.put("/rules/{rule_id}", response_model=RuleDetail)
def edit_rule(rule_id: str, body: RuleDraft,
              who: Principal = Depends(require_role("admin"))):
    """Replace a rule's definition, keeping its identity and its history.

    The status in the body must match the status the rule already has. An edit
    that could also flip a rule from shadow to active is an edit that starts
    acting on customers as a side effect of fixing a typo, and the refusal names
    the endpoint that does mean to do that.
    """
    with connect() as conn:
        if rule_id != body.rule_id:
            raise HTTPException(
                status_code=422,
                detail=f"path says {rule_id!r} and body says {body.rule_id!r}; a "
                       f"rule id is identity and this endpoint does not rename")
        if stored_rule(conn, rule_id) is None:
            raise HTTPException(status_code=404, detail=f"no rule {rule_id}")
        draft = _validated(conn, body)
        try:
            publish_rule(conn, draft, actor=who.actor)
        except PublishRefused as exc:
            raise _refused(exc) from exc
        detail = _detail(conn, rule_id)
        conn.commit()
        return detail


@router.post("/rules/{rule_id}/promote", response_model=RuleDetail)
def promote(rule_id: str, who: Principal = Depends(require_role("admin"))):
    """shadow -> active. From here the rule can alert, and can act.

    With two demo users there is no real separation of duties, so a
    second-approver gate would be theatre (decision 2). Recording WHO is the part
    that survives into a real deployment, and it is recorded on the version this
    publishes — not in a log line.
    """
    with connect() as conn:
        try:
            promote_rule(conn, rule_id, actor=who.actor)
        except PublishRefused as exc:
            raise _refused(exc) from exc
        detail = _detail(conn, rule_id)
        conn.commit()
        return detail


@router.delete("/rules/{rule_id}")
def remove_rule(rule_id: str, response: Response,
                purge: bool = Query(default=False),
                who: Principal = Depends(require_role("admin"))):
    """Retire a rule — or, for a draft that never went anywhere, remove it.

    Retiring is the operation that was actually being asked for: an inactive
    rule is not loaded by the engine, and it stays in the table because every
    decision it ever carried names it.
    """
    with connect() as conn:
        try:
            if purge:
                delete_rule(conn, rule_id)
                conn.commit()
                response.status_code = 204
                return None
            retire_rule(conn, rule_id, actor=who.actor)
        except PublishRefused as exc:
            raise _refused(exc) from exc
        detail = _detail(conn, rule_id)
        conn.commit()
        return detail


@router.post("/features/{feature_key}/publish", response_model=FeatureView)
def publish_feature_spec(feature_key: str,
                         who: Principal = Depends(require_role("admin"))):
    """Make a catalog row's current computation spec retrievable at a version.

    There is no feature-AUTHORING endpoint, deliberately: a computation spec is
    edited by seed, because writing one is a data-engineering act rather than an
    admin one (README, "what costs rows and what costs code"). This closes the
    other end of D1 — after such a seed, `feature_version_set` on every decision
    since names a spec version with no definition behind it.

    Idempotent: publishing a spec that has not moved returns the same version and
    writes nothing (0030).
    """
    with connect() as conn:
        try:
            publish_feature(conn, feature_key, actor=who.actor)
        except PublishRefused as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        view = next((f for f in read_features(conn, status=None)
                     if f.feature_key == feature_key), None)
        conn.commit()
        return view
