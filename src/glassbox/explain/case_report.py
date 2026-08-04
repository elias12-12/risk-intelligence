"""The case report (§13, constraint 4) — a filing draft, and it says so.

§13: "The case report cites every signal, its contribution, its `value_as_of`,
the rule version set, and any degraded features. It is a draft; analyst review
before filing is stated on the artifact."

Every one of those is here, and the draft notice is inside the markdown rather
than beside it — `CaseReport` refuses to be built otherwise. A banner that lives
in a sibling field is a banner that disappears the first time somebody copies the
body out.

Two things this report says that a generated document usually does not:

  * `value_as_of` beside every signal. Without it the audit answer depends on
    re-querying a feature store that may have moved since, which is exactly the
    failure §4 exists to prevent. `alert_signals` carries it precisely so the
    report does not have to go and look.

  * whether `rule_version_set` and `feature_version_set` actually RESOLVE.
    Through Week 4 they did not — the version stores were empty — and the report
    said so rather than printing "R-114 v1" and letting the number imply a stored
    definition. Session 3's publish path fills them, so the report now asks
    (`evidence._unresolved_versions`) and reports the answer either way. The
    claim is checked in both directions, which is the only version of it worth
    printing.

`generated_from` is the decision's `decided_at`, not `now()`. Two reports of the
same case must be identical, and a timestamp that moves makes a diff useless for
the one job an audit artifact has.
"""
from __future__ import annotations

from decimal import Decimal

from ..contract.explanation import DRAFT_NOTICE, CaseReport
from .evidence import AlertEvidence, Quoter, signed


def build_report(evidence: AlertEvidence) -> CaseReport:
    alert, ev, q = evidence.alert, evidence, Quoter()
    out: list[str] = []

    def line(text: str = "") -> None:
        out.append(text)

    line(f"# Case {alert.alert_id} — {alert.title}")
    line()
    line(f"> {DRAFT_NOTICE}")
    line()

    # ---------------------------------------------------------------- subject
    line("## Subject")
    line()
    line(f"- **{alert.subject.type}** `{alert.subject.id}`")
    # alert_subjects carries the primary subject too, with role 'primary'. Listing
    # it twice reads as two entities on the case, which for a ring alert is
    # exactly the thing a reader is counting.
    for s in alert.subjects:
        if (s.type, s.id) == (alert.subject.type, alert.subject.id):
            continue
        role = f" — {s.role}" if s.role else ""
        line(f"- {s.type} `{s.id}`{role}")
    line()

    # ---------------------------------------------------------------- decision
    score = q.q("score", "alerts", f"alerts.alert_id={alert.alert_id}", alert.score)
    band = q.q("band", "alerts", f"alerts.alert_id={alert.alert_id}", alert.band)
    taken = q.q("action taken", "decisions",
                f"decisions.decision_id={alert.decision_id}", alert.action.taken)
    line("## Decision")
    line()
    line(f"- Score **{score}**, band **{band}**")
    line(f"- Action **{taken}**"
         + (f", authorised by **{alert.action.source_rule}**"
            if alert.action.source_rule else ", no rule carried the action"))
    if alert.action.vetoed_by:
        line(f"- Severity capped by veto rule **{alert.action.vetoed_by}**")
    if alert.action.prevent_threshold_met is not None:
        line(f"- Preventive threshold met: "
             f"{'yes' if alert.action.prevent_threshold_met else 'no'}")
    line(f"- Evaluation `{alert.evidence.evaluation_id}`, "
         f"trigger `{alert.evidence.trigger_id}`")
    line(f"- Point-in-time bound `{alert.evidence.pit_bound_at}`")
    line()

    # ---------------------------------------------------------------- signals
    line("## Signals")
    line()
    line("Every signal the decision used, with the value it actually saw and when "
         "that value was true. The score is their sum and nothing else.")
    line()
    line("| # | signal | points | value | value as of | asserted by |")
    line("|---|---|---:|---|---|---|")
    for s in alert.signals:
        points = q.q(f"contribution: {s.feature_key}", "alert_signals",
                     f"alert_signals.alert_id={alert.alert_id},rank={s.rank}",
                     s.contribution)
        value = q.q(f"feature value: {s.feature_key}", "alert_signals",
                    f"alert_signals.alert_id={alert.alert_id},rank={s.rank}",
                    s.feature_value if s.feature_value is not None else "—")
        as_of = q.q(f"value_as_of: {s.feature_key}", "alert_signals",
                    f"alert_signals.alert_id={alert.alert_id},rank={s.rank}",
                    s.value_as_of.isoformat() if s.value_as_of else "—")
        claimants = ", ".join(s.asserted_by_rules) or (s.source_rule_id or "—")
        line(f"| {s.rank} | {s.human_text} | {signed(s.contribution)} | {value} "
             f"| {as_of} | {claimants} |")
        _ = points

    total = sum((x.contribution for x in alert.signals), Decimal(0))
    summed = q.derive("signal total",
                      " + ".join(signed(x.contribution) for x in alert.signals)
                      or "empty pool",
                      total)
    line()
    line(f"Sum of signals: **{summed}**, which equals the score. That equality is "
         f"enforced on the server and in the database, not asserted here.")
    if not alert.signals:
        line()
        line("This case has no signals: the pool was dropped because the "
             "mitigating evidence consumed the accusation. See "
             "`engine/consolidate.py`.")
    line()

    # ---------------------------------------------------------------- evidence
    line("## Evidence quality")
    line()
    if alert.evidence.degraded_features:
        names = q.q("degraded features", "decisions",
                    f"decisions.decision_id={alert.decision_id}",
                    ", ".join(alert.evidence.degraded_features))
        line(f"- **Degraded**: {names}. This decision was made on partial "
             f"evidence, and a reviewer should weigh it as such.")
    else:
        line("- No feature was missing or stale at decision time.")
    line(f"- Rule versions: "
         + ", ".join(f"`{k}` v{v}" for k, v in
                     sorted(alert.evidence.rule_version_set.items())) or "- none")
    line(f"- Feature versions: "
         + ", ".join(f"`{k}` v{v}" for k, v in
                     sorted(alert.evidence.feature_version_set.items())) or "- none")

    unresolvable = list(ev.unresolved_versions)
    if unresolvable:
        line()
        line("> The following recorded versions **do not resolve** — no stored "
             "definition sits behind them: " + ", ".join(unresolvable) + ". "
             "This is an audit gap, not a claim that the definitions were "
             "retrieved.")
    else:
        line()
        line("> Every version above **resolves**: the definition in force at "
             "decision time is stored in `rule_versions` / "
             "`feature_catalog_versions` and can be retrieved as it was, not as "
             "it is now.")
    line()

    # ---------------------------------------------------------------- actions
    line("## Actions taken")
    line()
    if ev.executions:
        line("| action | channel | issued | outcome | source | synthetic |")
        line("|---|---|---|---|---|---|")
        for x in ev.executions:
            outcome = q.q(f"execution {x.execution_id} outcome", "action_executions",
                          f"action_executions.execution_id={x.execution_id}",
                          x.outcome or "unresolved")
            line(f"| {x.action} | {x.channel or '—'} | {x.issued_at.isoformat()} "
                 f"| {outcome} | {x.outcome_source or '—'} "
                 f"| {'yes' if x.synthetic else 'no'} |")
        if any(x.synthetic for x in ev.executions):
            line()
            line("Outcomes marked synthetic were settled by "
                 "`scripts/resolve_actions.py` against the planted label. They are "
                 "not observed customer behaviour and must not be reported as a "
                 "measured pass rate.")
    else:
        line("Nothing was issued on this case.")
    line()

    # ---------------------------------------------------------------- footer
    line("## What this report is not")
    line()
    line("It is assembled by deterministic templating from the rows behind this "
         "one case. No language model produced any part of it, nothing outside "
         "this case was consulted, and no number in it was recalculated — every "
         "figure is quoted from the citation list published alongside.")

    return CaseReport(
        alert_id=alert.alert_id,
        title=alert.title,
        subject_type=alert.subject.type,
        subject_id=alert.subject.id,
        generated_from=alert.decided_at,
        markdown="\n".join(out),
        citations=q.citations,
        unresolvable_versions=unresolvable,
    )
