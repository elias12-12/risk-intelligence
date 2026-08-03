"""D4 — what an admin-authored rule is checked against.

Before this file, the only guardrail on an authored rule was the foreign key on
`rule_conditions.feature_key`. Everything else failed *silently*:

  * `conditions.fires()` returns `False` for an operator it does not recognise,
    so a typo'd `>==` produces a rule that never fires, never errors, and shows
    up in the ledger as a condition that simply never matched. Indistinguishable
    from a rule that is working and finding nothing.
  * A numeric operator with no `threshold_num` does the same thing, by the same
    line of code.
  * A negative contribution on a feature carrying a default makes §5 unreachable
    for that condition — the mitigator can never be *absent*, so it can never
    strip preventive authority. `test_degraded.py` enforces this across the
    shipped catalog; nothing enforced it for a rule authored at runtime.
  * An inline rule reading a feature that is not `inline_capable` is a rule in
    the authorization path reading something the authorization path cannot
    afford, and §2.1 says a rule is inline ONLY IF every feature it reads is.
  * A veto rule with no mitigating condition can never establish its veto —
    `conditions._veto_state` returns False when there is no exonerating evidence
    to look for — so it is a veto that cannot veto, which is the defect §7 was
    written to correct in the first place.

The unifying property of that list: **every one of them produces a rule that
does nothing, rather than a rule that fails.** That is the failure mode this
whole project is built to refuse, so the validator's job is to convert each of
them into a rejection an author can read.

**Why the vocabularies are checked against ROWS and not against a Pydantic
`Literal`.** `ref_action`, `ref_subject_type`, `ref_execution_mode` and
`ref_reason_code` exist so that a new value is an INSERT rather than a
migration. A `Literal` on the request model would be a second copy that goes
stale the moment somebody uses the seam as designed — and the console's
dropdowns come from `GET /reference`, which reads the same rows. One vocabulary,
three consumers. The two exceptions are the operator set and the subject types
the planner can reach, and they are exceptions for the opposite reason: they are
not data at all, they are what the interpreter IMPLEMENTS, so they are imported
from the implementation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import psycopg

from ..catalog import RULE_STATUSES
from ..contract.catalog import COMBINE_VALUES, ConditionDraft, RuleDraft
from ..db import fetch_all
from ..engine.conditions import (
    MEMBERSHIP_OPERATORS,
    NUMERIC_OPERATORS,
    SUPPORTED_OPERATORS,
)
from ..engine.evaluation import PLANNABLE_SUBJECT_TYPES


@dataclass(frozen=True)
class Rejection:
    """One reason a draft was refused, addressed to the person who wrote it."""
    field: str
    value: Any
    reason: str

    def as_dict(self) -> dict:
        return {"field": self.field,
                "value": None if self.value is None else str(self.value),
                "reason": self.reason}


class RuleInvalid(ValueError):
    """A draft that cannot be simulated or published. Carries every rejection,
    not the first one: an author fixing errors one round-trip at a time is an
    author who stops using the validator."""

    def __init__(self, rejections: list[Rejection]):
        self.rejections = rejections
        super().__init__("; ".join(f"{r.field}: {r.reason}" for r in rejections))


# ---------------------------------------------------------------- vocabularies
def _vocabulary(conn: psycopg.Connection) -> dict[str, Any]:
    """One round trip for every list the checks below need."""
    features = {
        r["feature_key"]: r
        for r in fetch_all(
            conn,
            "SELECT feature_key, inline_capable, default_when_absent, status, "
            "       entity_type "
            "  FROM feature_catalog")
    }
    return {
        "actions": {r["action"]: r for r in fetch_all(
            conn, "SELECT action, severity, is_preventive FROM ref_action")},
        "subject_types": {r["subject_type"] for r in fetch_all(
            conn, "SELECT subject_type FROM ref_subject_type")},
        "execution_modes": {r["mode"] for r in fetch_all(
            conn, "SELECT mode FROM ref_execution_mode")},
        "reason_codes": {r["reason_code"] for r in fetch_all(
            conn, "SELECT reason_code FROM ref_reason_code")},
        "features": features,
    }


# ---------------------------------------------------------------- the checks
def validate_draft(conn: psycopg.Connection, draft: RuleDraft) -> list[Rejection]:
    """Every problem with this draft, or an empty list.

    Content only. Whether `rule_id` already exists is a question about the
    REQUEST — a conflict on create, the whole point on edit — so it belongs to
    the caller, not here.
    """
    vocab = _vocabulary(conn)
    out: list[Rejection] = []

    if not draft.rule_id.strip() or " " in draft.rule_id:
        out.append(Rejection("rule_id", draft.rule_id,
                             "a rule id must be non-empty and contain no spaces"))
    if not draft.name.strip():
        out.append(Rejection("name", draft.name, "a rule needs a name"))

    if draft.subject_type not in vocab["subject_types"]:
        out.append(Rejection(
            "subject_type", draft.subject_type,
            f"not in ref_subject_type; known values are "
            f"{sorted(vocab['subject_types'])}"))
    elif draft.subject_type not in PLANNABLE_SUBJECT_TYPES:
        # In the vocabulary, unreachable by the planner. A rule on such a subject
        # would be loaded, never planned, and never evaluated — the silent
        # nothing this file exists to prevent.
        out.append(Rejection(
            "subject_type", draft.subject_type,
            "is a registered subject type but the planner cannot reach it, so a "
            "rule on it would never be evaluated. Adding a subject type is a "
            "code change (engine/evaluation._SUBJECT_SQL), not an INSERT"))

    if draft.execution_mode not in vocab["execution_modes"]:
        out.append(Rejection("execution_mode", draft.execution_mode,
                             f"not in ref_execution_mode; known values are "
                             f"{sorted(vocab['execution_modes'])}"))

    action = vocab["actions"].get(draft.action)
    if action is None:
        out.append(Rejection("action", draft.action,
                             f"not in ref_action; known values are "
                             f"{sorted(vocab['actions'])}"))

    if draft.status not in RULE_STATUSES:
        out.append(Rejection("status", draft.status,
                             f"must be one of {list(RULE_STATUSES)}"))

    if draft.combine.upper() not in COMBINE_VALUES:
        out.append(Rejection("combine", draft.combine,
                             f"must be one of {list(COMBINE_VALUES)}"))

    out += _thresholds(draft, action)
    out += _conditions(draft, vocab)
    return out


def _thresholds(draft: RuleDraft, action: dict | None) -> list[Rejection]:
    out: list[Rejection] = []
    if draft.review_threshold < 0:
        out.append(Rejection("review_threshold", draft.review_threshold,
                             "a score line cannot be negative"))

    if draft.prevent_threshold is None:
        return out

    if action is not None and not action["is_preventive"]:
        # Inert rather than wrong — precedence only consults prevent_threshold
        # for a preventive action — and inert settings are how an author comes to
        # believe a guardrail is in place when it is not.
        out.append(Rejection(
            "prevent_threshold", draft.prevent_threshold,
            f"action {draft.action!r} is not preventive, so a prevent threshold "
            f"would never be consulted. Leave it null"))
    if draft.prevent_threshold < draft.review_threshold:
        out.append(Rejection(
            "prevent_threshold", draft.prevent_threshold,
            f"is below review_threshold ({draft.review_threshold}). §7.3: a "
            f"preventive action requires a SEPARATE, HIGHER bar than an alert, "
            f"because a wrong alert costs analyst minutes and a wrong block "
            f"costs a customer"))
    return out


def _conditions(draft: RuleDraft, vocab: dict) -> list[Rejection]:
    out: list[Rejection] = []
    if not draft.conditions:
        out.append(Rejection("conditions", None,
                             "a rule with no conditions can never fire"))
        return out

    for i, cond in enumerate(draft.conditions):
        out += _condition(f"conditions[{i}]", cond, draft, vocab)

    if draft.is_veto and not any(c.contribution_points < 0 for c in draft.conditions):
        out.append(Rejection(
            "is_veto", True,
            "a veto rule with no mitigating condition can never establish its "
            "veto: establishment is defined over EXONERATING evidence, so this "
            "rule would cap nothing, ever"))
    return out


def _condition(where: str, cond: ConditionDraft, draft: RuleDraft,
               vocab: dict) -> list[Rejection]:
    out: list[Rejection] = []

    if cond.operator not in SUPPORTED_OPERATORS:
        out.append(Rejection(
            f"{where}.operator", cond.operator,
            f"is not an operator the engine implements. Known: "
            f"{list(SUPPORTED_OPERATORS)}. An unrecognised operator does not "
            f"raise at evaluation time — the condition simply never fires"))
    elif cond.operator in NUMERIC_OPERATORS and cond.threshold_num is None:
        out.append(Rejection(
            f"{where}.threshold_num", None,
            f"operator {cond.operator!r} compares numbers and no threshold_num "
            f"was given, so the condition would never fire"))
    elif cond.operator in MEMBERSHIP_OPERATORS and not (cond.threshold_text or "").strip():
        out.append(Rejection(
            f"{where}.threshold_text", cond.threshold_text,
            "operator 'in' needs a comma-separated threshold_text to match against"))
    elif (cond.operator not in NUMERIC_OPERATORS
          and cond.operator not in MEMBERSHIP_OPERATORS
          and cond.threshold_num is None and cond.threshold_text is None):
        out.append(Rejection(
            f"{where}.threshold_text", None,
            f"operator {cond.operator!r} needs something to compare against — "
            f"threshold_num, or threshold_text ('true'/'false' for a boolean "
            f"feature)"))

    if cond.reason_code is not None and cond.reason_code not in vocab["reason_codes"]:
        out.append(Rejection(
            f"{where}.reason_code", cond.reason_code,
            "not in ref_reason_code. New reason codes are an INSERT into that "
            "table, which is the seam working — but they have to exist first"))

    feature = vocab["features"].get(cond.feature_key)
    if feature is None:
        out.append(Rejection(
            f"{where}.feature_key", cond.feature_key,
            "is not in feature_catalog. A rule may only be built from registered "
            "features"))
        return out

    if feature["status"] != "active":
        out.append(Rejection(
            f"{where}.feature_key", cond.feature_key,
            f"is in the catalog with status {feature['status']!r}; the runner "
            f"does not compute it, so the condition would always degrade"))

    if draft.execution_mode == "inline_sync" and not feature["inline_capable"]:
        out.append(Rejection(
            f"{where}.feature_key", cond.feature_key,
            "is not inline_capable, and §2.1 makes a rule inline ONLY IF every "
            "feature it reads is. Run this rule async, or use an inline feature"))

    if cond.contribution_points < 0 and feature["default_when_absent"] is not None:
        out.append(Rejection(
            f"{where}.contribution_points", cond.contribution_points,
            f"is a mitigator on {cond.feature_key!r}, which carries a default "
            f"({feature['default_when_absent']!r}). A mitigator that defaults "
            f"can never be observed ABSENT, so §5 can never strip preventive "
            f"authority for it — the score rises on missing exonerating evidence "
            f"and the system acts on it anyway"))

    return out


def ensure_valid(conn: psycopg.Connection, draft: RuleDraft) -> None:
    """`validate_draft`, raising. The form the write paths use."""
    rejections = validate_draft(conn, draft)
    if rejections:
        raise RuleInvalid(rejections)


def normalised(draft: RuleDraft) -> RuleDraft:
    """The draft as it would be stored: `combine` upper-cased, and nothing else.

    Deliberately minimal. A validator that quietly repairs a draft is a validator
    whose rejections an author cannot predict, and the repaired value is the one
    that ends up in the audit trail.
    """
    return draft.model_copy(update={"combine": draft.combine.upper()})
