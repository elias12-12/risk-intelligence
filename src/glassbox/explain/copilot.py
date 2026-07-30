"""The three copilot chips (§13), as deterministic templating.

§13 maps them explicitly:

    why was this flagged      -> the signal set
    what would clear it       -> rule_definitions.clear_text
    what should I do first    -> rule_definitions.recommended_action_text

with one addition the plan did not name and §8 made available: "what should I do
first" also reports what has ALREADY been done to this customer, from
`action_executions`. An analyst told to phone the cardholder when the system
already sent a step-up that the cardholder abandoned is being told to do the
second-best thing, and that row exists now.

The first chip is the one with the constraint on it. It quotes the score, so
`CopilotAnswer` refuses to be built unless every mitigating signal and every
applied veto is cited — enforced in the model rather than here, so a second
caller cannot get it wrong in a new way.
"""
from __future__ import annotations

from decimal import Decimal

from ..contract.explanation import CopilotAnswer, CopilotResponse
from .evidence import ALLOWED_RELATIONS, AlertEvidence, Quoter, signed


def _cite_signal(q: Quoter, alert_id: int, signal) -> None:
    """A signal contributes two citations: its number and its sentence.

    The sentence is cited too because it is STORED TEXT carried verbatim — "New
    device fingerprint, first seen 6 minutes ago" contains a 6 that the copilot
    did not compute and must not be thought to have composed. Citing the whole
    string is what lets test_explain.py hold every number in the output to a
    source without exempting prose, which would have been the loophole.
    """
    key = f"alert_signals.alert_id={alert_id},rank={signal.rank}"
    q.q(f"contribution: {signal.feature_key}", "alert_signals", key,
        signal.contribution)
    q.q(f"signal text: {signal.feature_key}", "alert_signals", key,
        signal.human_text)


def answer_chips(evidence: AlertEvidence) -> CopilotResponse:
    return CopilotResponse(
        alert_id=evidence.alert.alert_id,
        subject_type=evidence.alert.subject.type,
        subject_id=evidence.alert.subject.id,
        answers=[_why(evidence), _clear(evidence), _first(evidence)],
        reads=list(ALLOWED_RELATIONS),
    )


def _why(ev: AlertEvidence) -> CopilotAnswer:
    """Why was this flagged. The one that quotes the score."""
    alert, q = ev.alert, Quoter()
    lines: list[str] = []

    score = q.q("score", "alerts", f"alerts.alert_id={alert.alert_id}", alert.score)
    band = q.q("band", "alerts", f"alerts.alert_id={alert.alert_id}", alert.band)
    subject = f"{alert.subject.type} {alert.subject.id}"

    lines.append(f"{subject} scored {score} ({band}). The score is the sum of the "
                 f"signals below and nothing else.")

    if ev.aggravating:
        total = sum((s.contribution for s in ev.aggravating), Decimal(0))
        n = q.derive("aggravating signal count",
                     "count(alert_signals where direction='aggravating')",
                     len(ev.aggravating))
        subtotal = q.derive("aggravating subtotal",
                            " + ".join(signed(s.contribution) for s in ev.aggravating),
                            total)
        lines.append(f"{n} aggravating signal(s), totalling {subtotal}:")
        for s in ev.aggravating:
            _cite_signal(q, alert.alert_id, s)
            lines.append(f"  {signed(s.contribution)}  {s.human_text}"
                         f"  [{s.reason_code or 'no reason code'}]")

    # Mitigators are not an appendix. They are cited unconditionally, and the
    # model refuses the answer if any is missing — see CopilotAnswer._mitigators.
    mitigating_cited = 0
    if ev.mitigating:
        total = sum((s.contribution for s in ev.mitigating), Decimal(0))
        n = q.derive("mitigating signal count",
                     "count(alert_signals where direction='mitigating')",
                     len(ev.mitigating))
        subtotal = q.derive("mitigating subtotal",
                            " + ".join(signed(s.contribution) for s in ev.mitigating),
                            total)
        lines.append(f"{n} mitigating signal(s), totalling {subtotal} — evidence "
                     f"AGAINST acting, and already deducted from the score above:")
        for s in ev.mitigating:
            _cite_signal(q, alert.alert_id, s)
            mitigating_cited += 1
            lines.append(f"  {signed(s.contribution)}  {s.human_text}"
                         f"  [{s.reason_code or 'no reason code'}]")
    else:
        lines.append("No mitigating signal fired on this case.")

    veto_cited = 0
    for s in ev.vetoes:
        q.q("veto", "alert_signals",
            f"alert_signals.alert_id={alert.alert_id},rank={s.rank}", s.human_text)
        veto_cited += 1
        lines.append(f"VETO: {s.human_text}")

    if alert.action.vetoed_by:
        rule = q.q("vetoed by", "decisions",
                   f"decisions.decision_id={alert.decision_id}",
                   alert.action.vetoed_by)
        taken = q.q("action taken", "decisions",
                    f"decisions.decision_id={alert.decision_id}",
                    alert.action.taken)
        lines.append(f"The action was capped at {taken} by {rule}: exonerating "
                     f"evidence was established, so severity was held back even "
                     f"though the score stands.")

    if alert.evidence.degraded_features:
        names = q.q("degraded features", "decisions",
                    f"decisions.decision_id={alert.decision_id}",
                    ", ".join(alert.evidence.degraded_features))
        lines.append(f"Evidence was missing or stale for: {names}. The score was "
                     f"assembled from partial evidence.")

    return CopilotAnswer(
        chip="why_flagged",
        question="Why was this flagged?",
        lines=lines, citations=q.citations,
        score_quoted=alert.score,
        signals_total=len(alert.signals),
        mitigating_total=len(ev.mitigating), mitigating_cited=mitigating_cited,
        veto_total=len(ev.vetoes), veto_cited=veto_cited,
    )


def _clear(ev: AlertEvidence) -> CopilotAnswer:
    """What would clear it. Counterfactuals, one per rule that fired."""
    alert, q = ev.alert, Quoter()
    lines: list[str] = []

    for rule_id in alert.rules_fired:
        rule = ev.rules.get(rule_id)
        if not rule:
            continue
        name = q.q("rule", "rule_definitions", f"rule_definitions.rule_id={rule_id}",
                   rule_id)
        if rule["clear_text"]:
            # Cited whole: this is the rule author's sentence, not the copilot's,
            # and any number inside it belongs to whoever wrote the rule.
            text = q.q(f"clear_text: {rule_id}", "rule_definitions",
                       f"rule_definitions.rule_id={rule_id}", rule["clear_text"])
            lines.append(f"{name}: {text}")
        else:
            lines.append(f"{name} has no stored counterfactual. Nothing is "
                         f"asserted about what would clear it.")

    if ev.mitigating:
        lines.append("Already working in this customer's favour, and already "
                     "counted:")
        for s in ev.mitigating:
            _cite_signal(q, alert.alert_id, s)
            lines.append(f"  {signed(s.contribution)}  {s.human_text}")

    if not lines:
        lines.append("No rule on this case stores a counterfactual.")

    # score_quoted stays None: a counterfactual is not a claim about the
    # arithmetic, so constraint 3 does not bind here — and pretending it does
    # would make the constraint decorative.
    return CopilotAnswer(
        chip="what_would_clear_it",
        question="What would clear it?",
        lines=lines, citations=q.citations,
        signals_total=len(alert.signals),
        mitigating_total=len(ev.mitigating), veto_total=len(ev.vetoes),
    )


def _first(ev: AlertEvidence) -> CopilotAnswer:
    """What should I do first. The recommendation, plus what already happened."""
    alert, q = ev.alert, Quoter()
    lines: list[str] = []

    source = alert.action.source_rule
    taken = q.q("action taken", "decisions",
                f"decisions.decision_id={alert.decision_id}", alert.action.taken)
    if source:
        rule = q.q("action source rule", "decisions",
                   f"decisions.decision_id={alert.decision_id}", source)
        lines.append(f"The system took {taken}, authorised by {rule}.")
    else:
        lines.append(f"The system took {taken}. No rule carried the action.")

    # The veto has to be named HERE and not only under "why was this flagged".
    # `recommended_action_text` belongs to the rule that carried the SEVERITY,
    # and a veto caps what that severity is allowed to become — so quoting the
    # recommendation on its own would advise an analyst to do the thing the
    # system deliberately declined to do, in the voice of the system.
    if alert.action.vetoed_by:
        vetoed_by = q.q("vetoed by", "decisions",
                        f"decisions.decision_id={alert.decision_id}",
                        alert.action.vetoed_by)
        lines.append(f"Read the recommendation below with that in mind: it "
                     f"belongs to {source or 'the rule that scored highest'}, "
                     f"whose severity was CAPPED at {taken} by {vetoed_by}. It "
                     f"describes what that rule wanted, not what was done.")

    if alert.action.recommended_text:
        lines.append(q.q(f"recommended_action_text: {source}", "rule_definitions",
                         f"rule_definitions.rule_id={source}",
                         alert.action.recommended_text))
    else:
        lines.append("No stored recommendation for the rule that carried the "
                     "action.")

    if alert.action.prevent_threshold_met is False and alert.action.taken != "allow":
        lines.append("The preventive threshold was NOT met, so nothing "
                     "preventive was authorised regardless of the score.")

    # §8's row, which is the whole reason §13 had to follow it.
    if ev.executions:
        n = q.derive("executions issued", "count(action_executions for this alert)",
                     len(ev.executions))
        lines.append(f"{n} action(s) have already been issued on this case:")
        for x in ev.executions:
            outcome = q.q(f"execution {x.execution_id} outcome", "action_executions",
                          f"action_executions.execution_id={x.execution_id}",
                          x.outcome or "unresolved")
            channel = x.channel or "no channel"
            flag = " (synthetic outcome)" if x.synthetic else ""
            lines.append(f"  {x.action} via {channel} -> {outcome}{flag}")
        if any(x.synthetic for x in ev.executions):
            lines.append("Outcomes marked synthetic were settled by a script "
                         "against the planted label, not by a customer or an "
                         "analyst. Do not read them as observed behaviour.")
    else:
        lines.append("Nothing has been issued on this case yet.")

    return CopilotAnswer(
        chip="what_should_i_do_first",
        question="What should I do first?",
        lines=lines, citations=q.citations,
        signals_total=len(alert.signals),
        mitigating_total=len(ev.mitigating), veto_total=len(ev.vetoes),
    )
