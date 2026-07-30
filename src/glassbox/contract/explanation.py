"""explanation.v1 — the copilot and the case report (§13).

A FOURTH SIBLING of alert.v1. models.py is not touched, for the fourth time.

§13 calls this "where §1 can be broken silently", and the reason is worth
restating: an explanation that drops a mitigator, restates a contribution
slightly wrong, or asserts a score it did not compute looks exactly like a
correct explanation. That is a worse failure than a wrong score, because the
whole premise of this system is that the explanation is the product.

THIS IMPLEMENTATION IS DETERMINISTIC TEMPLATING AND NOTHING ELSE. No model is
involved, at any point, in any of these fields. §18's open decision 7 is settled
that way deliberately: it is §13's own recommendation ("the explanation surface
of a glass-box system should not itself be a black box"), it is the entire demo
value with none of the risk, and it is a design choice rather than a limitation.
If a model is introduced later it sits behind the same five constraints, with the
arithmetic still computed outside it.

THE CONSTRAINTS, AND WHERE EACH ONE IS ENFORCED
-----------------------------------------------

1. Reads the alert in view and nothing else — enforced in explain/, and by a
   cursor hook in test_explain.py that fails on a query touching any relation
   outside {alerts, decisions, alert_signals, alert_subjects, action_executions,
   rule_definitions}.

2. Numbers are QUOTED, never restated. Every number that reaches a rendered line
   passes through `Quoter.q`, which records a `Citation` naming the table and the
   primary key it came from. Derived numbers — sums, counts — are cited too, with
   `source='derived'` and the formula in `key`, so "computed outside the model"
   is visible rather than promised. A test extracts every numeric token from
   every line and asserts it appears among the citations.

3. Mitigators and applied vetoes appear in EVERY explanation that mentions the
   score. Enforced HERE, as a raising validator, in the same style as
   alert.v1's `sum(signals) == score` — because "an explanation that lists only
   aggravators is wrong even when every line is true" is a claim about the
   payload, and the payload is what should refuse to be built.

4. The case report cites the full evidence set and states on the artifact that it
   is a draft.

5. Nothing asserts a capability the system does not have. §11's objection to
   console copy that outruns the system, applied at the only place such copy
   currently exists.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import ContractViolation

STRICT = ConfigDict(extra="forbid", frozen=True)

CHIPS = ("why_flagged", "what_would_clear_it", "what_should_i_do_first")

METHOD = (
    "Deterministic templating over stored rows. No language model is involved in "
    "any field of this payload; every number is quoted from the citation beside "
    "it and every derived number names its formula."
)

DRAFT_NOTICE = (
    "DRAFT — generated from stored rows. An analyst must review this before it "
    "is filed. Nothing in it has been checked against anything outside this "
    "case."
)


class Citation(BaseModel):
    """Where one quoted value came from.

    `source='derived'` is not a cheat: it is how arithmetic declares itself. The
    formula goes in `key`, so a reader can recompute it from the other citations
    without trusting this one.
    """
    model_config = STRICT

    label: str
    source: str            # alert_signals | decisions | action_executions | ... | derived
    key: str               # primary key, or the formula for a derived value
    value: str


class CopilotAnswer(BaseModel):
    model_config = STRICT

    chip: Literal["why_flagged", "what_would_clear_it", "what_should_i_do_first"]
    question: str
    lines: list[str]
    citations: list[Citation] = Field(default_factory=list)

    # The score this answer quoted, if it quoted one. Constraint 3 applies only
    # to answers that mention the score — "what would clear it" recites a
    # counterfactual and is not making a claim about the arithmetic.
    score_quoted: Decimal | None = None
    signals_total: int = 0
    mitigating_total: int = 0
    mitigating_cited: int = 0
    veto_total: int = 0
    veto_cited: int = 0

    @model_validator(mode="after")
    def _mitigators_survive(self) -> "CopilotAnswer":
        if self.score_quoted is None:
            return self
        if self.mitigating_cited != self.mitigating_total:
            raise ContractViolation(
                f"{self.chip}: quotes a score of {self.score_quoted} but cites "
                f"{self.mitigating_cited} of {self.mitigating_total} mitigating "
                f"signals. An explanation that lists only aggravators is wrong "
                f"even when every line in it is true."
            )
        if self.veto_cited != self.veto_total:
            raise ContractViolation(
                f"{self.chip}: quotes a score of {self.score_quoted} without "
                f"naming the veto that capped the action. A capped decision with "
                f"nothing joining the score to the action is invisible policy."
            )
        return self


class CopilotResponse(BaseModel):
    model_config = STRICT

    alert_id: int
    subject_type: str
    subject_id: str
    answers: list[CopilotAnswer]
    method: str = METHOD
    model_backed: bool = False
    # Every relation this response was allowed to read. Published so a reviewer
    # can check the claim rather than take it: §13's first constraint is a scope
    # limit, and a scope limit nobody can see is a promise.
    reads: list[str] = Field(default_factory=list)


class CaseReport(BaseModel):
    model_config = STRICT

    alert_id: int
    title: str
    subject_type: str
    subject_id: str
    generated_from: datetime | None = None     # the decision's decided_at, not now()
    markdown: str
    citations: list[Citation] = Field(default_factory=list)
    draft: bool = True
    draft_notice: str = DRAFT_NOTICE
    method: str = METHOD
    model_backed: bool = False
    # rule_version_set names versions; rule_versions is empty, so they resolve to
    # nothing. The report says which rather than implying a stored definition
    # exists behind the number it just printed.
    unresolvable_versions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _is_a_draft(self) -> "CaseReport":
        if not self.draft or self.draft_notice not in self.markdown:
            raise ContractViolation(
                f"case report for alert {self.alert_id} does not carry its draft "
                f"notice in the artifact itself. §13: analyst review before "
                f"filing is stated ON the artifact, not alongside it."
            )
        return self
