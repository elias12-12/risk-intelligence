"""D1 — writing a rule down in a way that survives being changed.

Before this file, the control plane was writable only by hand and versioned by
nobody. `rule_definitions.version` and `feature_catalog.spec_version` were
written once by a seed and never again — including by `0026`, which repriced
T-021 and moved a signed-off score without touching the counter. Every decision
in the database records a `rule_version_set`, and until now every one of those
numbers resolved to nothing, because the version STORES created in `0013` had
never had a row in them.

Three properties, and none of them is optional.

**The definition is applied and published in the same call, or neither.** A
saved rule with no snapshot is a rule whose stored decisions point at a version
that does not exist; a snapshot with no rule is a definition of nothing. The
caller's transaction is the boundary — `POST /rules` commits once, at the end.

**Applying and simulating share one function.** `apply_definition` is the exact
INSERT/UPDATE pair `engine/simulate._apply_draft` used to own, and
`simulate_rule` now calls it inside its rolled-back scope. What an admin tested
is what an admin publishes, down to the statement; the only difference is the
COMMIT, which is the whole of WEEK5-PLAN decision 6.

**A new rule lands in shadow and cannot leave it by being saved again.** The
promotion is its own call, recording its own actor (decision 2). Publishing a
draft that asks for `active` is REFUSED rather than quietly downgraded to
shadow, because an author who believes they published a live rule and did not is
in the worst of the three possible states.

The version arithmetic itself is in SQL — `publish_rule_version` in migration
0030 — because seed `0031` backfills the definitions that predate this file and
has to produce byte-identical rows. Two implementations of "what a published
version is" would diverge exactly the way §3.1 says train and serve diverge.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

import psycopg

from ..catalog import RULE_STATUSES, SHADOW
from ..contract.catalog import RuleDraft
from ..db import fetch_one, fetch_value

ACTIVE = "active"
INACTIVE = "inactive"


class PublishRefused(ValueError):
    """A write the control plane will not make. Distinct from `RuleInvalid`,
    which is about the draft's CONTENT: these are refusals about the transition
    being asked for, and the message names the endpoint that would do it."""


@dataclass(frozen=True)
class Published:
    """What a publish did, in the caller's terms."""
    rule_id: str
    version: int
    status: str
    created: bool
    version_bumped: bool
    published_by: str


# ---------------------------------------------------------------- the write
def apply_definition(conn: psycopg.Connection, draft: RuleDraft, replacing: bool,
                     created_by: str) -> None:
    """The rule row and its conditions, as authored.

    Shared verbatim with the rule what-if, which calls this inside a transaction
    that is rolled back. Conditions are replaced wholesale rather than diffed:
    `condition_id` is a surrogate key with no meaning to an author, and a diff
    would have to guess which stored condition a submitted one "is".

    Note what the DELETE cascades to on an edit — `decision_conditions` rows for
    the old conditions. That is a real loss of ledger history for a repriced
    rule, and it is why `rule_versions` holds the conditions as JSONB: the
    definition survives the edit even though the per-condition ledger does not.
    """
    lag = timedelta(seconds=draft.evaluation_lag_seconds or 0)
    fields = (draft.name, draft.description, draft.subject_type,
              draft.execution_mode, draft.action, draft.review_threshold,
              draft.prevent_threshold, draft.is_veto, draft.combine.upper(),
              draft.status, lag, draft.recommended_action_text, draft.clear_text)

    with conn.cursor() as cur:
        if replacing:
            cur.execute(
                """
                UPDATE rule_definitions
                   SET name = %s, description = %s, subject_type = %s,
                       execution_mode = %s, action = %s, review_threshold = %s,
                       prevent_threshold = %s, is_veto = %s, combine = %s,
                       status = %s, evaluation_lag = %s,
                       recommended_action_text = %s, clear_text = %s
                 WHERE rule_id = %s
                """,
                (*fields, draft.rule_id),
            )
            cur.execute("DELETE FROM rule_conditions WHERE rule_id = %s",
                        (draft.rule_id,))
        else:
            cur.execute(
                """
                INSERT INTO rule_definitions
                    (rule_id, name, description, subject_type, execution_mode,
                     action, review_threshold, prevent_threshold, is_veto,
                     combine, status, evaluation_lag, recommended_action_text,
                     clear_text, created_by, shadow_since)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        CASE WHEN %s = 'shadow' THEN now() END)
                """,
                (draft.rule_id, *fields, created_by, draft.status),
            )

        for cond in draft.conditions:
            cur.execute(
                """
                INSERT INTO rule_conditions
                    (rule_id, condition_group, feature_key, operator,
                     threshold_num, threshold_text, contribution_points,
                     reason_code, signal_template, is_required)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (draft.rule_id, cond.condition_group, cond.feature_key,
                 cond.operator, cond.threshold_num, cond.threshold_text,
                 cond.contribution_points, cond.reason_code,
                 cond.signal_template, cond.is_required),
            )


def stored_rule(conn: psycopg.Connection, rule_id: str) -> dict | None:
    return fetch_one(
        conn,
        "SELECT rule_id, status, version FROM rule_definitions WHERE rule_id = %s",
        (rule_id,))


def publish_rule(conn: psycopg.Connection, draft: RuleDraft, actor: str) -> Published:
    """Write a rule and make the written definition retrievable.

    Does not commit — the route owns the transaction, so a publish that fails
    its own snapshot leaves no rule behind.
    """
    existing = stored_rule(conn, draft.rule_id)
    before = existing["version"] if existing else 0

    if existing is None:
        if draft.status != SHADOW:
            raise PublishRefused(
                f"a new rule lands in {SHADOW!r} and is promoted separately, so "
                f"status={draft.status!r} is refused here. Publish it, measure it "
                f"against the population it will run on, then "
                f"POST /rules/{draft.rule_id}/promote — which records who did it "
                f"(WEEK5-PLAN decision 2)")
    elif draft.status != existing["status"]:
        raise PublishRefused(
            f"{draft.rule_id} is {existing['status']!r} and this draft says "
            f"{draft.status!r}. A status change is its own call: "
            f"POST /rules/{draft.rule_id}/promote to activate, "
            f"DELETE /rules/{draft.rule_id} to retire. An edit that could also "
            f"flip the switch is an edit that can start acting on customers by "
            f"accident")

    apply_definition(conn, draft, replacing=existing is not None, created_by=actor)
    version = _publish_version(conn, draft.rule_id, actor)
    return Published(rule_id=draft.rule_id, version=version, status=draft.status,
                     created=existing is None, version_bumped=version > before,
                     published_by=actor)


def promote_rule(conn: psycopg.Connection, rule_id: str, actor: str) -> Published:
    """shadow -> active. The moment a rule is allowed to touch a customer.

    A version bump falls out of it rather than being asked for: `status` is part
    of the published definition, and a decision made by an acting rule is not
    replayable against a definition that says the rule was only watching.
    """
    return _transition(conn, rule_id, to=ACTIVE, actor=actor,
                       allowed_from=(SHADOW,),
                       refusal=("only a shadow rule can be promoted. An active "
                                "rule is already acting; an inactive one is "
                                "retired and has to be re-published first"))


def retire_rule(conn: psycopg.Connection, rule_id: str, actor: str) -> Published:
    """Deletion, as the schema already understood it (decision 7).

    `decisions.action_source_rule`, `decisions.vetoed_by` and
    `alert_signals.source_rule_id` all reference `rule_definitions` with no
    ON DELETE clause, so Postgres refuses to delete a rule that ever acted. That
    is the audit trail defending itself, and this is the operation that was
    actually being asked for.
    """
    return _transition(conn, rule_id, to=INACTIVE, actor=actor,
                       allowed_from=(ACTIVE, SHADOW, INACTIVE),
                       refusal="")


def delete_rule(conn: psycopg.Connection, rule_id: str) -> None:
    """Hard delete, and only for a draft that never went anywhere.

    Refused for anything ever published — a version store with a dangling
    rule_id is not an audit trail — and refused by Postgres itself for anything
    that ever acted. `rule_conditions` cascades.
    """
    if stored_rule(conn, rule_id) is None:
        raise PublishRefused(f"no rule {rule_id}")
    published = fetch_value(
        conn, "SELECT count(*) AS n FROM rule_versions WHERE rule_id = %s",
        (rule_id,))
    if published:
        raise PublishRefused(
            f"{rule_id} has {published} published version(s) and cannot be "
            f"purged: the definitions behind every stored rule_version_set have "
            f"to stay resolvable. Retire it instead — DELETE /rules/{rule_id} "
            f"without purge sets status='inactive'")
    with conn.cursor() as cur:
        cur.execute("DELETE FROM rule_definitions WHERE rule_id = %s", (rule_id,))


def _transition(conn: psycopg.Connection, rule_id: str, to: str, actor: str,
                allowed_from: tuple[str, ...], refusal: str) -> Published:
    existing = stored_rule(conn, rule_id)
    if existing is None:
        raise PublishRefused(f"no rule {rule_id}")
    if existing["status"] not in allowed_from:
        raise PublishRefused(f"{rule_id} is {existing['status']!r}: {refusal}")

    with conn.cursor() as cur:
        cur.execute(
            "UPDATE rule_definitions SET status = %s, "
            "       shadow_since = CASE WHEN %s = 'shadow' THEN now() "
            "                           ELSE shadow_since END "
            " WHERE rule_id = %s",
            (to, to, rule_id))
    version = _publish_version(conn, rule_id, actor)
    return Published(rule_id=rule_id, version=version, status=to, created=False,
                     version_bumped=version > existing["version"],
                     published_by=actor)


def _publish_version(conn: psycopg.Connection, rule_id: str, actor: str) -> int:
    """0030's function, which is where the bump-if-changed rule lives."""
    return fetch_value(conn, "SELECT publish_rule_version(%s, %s) AS v",
                       (rule_id, actor))


# ---------------------------------------------------------------- features
def publish_feature(conn: psycopg.Connection, feature_key: str, actor: str) -> int:
    """The other half of D1 (§Part II: `spec_version` -> `feature_version_set`).

    There is no feature-authoring endpoint — a computation spec is edited by
    seed, because writing one is a data-engineering act rather than an admin one
    (README, "what costs rows and what costs code"). What this closes is the
    other end: after such a seed the catalog row has moved and nothing records
    that it did, so `feature_version_set` on every decision since names a spec
    version with no definition behind it.
    """
    if fetch_value(conn, "SELECT count(*) AS n FROM feature_catalog "
                         "WHERE feature_key = %s", (feature_key,)) == 0:
        raise PublishRefused(f"no feature {feature_key}")
    return fetch_value(conn, "SELECT publish_feature_version(%s, %s) AS v",
                       (feature_key, actor))


def publish_all_features(conn: psycopg.Connection, actor: str) -> dict[str, int]:
    """Every catalog row, published. Idempotent by construction: a spec that has
    not moved since its last publish gets no new version (0030)."""
    keys = [r["feature_key"] for r in _feature_keys(conn)]
    return {key: publish_feature(conn, key, actor) for key in keys}


def _feature_keys(conn: psycopg.Connection) -> list[dict]:
    from ..db import fetch_all
    return fetch_all(conn, "SELECT feature_key FROM feature_catalog "
                           "ORDER BY feature_key")


assert set(RULE_STATUSES) == {ACTIVE, SHADOW, INACTIVE}, (
    "the lifecycle grew a status this module does not handle")
