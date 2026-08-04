"""What "the alert in view" means, in one place (§13, constraint 1).

§13's first constraint is a scope limit: the copilot reads `alert_signals`,
`decisions` and `action_executions` for the alert in view — and nothing else. No
free-text recall, no cross-alert inference.

Two relations are added to that list and both are the same alert:
`alert_subjects`, which is how a ring alert names its four member accounts, and
`rule_definitions`, which is where `clear_text` and `recommended_action_text`
live — §13 names both as the source for two of the three chips.

**Session 3 adds the two version stores, and that is a widening of a boundary
this project treats as load-bearing, so here is the argument.** §13's constraint
4 requires the report to cite the rule version set. Until the publish path
existed the report could only print those numbers and admit they resolved to
nothing; now that they resolve, the honest thing to print is which definitions
are actually retrievable — and that cannot be known without looking. The lookup
is keyed on `(rule_id, version)` pairs that are already ON the alert, so it
resolves a pointer the alert carries rather than reaching for a fact about
anything else. It is not free-text recall and it cannot see another case: the
`WHERE` clause has no degrees of freedom. Everything the boundary was drawn
against — `transactions`, `feature_values`, `case_outcomes`, other alerts — is
still outside it, and `test_explain.py`'s cursor hook still proves it.

Nothing else is reachable from here. `transactions`, `feature_values`,
`case_outcomes` and every other alert are all outside the boundary, and
test_explain.py enforces that with a cursor hook rather than trusting this
docstring.

The Quoter is the other half of the design. Every number that reaches a rendered
line goes through `q()`, which returns the text AND records where it came from.
That makes constraint 2 — "contributions, scores and thresholds are quoted, never
restated" — mechanical rather than a matter of care: a number nobody quoted
cannot appear, because there is no other way to get one into a string.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import psycopg

from ..contract.explanation import Citation
from ..contract.executions import ExecutionRecord, read_executions
from ..contract.models import AlertDetail
from ..contract.read import get_alert
from ..db import fetch_all

ALLOWED_RELATIONS = (
    "alerts", "decisions", "alert_signals", "alert_subjects",
    "action_executions", "rule_definitions", "rule_versions",
    "feature_catalog_versions",
)


@dataclass
class AlertEvidence:
    """One alert, its signals, its executions, and the rules that scored it."""
    alert: AlertDetail
    executions: list[ExecutionRecord]
    rules: dict[str, dict]          # rule_id -> clear_text / recommended / threshold
    # Versions this decision recorded that no published definition sits behind.
    # Empty on anything published through `rules/publish.py` or backfilled by
    # seed 0031 — which is everything here — and non-empty is a real audit gap
    # the report has to state rather than paper over.
    unresolved_versions: list[str] = field(default_factory=list)

    @property
    def aggravating(self) -> list:
        return [s for s in self.alert.signals if s.direction == "aggravating"]

    @property
    def mitigating(self) -> list:
        return [s for s in self.alert.signals if s.direction == "mitigating"]

    @property
    def vetoes(self) -> list:
        return [s for s in self.alert.signals if s.direction == "veto"]


def load(conn: psycopg.Connection, alert_id: int) -> AlertEvidence | None:
    alert = get_alert(conn, alert_id)
    if alert is None:
        return None

    # Every rule that scored THIS alert. get_alert joins rule_definitions for the
    # action source only; "what would clear it" needs the counterfactual from
    # each rule that fired, and a rule that contributed evidence has a clearing
    # story even when another rule carried the action.
    rules = {}
    if alert.rules_fired:
        rules = {r["rule_id"]: r for r in fetch_all(
            conn,
            "SELECT rule_id, name, clear_text, recommended_action_text, "
            "       review_threshold, prevent_threshold, is_veto "
            "  FROM rule_definitions WHERE rule_id = ANY(%s) ORDER BY rule_id",
            (list(alert.rules_fired),))}

    return AlertEvidence(alert=alert,
                         executions=read_executions(conn, alert_id=alert_id),
                         rules=rules,
                         unresolved_versions=_unresolved_versions(conn, alert))


def _unresolved_versions(conn: psycopg.Connection, alert: AlertDetail) -> list[str]:
    """Which of this decision's recorded versions have no stored definition.

    Asked of the version stores directly rather than assumed either way. Before
    session 3 the answer was "all of them" and the report said so; asserting the
    opposite now without checking would be the same unearned claim in the other
    direction.
    """
    rule_set = alert.evidence.rule_version_set or {}
    feature_set = alert.evidence.feature_version_set or {}
    missing: list[str] = []

    if rule_set:
        found = {(r["rule_id"], r["version"]) for r in fetch_all(
            conn,
            "SELECT rule_id, version FROM rule_versions WHERE rule_id = ANY(%s)",
            (sorted(rule_set),))}
        missing += [f"rule {k} v{v}" for k, v in sorted(rule_set.items())
                    if (k, v) not in found]

    if feature_set:
        found = {(r["feature_key"], r["spec_version"]) for r in fetch_all(
            conn,
            "SELECT feature_key, spec_version FROM feature_catalog_versions "
            " WHERE feature_key = ANY(%s)",
            (sorted(feature_set),))}
        missing += [f"feature {k} v{v}" for k, v in sorted(feature_set.items())
                    if (k, v) not in found]

    return missing


@dataclass
class Quoter:
    """Every number in the output, and where it came from.

    `q` is the only way a value becomes text. Derived values go through `derive`,
    which records the formula instead of a primary key — that is what "arithmetic
    is computed outside the model and injected" looks like when there is no model
    and the arithmetic still has to be auditable.
    """
    citations: list[Citation] = field(default_factory=list)

    def q(self, label: str, source: str, key: str, value: Any) -> str:
        text = _fmt(value)
        self.citations.append(
            Citation(label=label, source=source, key=key, value=text))
        return text

    def derive(self, label: str, formula: str, value: Any) -> str:
        return self.q(label, "derived", formula, value)

    def values(self) -> set[str]:
        return {c.value for c in self.citations}


def _fmt(value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return f"{int(value):+d}" if value < 0 else str(int(value))
        return str(round(value, 2))
    return str(value)


def signed(value: Decimal) -> str:
    """A contribution renders with its sign, always.

    +34 and -9 are different claims about the same customer, and a bar that drops
    the sign on the deductions is the mitigator-erasure §13's third constraint is
    about, happening one layer further out.
    """
    return f"{'+' if value >= 0 else '-'}{abs(int(value)) if value == value.to_integral_value() else abs(round(value, 2))}"
