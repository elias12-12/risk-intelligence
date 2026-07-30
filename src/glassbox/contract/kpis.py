"""kpis.v1 — the nine tiles, computed from stored rows (§11).

A THIRD SIBLING of alert.v1, alongside queue.v1 and executions.v1. Nothing in
models.py is touched: `Subject`, `Signal`, `Action` and `Evidence` live inside
alert.v1's `$defs` closure, so adding a field to any of them for a tile's benefit
would change the frozen file's bytes and break its pinned digest.

§11 is not a reporting layer bolted on at the end. Every tile had a prerequisite
and shipping the tile first meant shipping a number wrong in a way nobody could
see. All nine prerequisites now exist, and each tile below names the one it
needed.

FOUR RULES, EACH OF WHICH IS A §11 REQUIREMENT AND NOT A STYLE CHOICE
--------------------------------------------------------------------

1. EVERY TILE NAMES ITS WINDOW. `window_start` and `window_end` are on every
   tile, not just on the set, because a tile that travels without its window is a
   number somebody will compare against a different one.

2. EVERY RATE PUBLISHES ITS NUMERATOR AND DENOMINATOR. §8's denominators here are
   single digits — four challenges, two holds — and a bare "0% prevention false
   positive rate" reads as a result when it is a sample size.

3. A DELTA EXISTS ONLY AGAINST A REAL PRIOR WINDOW. The baseline is always the
   immediately preceding window of the same length, never a parameter a caller
   can pick to flatter a number. When the dataset does not reach back that far —
   which it does not for any window over ~15 days, since it spans 30 — the
   baseline is None and the delta is None. §11: "a delta with no baseline is the
   one kind of KPI that is worse than no KPI."

4. NOTHING ASSERTS A CAPABILITY THE SYSTEM DOES NOT HAVE. `synthetic` and
   `caveat` are on the wire. The false-negative rate is exact here because we
   planted the fraud, and meaningless anywhere else; the challenge outcomes were
   settled by a script, not a customer; and `fail_mode` holds the lane's POLICY,
   never an observed failure, because nothing real has run.

WINDOWS ARE MEASURED ON EVENT TIME — `decisions.occurred_at` and
`alerts.first_event_at` — never on `decided_at`, `created_at` or `now()`. Those
are wall clock, and a tile windowed on them reports a replay of January as
belonging to today. Migration 0023 and §W3.6 of the handoff both say so; this is
the third place it matters.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import psycopg
from pydantic import BaseModel, ConfigDict, Field

from ..config import reference_now
from ..db import fetch_all, fetch_one

STRICT = ConfigDict(extra="forbid", frozen=True)

DEFAULT_WINDOW = timedelta(days=7)

SYNTHETIC_GROUND_TRUTH = (
    "Measured against transactions.synthetic_label — fraud this repository "
    "planted. Exact on this dataset and meaningless beyond it. In production "
    "recall is not measurable without a random-sample audit of unalerted traffic."
)
SYNTHETIC_OUTCOMES = (
    "Challenge outcomes were settled by scripts/resolve_actions.py against the "
    "synthetic label, not by a customer. Every source row is stamped synthetic."
)
FAIL_MODE_IS_POLICY = (
    "fail_mode records the lane's POLICY (open for inline, null for async), not "
    "an observed failure: nothing has failed because nothing real has run. This "
    "is not a measured resilience number."
)


class KpiPart(BaseModel):
    """One component of a tile, with its own denominator.

    Parts exist because three of the nine tiles are not scalars — a score
    distribution, a per-rule precision and a reason-code trend are each a set of
    numbers — and because §11's "block / challenge / fail-open rate" is one row
    covering three rates whose denominators differ. Sharing a tile's denominator
    across them would be the exact error rule 2 exists to prevent.
    """
    model_config = STRICT

    label: str
    value: Decimal | None = None
    unit: str = "count"
    numerator: int | None = None
    denominator: int | None = None
    baseline_value: Decimal | None = None
    delta_pct: Decimal | None = None


class KpiTile(BaseModel):
    model_config = STRICT

    key: str
    label: str
    value: Decimal | None = None
    unit: str                                    # count | percent | seconds
    numerator: int | None = None
    denominator: int | None = None

    window_start: datetime
    window_end: datetime
    baseline_start: datetime | None = None
    baseline_end: datetime | None = None
    baseline_value: Decimal | None = None
    delta_pct: Decimal | None = None

    basis: str                                   # how it was derived, in words
    requires: str                                # the §-item that made it possible
    synthetic: bool = False
    caveat: str | None = None
    parts: list[KpiPart] = Field(default_factory=list)


class KpiSet(BaseModel):
    model_config = STRICT

    as_of: datetime
    window_start: datetime
    window_end: datetime
    baseline_start: datetime | None = None
    baseline_end: datetime | None = None
    # False when the dataset does not reach back a full window before
    # window_start. Every delta on every tile is then None, and the client is
    # told why rather than left to infer it from a field that is simply missing.
    baseline_available: bool
    baseline_absent_reason: str | None = None
    tiles: list[KpiTile]


# ---------------------------------------------------------------- arithmetic
def _rate(numerator: int | None, denominator: int | None) -> Decimal | None:
    """A percentage, or None. Never 0.0 for an empty denominator — that reads as
    a measured zero and is an absence of measurement."""
    if not denominator:
        return None
    return (Decimal(numerator or 0) * 100 / Decimal(denominator)).quantize(Decimal("0.01"))


def _delta(current: Decimal | None, baseline: Decimal | None) -> Decimal | None:
    """Percentage change. None whenever the comparison would be invented.

    A baseline of zero is the case worth naming: every change from nothing is an
    infinite increase, and rendering "+∞%" or silently "+100%" would both be
    claims the data does not make.
    """
    if current is None or baseline is None or baseline == 0:
        return None
    return ((current - baseline) / abs(baseline) * 100).quantize(Decimal("0.1"))


def _q(value) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value)).quantize(Decimal("0.01"))


# ---------------------------------------------------------------- the tiles
def read_kpis(conn: psycopg.Connection, as_of: datetime | None = None,
              window: timedelta = DEFAULT_WINDOW) -> KpiSet:
    as_of = as_of or reference_now()
    w_start, w_end = as_of - window, as_of
    b_start, b_end = w_start - window, w_start

    earliest = fetch_one(conn, "SELECT min(event_at) AS t FROM v_kpi_decisions")["t"]
    baseline_ok = earliest is not None and earliest <= b_start
    reason = None if baseline_ok else (
        f"the dataset begins at {earliest.isoformat() if earliest else 'no data'}, "
        f"which is after the start of the preceding {window.days}-day window "
        f"({b_start.isoformat()}). §11: a delta with no baseline is worse than no "
        f"delta, so every delta in this set is null."
    )

    p = {"w_start": w_start, "w_end": w_end,
         "b_start": b_start if baseline_ok else None,
         "b_end": b_end if baseline_ok else None}

    def tile(**kw) -> KpiTile:
        kw.setdefault("window_start", w_start)
        kw.setdefault("window_end", w_end)
        if baseline_ok:
            kw.setdefault("baseline_start", b_start)
            kw.setdefault("baseline_end", b_end)
        return KpiTile(**kw)

    tiles = [
        _alert_volume(conn, p, tile),
        _score_distribution(conn, p, tile),
        _false_positive_rate(conn, p, tile),
        _false_negative_rate(conn, p, tile),
        _validation_outcomes(conn, p, tile),
        _median_triage_time(conn, p, tile),
        _action_rates(conn, p, tile),
        _rule_precision(conn, p, tile),
        _emerging_trends(conn, p, tile),
    ]

    return KpiSet(
        as_of=as_of, window_start=w_start, window_end=w_end,
        baseline_start=b_start if baseline_ok else None,
        baseline_end=b_end if baseline_ok else None,
        baseline_available=baseline_ok, baseline_absent_reason=reason,
        tiles=tiles,
    )


_VOLUME_SQL = """
SELECT count(*) FILTER (WHERE became_case)                       AS cases,
       count(*)                                                  AS evaluated,
       count(*) FILTER (WHERE alert_routing = 'raised')          AS raised,
       count(*) FILTER (WHERE alert_routing = 'restated')        AS restated,
       count(*) FILTER (WHERE alert_routing = 'folded')          AS folded,
       count(*) FILTER (WHERE alert_routing = 'suppressed')      AS suppressed,
       count(*) FILTER (WHERE alert_routing = 'no_authority')    AS no_authority
  FROM v_kpi_decisions
 WHERE event_at > %(start)s AND event_at <= %(end)s
"""


def _alert_volume(conn, p, tile) -> KpiTile:
    now = fetch_one(conn, _VOLUME_SQL, {"start": p["w_start"], "end": p["w_end"]})
    before = (fetch_one(conn, _VOLUME_SQL, {"start": p["b_start"], "end": p["b_end"]})
              if p["b_start"] else None)
    baseline = Decimal(before["cases"]) if before else None
    return tile(
        key="alert_volume", label="Alert volume", unit="count",
        value=Decimal(now["cases"]), numerator=now["cases"],
        denominator=now["evaluated"],
        baseline_value=baseline, delta_pct=_delta(Decimal(now["cases"]), baseline),
        basis="cases raised or restated, over every decision evaluated. A folded "
              "evaluation is the same case seen again and is not counted twice.",
        requires="§9 dedup + §10 population scoring; the denominator is "
                 "decisions.alert_routing, which did not exist before 0023",
        parts=[KpiPart(label=k, value=Decimal(now[k]), numerator=now[k],
                       denominator=now["evaluated"])
               for k in ("raised", "restated", "folded", "suppressed",
                         "no_authority")],
    )


_DIST_SQL = """
SELECT subject_type, band,
       count(*)                              AS n,
       count(*) FILTER (WHERE score > 0)     AS scoring
  FROM v_kpi_decisions
 WHERE event_at > %(start)s AND event_at <= %(end)s
 GROUP BY 1, 2
 ORDER BY 1, 2
"""


def _score_distribution(conn, p, tile) -> KpiTile:
    rows = fetch_all(conn, _DIST_SQL, {"start": p["w_start"], "end": p["w_end"]})
    total = sum(r["n"] for r in rows)
    scoring = sum(r["scoring"] for r in rows)
    return tile(
        key="score_distribution", label="Score distribution", unit="count",
        value=Decimal(scoring), numerator=scoring, denominator=total,
        basis="decisions by subject type and band, from the cutoffs calibrated "
              "per subject type in 0027. Only `transaction` is calibrated; the "
              "others carry an inherited cutoff and say so in score_bands.basis.",
        requires="§10 population scoring, and per-subject-type bands",
        parts=[KpiPart(label=f"{r['subject_type']}:{r['band']}",
                       value=Decimal(r["n"]), numerator=r["n"], denominator=total)
               for r in rows],
    )


_FP_SQL = """
SELECT count(*) FILTER (WHERE disposition IS NOT NULL)     AS dispositioned,
       count(*) FILTER (WHERE is_false_positive)           AS false_positives,
       count(*) FILTER (WHERE is_true_positive)            AS true_positives,
       count(*) FILTER (WHERE disposition = 'inconclusive') AS inconclusive,
       count(*)                                            AS cases
  FROM v_kpi_cases
 WHERE event_at > %(start)s AND event_at <= %(end)s
"""


def _false_positive_rate(conn, p, tile) -> KpiTile:
    now = fetch_one(conn, _FP_SQL, {"start": p["w_start"], "end": p["w_end"]})
    before = (fetch_one(conn, _FP_SQL, {"start": p["b_start"], "end": p["b_end"]})
              if p["b_start"] else None)
    value = _rate(now["false_positives"], now["dispositioned"])
    baseline = _rate(before["false_positives"], before["dispositioned"]) if before else None
    return tile(
        key="false_positive_rate", label="False-positive rate", unit="percent",
        value=value, numerator=now["false_positives"],
        denominator=now["dispositioned"],
        baseline_value=baseline, delta_pct=_delta(value, baseline),
        basis="cases dispositioned false_positive or confirmed_legit, over cases "
              "with any disposition. `inconclusive` is neither and is excluded "
              "from both sides rather than folded into one.",
        requires="§8 — for preventive actions this also needs execution outcomes, "
                 "which is the prevention_false_positive part of `action_rates`",
        caveat=None if now["dispositioned"] else
        "no case in this window has been dispositioned, so there is no rate",
    )


_FN_SQL = """
SELECT count(*) FILTER (WHERE is_fraud)                      AS fraud,
       count(*) FILTER (WHERE is_fraud AND became_case)      AS caught,
       count(*) FILTER (WHERE is_labelled)                   AS labelled
  FROM v_kpi_decisions
 WHERE event_at > %(start)s AND event_at <= %(end)s
"""


def _false_negative_rate(conn, p, tile) -> KpiTile:
    now = fetch_one(conn, _FN_SQL, {"start": p["w_start"], "end": p["w_end"]})
    before = (fetch_one(conn, _FN_SQL, {"start": p["b_start"], "end": p["b_end"]})
              if p["b_start"] else None)
    missed = now["fraud"] - now["caught"]
    value = _rate(missed, now["fraud"])
    baseline = (_rate(before["fraud"] - before["caught"], before["fraud"])
                if before else None)
    return tile(
        key="false_negative_rate", label="False-negative rate", unit="percent",
        value=value, numerator=missed, denominator=now["fraud"],
        baseline_value=baseline, delta_pct=_delta(value, baseline),
        basis="labelled-fraud decisions that never reached a case, over all "
              "labelled-fraud decisions in the window.",
        requires="synthetic ground truth from the generator — the only reason "
                 "this tile is computable at all",
        synthetic=True, caveat=SYNTHETIC_GROUND_TRUTH,
    )


_OUTCOMES_SQL = """
SELECT COALESCE(disposition, 'undispositioned') AS disposition, count(*) AS n
  FROM v_kpi_cases
 WHERE event_at > %(start)s AND event_at <= %(end)s
 GROUP BY 1 ORDER BY 1
"""


def _validation_outcomes(conn, p, tile) -> KpiTile:
    rows = fetch_all(conn, _OUTCOMES_SQL, {"start": p["w_start"], "end": p["w_end"]})
    total = sum(r["n"] for r in rows)
    settled = sum(r["n"] for r in rows if r["disposition"] != "undispositioned")
    return tile(
        key="validation_outcomes", label="Validation outcomes", unit="count",
        value=Decimal(settled), numerator=settled, denominator=total,
        basis="analyst dispositions per case, deduplicated to one verdict per "
              "case — case_outcomes has no uniqueness constraint, and joining it "
              "raw fans every denominator out by the number of dispositions.",
        requires="§8 case_outcomes — BUILT since Week 1",
        parts=[KpiPart(label=r["disposition"], value=Decimal(r["n"]),
                       numerator=r["n"], denominator=total) for r in rows],
        caveat="On this dataset every disposition was written by "
               "scripts/resolve_actions.py, not by an analyst.",
    )


_TRIAGE_SQL = """
SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY triage_seconds) AS median,
       percentile_cont(0.9) WITHIN GROUP (ORDER BY triage_seconds) AS p90,
       count(*) FILTER (WHERE triage_seconds IS NOT NULL)          AS settled,
       count(*)                                                    AS cases
  FROM v_kpi_cases
 WHERE event_at > %(start)s AND event_at <= %(end)s
"""


def _median_triage_time(conn, p, tile) -> KpiTile:
    now = fetch_one(conn, _TRIAGE_SQL, {"start": p["w_start"], "end": p["w_end"]})
    before = (fetch_one(conn, _TRIAGE_SQL, {"start": p["b_start"], "end": p["b_end"]})
              if p["b_start"] else None)
    value = _q(now["median"])
    baseline = _q(before["median"]) if before else None
    return tile(
        key="median_triage_time", label="Median triage time", unit="seconds",
        value=value, numerator=now["settled"], denominator=now["cases"],
        baseline_value=baseline, delta_pct=_delta(value, baseline),
        basis="alerts.first_event_at -> case_outcomes.decided_at. Measured on "
              "EVENT time: alerts.created_at is DEFAULT now(), so a triage time "
              "measured on it looks plausible and is wrong for every replay.",
        requires="§12 — case_outcomes.decided_at is written explicitly rather "
                 "than defaulted, which is what makes this computable",
        parts=[KpiPart(label="p90", value=_q(now["p90"]), unit="seconds",
                       numerator=now["settled"], denominator=now["cases"])],
        caveat="Every case here was settled in one synthetic pass, so these "
               "durations are a property of the generator's clock and describe "
               "no analyst's working day.",
    )


_EXEC_SQL = """
SELECT count(*) FILTER (WHERE is_preventive)                        AS preventive,
       count(*) FILTER (WHERE action = 'challenge')                 AS challenges,
       count(*) FILTER (WHERE action = 'challenge' AND outcome = 'passed')    AS passed,
       count(*) FILTER (WHERE action = 'challenge' AND outcome = 'failed')    AS failed,
       count(*) FILTER (WHERE action = 'challenge' AND outcome = 'abandoned') AS abandoned,
       count(*) FILTER (WHERE action = 'hold')                      AS holds,
       count(*) FILTER (WHERE action = 'block')                     AS blocks,
       count(*) FILTER (WHERE action = 'notify')                    AS notifications,
       count(*) FILTER (WHERE is_prevention_false_positive)         AS prevention_fp,
       count(*) FILTER (WHERE is_prevention_true_positive)          AS prevention_tp,
       bool_or(synthetic)                                           AS synthetic
  FROM v_kpi_executions
 WHERE event_at > %(start)s AND event_at <= %(end)s
"""

_FAILMODE_SQL = """
SELECT count(*) FILTER (WHERE fail_mode = 'open') AS fail_open,
       count(*)                                   AS evaluated
  FROM v_kpi_decisions
 WHERE event_at > %(start)s AND event_at <= %(end)s
"""


def _action_rates(conn, p, tile) -> KpiTile:
    now = fetch_one(conn, _EXEC_SQL, {"start": p["w_start"], "end": p["w_end"]})
    fm = fetch_one(conn, _FAILMODE_SQL, {"start": p["w_start"], "end": p["w_end"]})
    before = (fetch_one(conn, _EXEC_SQL, {"start": p["b_start"], "end": p["b_end"]})
              if p["b_start"] else None)

    value = _rate(now["preventive"], fm["evaluated"])
    baseline = None
    if before:
        fmb = fetch_one(conn, _FAILMODE_SQL,
                        {"start": p["b_start"], "end": p["b_end"]})
        baseline = _rate(before["preventive"], fmb["evaluated"])

    return tile(
        key="action_rates", label="Block / challenge / fail-open", unit="percent",
        value=value, numerator=now["preventive"], denominator=fm["evaluated"],
        baseline_value=baseline, delta_pct=_delta(value, baseline),
        basis="counted off ACTION EXECUTIONS, never off decisions. §9 issues a "
              "preventive action when a case is raised and not when it folds, so "
              "off decisions the rate would be several times the number of "
              "customers actually affected.",
        requires="§8 action_executions — a table nothing wrote until Week 3",
        synthetic=bool(now["synthetic"]),
        caveat=SYNTHETIC_OUTCOMES + " " + FAIL_MODE_IS_POLICY,
        parts=[
            KpiPart(label="challenge pass rate", unit="percent",
                    value=_rate(now["passed"], now["challenges"]),
                    numerator=now["passed"], denominator=now["challenges"]),
            KpiPart(label="challenge abandon rate", unit="percent",
                    value=_rate(now["abandoned"], now["challenges"]),
                    numerator=now["abandoned"], denominator=now["challenges"]),
            KpiPart(label="holds issued", value=Decimal(now["holds"]),
                    numerator=now["holds"], denominator=fm["evaluated"]),
            KpiPart(label="blocks issued", value=Decimal(now["blocks"]),
                    numerator=now["blocks"], denominator=fm["evaluated"]),
            KpiPart(label="notifications", value=Decimal(now["notifications"]),
                    numerator=now["notifications"], denominator=fm["evaluated"]),
            # The join §8 exists for. Zero here is a RESULT, not an absence: every
            # preventive action on this dataset lands on a fraud-labelled subject,
            # so no challenge passes and nothing is dispositioned legitimate.
            KpiPart(label="prevention false positives",
                    value=Decimal(now["prevention_fp"]),
                    numerator=now["prevention_fp"], denominator=now["preventive"]),
            KpiPart(label="prevention true positives",
                    value=Decimal(now["prevention_tp"]),
                    numerator=now["prevention_tp"], denominator=now["preventive"]),
            KpiPart(label="fail-open (lane policy, not observed failures)",
                    unit="percent", value=_rate(fm["fail_open"], fm["evaluated"]),
                    numerator=fm["fail_open"], denominator=fm["evaluated"]),
        ],
    )


_RULE_SQL = """
SELECT rule_id,
       count(*)                                        AS cases,
       count(*) FILTER (WHERE carried_the_action)      AS carried,
       count(*) FILTER (WHERE disposition IS NOT NULL) AS dispositioned,
       count(*) FILTER (WHERE is_true_positive)        AS true_positives
  FROM v_kpi_rule_attribution
 WHERE event_at > %(start)s AND event_at <= %(end)s
 GROUP BY 1 ORDER BY 1
"""


def _rule_precision(conn, p, tile) -> KpiTile:
    rows = fetch_all(conn, _RULE_SQL, {"start": p["w_start"], "end": p["w_end"]})
    before = {r["rule_id"]: r for r in fetch_all(
        conn, _RULE_SQL, {"start": p["b_start"], "end": p["b_end"]})} \
        if p["b_start"] else {}

    dispositioned = sum(r["dispositioned"] for r in rows)
    true_positives = sum(r["true_positives"] for r in rows)

    parts = []
    for r in rows:
        value = _rate(r["true_positives"], r["dispositioned"])
        prior = before.get(r["rule_id"])
        base = _rate(prior["true_positives"], prior["dispositioned"]) if prior else None
        parts.append(KpiPart(
            label=r["rule_id"], unit="percent", value=value,
            numerator=r["true_positives"], denominator=r["dispositioned"],
            baseline_value=base, delta_pct=_delta(value, base)))

    return tile(
        key="rule_precision", label="Per-rule precision", unit="percent",
        value=_rate(true_positives, dispositioned),
        numerator=true_positives, denominator=dispositioned,
        basis="cases a rule ASSERTED evidence on that were confirmed fraud. "
              "Attribution survives consolidation only because "
              "alert_signals.asserted_by_rules was recorded — the responsible "
              "rule is exactly what dedup discards.",
        requires="§6 asserted_by_rules surviving dedup, plus §8 dispositions",
        caveat="A rule that asserted evidence on a case another rule CARRIED is "
               "not thereby wrong. v_kpi_rule_attribution publishes both "
               "attributions; this tile uses `asserted`.",
        parts=parts,
    )


_TREND_SQL = """
SELECT reason_code, count(*) AS n
  FROM v_kpi_reason_codes
 WHERE event_at > %(start)s AND event_at <= %(end)s
 GROUP BY 1 ORDER BY 2 DESC, 1
"""


def _emerging_trends(conn, p, tile) -> KpiTile:
    rows = fetch_all(conn, _TREND_SQL, {"start": p["w_start"], "end": p["w_end"]})
    before = {r["reason_code"]: r["n"] for r in fetch_all(
        conn, _TREND_SQL, {"start": p["b_start"], "end": p["b_end"]})} \
        if p["b_start"] else {}

    total = sum(r["n"] for r in rows)
    parts = []
    for r in rows:
        base = Decimal(before[r["reason_code"]]) if r["reason_code"] in before else None
        parts.append(KpiPart(
            label=r["reason_code"], value=Decimal(r["n"]),
            numerator=r["n"], denominator=total,
            baseline_value=base, delta_pct=_delta(Decimal(r["n"]), base)))

    return tile(
        key="emerging_trends", label="Emerging trends", unit="count",
        value=Decimal(len(rows)), numerator=len(rows), denominator=total,
        basis="distinct reason codes cited by cases in the window, one count per "
              "(case, reason code). Deltas compare against the immediately "
              "preceding window of the same length and nothing else.",
        requires="§10 population scoring plus reason-code attribution on signals",
        parts=parts,
    )
