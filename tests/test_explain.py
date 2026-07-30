"""§13 — the copilot and the case report, and the five constraints on them.

§13 calls this "where §1 can be broken silently": an explanation that drops a
mitigator, restates a contribution slightly wrong, or asserts a score it did not
compute looks exactly like a correct explanation. So each of the five constraints
gets a test, and three of them are mechanical rather than textual — a cursor hook
for the scope limit, a numeric-token sweep for the quoting rule, and a raising
validator for the mitigator rule.

The acceptance criterion moved. §13 names "a report generated for T-021" that
"names the three mitigating signals and the veto". After seed 0026 repriced
country_is_new_for_customer, TXN-48251 scores 0 with an empty pool and raises no
alert, so it cannot carry that test. TXN-48300 can and does: it is the case whose
action is `monitor`, whose `vetoed_by` is T-021, and whose pool holds all three
of T-021's mitigators plus the veto signal.
"""
from __future__ import annotations

import re

import psycopg
import pytest
from pydantic import ValidationError

from glassbox.contract.explanation import DRAFT_NOTICE, CopilotAnswer
from glassbox.contract.models import is_contract_violation
from glassbox.contract.read import list_alerts
from glassbox.db import fetch_value
from glassbox.explain import ALLOWED_RELATIONS, answer_chips, build_report, load

# Digits with optional sign/decimal, but not the ones inside an ISO timestamp or
# an identifier like TXN-48300 — those are not quantities and are carried
# verbatim from the row they name.
NUMBER = re.compile(r"(?<![\w:.-])[+-]?\d+(?:\.\d+)?(?![\w:.-])")


@pytest.fixture
def veto_alert_id(conn) -> int:
    """TXN-48300 — the case §13's acceptance now points at."""
    return next(a.alert_id for a in list_alerts(conn, limit=200)
                if a.subject.id == "TXN-48300")


@pytest.fixture
def burst_alert_id(conn) -> int:
    return next(a.alert_id for a in list_alerts(conn, limit=200)
                if a.subject.id == "TXN-48291")


# ------------------------------------------------- constraint 1: scope
def test_it_reads_the_alert_in_view_and_nothing_else(conn, monkeypatch, veto_alert_id):
    """§13's first constraint, enforced rather than documented.

    Every statement executed while building the answers is recorded, and any
    relation outside the allowed set fails the test. `transactions`,
    `feature_values` and `case_outcomes` are all outside it: the copilot has no
    business re-deriving a feature or reading another analyst's verdict.
    """
    seen: list[str] = []
    original = psycopg.Cursor.execute

    def recording(self, query, params=None, **kwargs):
        seen.append(query if isinstance(query, str) else query.decode())
        return original(self, query, params, **kwargs)

    monkeypatch.setattr(psycopg.Cursor, "execute", recording)
    evidence = load(conn, veto_alert_id)
    answer_chips(evidence)
    build_report(evidence)
    monkeypatch.undo()

    assert seen, "the hook must actually have seen the queries"
    forbidden = ("transactions", "feature_values", "case_outcomes",
                 "decision_conditions", "feature_catalog", "clusters",
                 "entity_links", "events")
    for sql in seen:
        lowered = sql.lower()
        for relation in forbidden:
            assert re.search(rf"\b(from|join)\s+{relation}\b", lowered) is None, (
                f"the copilot read {relation}: {sql.strip()[:160]}")
        assert "v_kpi" not in lowered


def test_the_allowed_relations_are_published_on_the_wire(conn, veto_alert_id):
    """A scope limit nobody can see is a promise. The response names it."""
    response = answer_chips(load(conn, veto_alert_id))
    assert tuple(response.reads) == ALLOWED_RELATIONS
    assert "alert_signals" in response.reads
    assert "action_executions" in response.reads


def test_no_cross_alert_inference(conn, veto_alert_id, burst_alert_id):
    """Two cases, two disjoint citation sets. Nothing in one explanation can be
    sourced from a row belonging to the other."""
    a = build_report(load(conn, veto_alert_id))
    b = build_report(load(conn, burst_alert_id))
    keys_a = {c.key for c in a.citations if c.source != "derived"}
    keys_b = {c.key for c in b.citations if c.source != "derived"}
    assert keys_a and keys_b
    assert not (keys_a & keys_b)
    for key in keys_a:
        assert str(veto_alert_id) in key or "decision_id" in key or "execution_id" in key


# ------------------------------------------------- constraint 2: quoted, not restated
@pytest.mark.parametrize("subject", ["TXN-48300", "TXN-48291", "RING-1187", "ACC-2201"])
def test_every_number_traces_to_a_citation(conn, subject):
    """§13: contributions, scores and thresholds are QUOTED, never restated.

    Every numeric token in every rendered line must be traceable to a citation —
    so a number nobody sourced cannot reach the output, because the only way to
    turn a value into text runs through the Quoter.

    Containment, not equality, and that is the point rather than a weakening:
    stored prose is cited AS A WHOLE ("New device fingerprint, first seen 6
    minutes ago" is one citation from one row), while every number the copilot
    computes is cited on its own with its formula. Exempting prose from the sweep
    instead would have left the obvious loophole — any unsourced number could
    hide inside a sentence.
    """
    alert_id = next(a.alert_id for a in list_alerts(conn, limit=200)
                    if a.subject.id == subject)
    response = answer_chips(load(conn, alert_id))

    for answer in response.answers:
        assert answer.citations, f"{answer.chip} rendered lines with no citations"
        haystack = "\n".join(c.value for c in answer.citations)
        for line in answer.lines:
            for token in NUMBER.findall(line):
                assert token in haystack or token.lstrip("+") in haystack, (
                    f"{answer.chip}: {token!r} appears in the output but is not "
                    f"traceable to any cited row:\n  {line}")


def test_derived_numbers_name_their_formula(conn, veto_alert_id):
    """Arithmetic is computed outside the model and injected — and says so."""
    response = answer_chips(load(conn, veto_alert_id))
    why = next(a for a in response.answers if a.chip == "why_flagged")
    derived = [c for c in why.citations if c.source == "derived"]
    assert derived, "the subtotals are computed, and computation must declare itself"
    subtotal = next(c for c in derived if c.label == "mitigating subtotal")
    assert subtotal.key == "-9 + -6 + -4"
    assert subtotal.value == "-19"


def test_the_score_is_quoted_from_the_row_not_recomputed(conn, veto_alert_id):
    response = answer_chips(load(conn, veto_alert_id))
    why = next(a for a in response.answers if a.chip == "why_flagged")
    score = next(c for c in why.citations if c.label == "score")
    assert score.source == "alerts"
    assert score.key == f"alerts.alert_id={veto_alert_id}"
    assert score.value == str(int(why.score_quoted))


# ------------------------------------------------- constraint 3: mitigators and vetoes
def test_the_acceptance_case_names_three_mitigators_and_the_veto(conn, veto_alert_id):
    """§13's acceptance criterion, on the case that can now carry it."""
    evidence = load(conn, veto_alert_id)
    why = next(a for a in answer_chips(evidence).answers if a.chip == "why_flagged")
    text = "\n".join(why.lines)

    assert why.mitigating_total == 3 and why.mitigating_cited == 3
    assert why.veto_total == 1 and why.veto_cited == 1
    for phrase in ("Airline ticket", "match normal spending", "Chip-and-PIN"):
        assert phrase in text
    assert "VETO:" in text
    assert "capped at monitor by T-021" in text

    report = build_report(evidence)
    for phrase in ("Airline ticket", "match normal spending", "Chip-and-PIN"):
        assert phrase in report.markdown
    assert "T-021" in report.markdown


def test_an_explanation_that_drops_a_mitigator_refuses_to_be_built():
    """Enforced as a raising validator, in alert.v1's style, because "an
    explanation that lists only aggravators is wrong even when every line is
    true" is a claim about the payload."""
    from decimal import Decimal
    with pytest.raises(ValidationError, match="only aggravators") as caught:
        CopilotAnswer(chip="why_flagged", question="Why?", lines=["87 is high"],
                      score_quoted=Decimal(87), signals_total=5,
                      mitigating_total=2, mitigating_cited=0)
    assert is_contract_violation(caught.value)


def test_an_explanation_that_drops_a_veto_refuses_too():
    from decimal import Decimal
    with pytest.raises(ValidationError, match="naming the veto") as caught:
        CopilotAnswer(chip="why_flagged", question="Why?", lines=["68"],
                      score_quoted=Decimal(68), signals_total=8,
                      mitigating_total=3, mitigating_cited=3,
                      veto_total=1, veto_cited=0)
    assert is_contract_violation(caught.value)


def test_the_counterfactual_chip_is_not_bound_by_the_score_rule(conn, veto_alert_id):
    """It quotes no score, so constraint 3 does not apply — and pretending it did
    would make the constraint decorative. It still lists the mitigators, because
    they are the answer to that question, not because a validator forced it."""
    clear = next(a for a in answer_chips(load(conn, veto_alert_id)).answers
                 if a.chip == "what_would_clear_it")
    assert clear.score_quoted is None
    assert "Airline ticket" in "\n".join(clear.lines)


def test_a_veto_is_named_where_the_recommendation_is_quoted(conn, veto_alert_id):
    """`recommended_action_text` belongs to the rule that carried the SEVERITY.
    Quoting it alone would advise an analyst to do the thing the system
    deliberately declined to do, in the system's own voice."""
    first = next(a for a in answer_chips(load(conn, veto_alert_id)).answers
                 if a.chip == "what_should_i_do_first")
    text = "\n".join(first.lines)
    assert "CAPPED" in text and "T-021" in text
    assert "what that rule wanted, not what was done" in text


# ------------------------------------------------- constraint 4: the report
def test_the_report_cites_the_full_evidence_set(conn, burst_alert_id):
    report = build_report(load(conn, burst_alert_id))
    md = report.markdown

    assert DRAFT_NOTICE in md, "the notice lives IN the artifact, not beside it"
    assert report.draft is True
    assert "value as of" in md, "§13 names value_as_of explicitly"
    assert "Rule versions:" in md and "Feature versions:" in md
    assert "Degraded" in md or "No feature was missing" in md
    assert "Actions taken" in md

    labels = {c.label for c in report.citations}
    assert any(label.startswith("value_as_of:") for label in labels)
    assert any(label.startswith("contribution:") for label in labels)
    assert any(label.startswith("feature value:") for label in labels)


def test_the_report_admits_its_versions_resolve_to_nothing(conn, burst_alert_id):
    """rule_versions is empty. Printing "R-114 v1" and stopping would imply a
    stored definition sitting behind the number."""
    assert fetch_value(conn, "SELECT count(*) FROM rule_versions") == 0
    report = build_report(load(conn, burst_alert_id))
    assert report.unresolvable_versions
    assert "do not resolve" in report.markdown


def test_a_report_without_its_draft_notice_refuses_to_be_built():
    from glassbox.contract.explanation import CaseReport
    with pytest.raises(ValidationError, match="draft notice") as caught:
        CaseReport(alert_id=1, title="t", subject_type="transaction",
                   subject_id="X", markdown="# Case 1\n\nno notice here")
    assert is_contract_violation(caught.value)


def test_the_report_is_reproducible(conn, burst_alert_id):
    """Two reports of the same case must be identical. A `now()` in the header
    makes a diff useless for the one job an audit artifact has."""
    first = build_report(load(conn, burst_alert_id))
    second = build_report(load(conn, burst_alert_id))
    assert first.markdown == second.markdown
    assert first.generated_from == second.generated_from


def test_the_empty_pool_case_explains_itself(conn):
    """The case 0026 created: a decision with no signals at all.

    TXN-48251 raises no alert, so the report is built for the shape rather than
    the subject — but the branch has to exist, because "sum of signals: 0, which
    equals the score" reads as a bug without the sentence after it.
    """
    from glassbox.explain.case_report import build_report as build
    evidence = load(conn, next(a.alert_id for a in list_alerts(conn, limit=200)))
    assert build(evidence).markdown  # smoke: the generic path still renders

    source = (__import__("inspect").getsource(build))
    assert "mitigating evidence consumed the accusation" in source, (
        "an empty pool must say why it is empty")


# ------------------------------------------------- constraint 5: no unearned claims
def test_nothing_asserts_a_capability_the_system_does_not_have(conn, veto_alert_id):
    """§11's objection to console copy that outruns the system, applied at the
    only place such copy now exists. The escalation toast promising that a
    decision "feeds the next model retrain" is the example it gives; there is no
    model here and calibration is a human-approved recommendation."""
    evidence = load(conn, veto_alert_id)
    text = "\n".join(l for a in answer_chips(evidence).answers for l in a.lines)
    text += build_report(evidence).markdown

    for claim in ("retrain", "machine learning", "the model predicts",
                  "learns from", "improves over time", "AI "):
        assert claim.lower() not in text.lower(), (
            f"the explanation claims {claim!r}, which this system does not do")


def test_the_payload_says_it_is_not_model_backed(conn, veto_alert_id):
    """§18's open decision 7, settled by building. A client must be able to tell
    a templated explanation from a generated one without asking."""
    response = answer_chips(load(conn, veto_alert_id))
    report = build_report(load(conn, veto_alert_id))
    assert response.model_backed is False and report.model_backed is False
    assert "No language model is involved" in response.method
    assert "No language model produced any part of it" in report.markdown


def test_synthetic_outcomes_are_flagged_in_the_prose(conn, burst_alert_id):
    """A challenge outcome settled by a script must not read as customer
    behaviour, in the explanation surface any more than on executions.v1."""
    evidence = load(conn, burst_alert_id)
    assert any(x.synthetic for x in evidence.executions)
    first = next(a for a in answer_chips(evidence).answers
                 if a.chip == "what_should_i_do_first")
    assert "synthetic" in "\n".join(first.lines)
    assert "not observed customer behaviour" in build_report(evidence).markdown
