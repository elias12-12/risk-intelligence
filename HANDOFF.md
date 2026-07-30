# Handoff — GlassBox

**Branch:** `week2-contract-engine` · **Base:** `main` @ `3c640f6`
**Status:** Week 2 complete (§§3–7, §12). Week 3 complete for §8, §9, §10 and the
sibling read contracts; §13, the `sequence` runner and the version stores are
deferred with reasons in §W3.5.
**Verified against:** PostgreSQL 16 (docker-compose), 149 tests green.

Week 3's account is first; the Week-2 account below it is unchanged and still the
authoritative record of that week's six defects.

---

# Week 3 — execution, hygiene, observability

## W3.1 What changed, in one paragraph each

**§8 — the topology no longer ends at `decisions`.** `action_executions` was a
table nothing wrote, so "block rate" counted intentions, and §7.3's argument that
prevention needs a higher threshold rested on a wrong-block cost nobody could
observe. `engine/execute.py` now issues what a decision authorised, one row per
preventive action plus a severity-routed notification, channel from
`action_routing`. `engine/outcomes.py` settles them and dispositions the cases.

**§9 — alert volume is a count of cases, not cycles.** `dedup_key` was computed
and stored and nothing folded on it: every re-run inserted a new alert. A repeat
evaluation inside the subject type's `open_window` now folds; a repeat that scores
*higher* restates the case, re-pointing it at the worse evaluation and replacing
its signal set in the same operation. Running each lane three times over a static
dataset produces 7 alerts every time, with `triggering_events` rising 7 → 14 → 21.

**§10 — the population is observable.** `alert_signals` holds the
post-consolidation survivors of the *alerted* subjects: 7 alerts, 29 signals out
of 9,923 decisions. `decision_conditions` records every condition of every
applicable rule, fired or not — 79,068 rows — which is the only possible source
for a fire rate, a mean contribution or a per-condition precision.

## W3.2 What you can run right now

```powershell
.\scripts\bootstrap.ps1        # run with the venv on PATH; ~3 min, 149 tests
```

```bash
psql "$GLASSBOX_DSN" -f db/acceptance/verify_scores.sql   # human-readable proof
python scripts/condition_report.py                       # §10's finding
python -m glassbox serve
curl http://127.0.0.1:8000/queue                         # priority-ordered
curl http://127.0.0.1:8000/alerts/4/executions           # what was done, and how it went
```

The five signed-off scores are unmoved:

| Subject | Rules | Score | Band | Action | Note |
|---|---|---:|---|---|---|
| `TXN-48291` | R-114 | **87** | high | `challenge` | |
| `TXN-48300` | R-114 + T-021 | **68** | elevated | `monitor` | R-114 wanted `challenge`; the veto held it |
| `RING-1187` | L-203 | **64** | elevated | `hold` | network subject, discovered from the link layer |
| `ACC-2201` | S-077 | **58** | elevated | `hold` | one condition resolved via the *trigger* |
| `TXN-48251` | T-021 | **31** | low | `allow` | no authority, so no alert |

Population: 9,844 transactions → 9,923 decisions, **79,068 condition-ledger rows**,
7 alerts (4 fixture, 3 background), 13 executions, 7 dispositions, 167,047 feature
values, **0 invariant failures** in either `v_alert_invariants` or
`v_decision_routing`, 310 decisions (3.1%) carrying degraded evidence.

Routing spread on a single pass: `no_authority` 9,916, `raised` 7. Every decision
says what became of it — that is the alert-volume denominator, and it did not exist
before this week.

## W3.3 The finding §10 was built to produce

```
rule    feature                        dir  priced   fired   fire%   prec%  pts/pp
T-021   country_is_new_for_customer    agg      50     398    4.04    6.78     7.4
R-114   session_geo_jump_km            agg      18     317    3.22   10.73     1.7
R-114   mcc_is_new_for_customer        agg      14     802    8.15    8.60     1.6
...
T-021   entry_mode_chip_pin            mit      -4    9562   97.14  100.00     0.0
```

`country_is_new_for_customer` is priced at **+50** — sized backwards in Week 1 so
T-021's points would sum to its displayed 31 — and earns 6.78% precision over 398
firings. That is 4.4× the cost per unit of measured precision of the next
aggravator and 34× the best-sampled one. It fires on 4.04% of the population, so
on its own it puts every genuine first trip abroad at mid-elevated unless a
mitigator happens to fire. Repricing is Week 4; the report recommends and never
writes, because silently retuned weights break the audit story.

**Precision had to be made direction-aware to get this answer.** A mitigator is
right when it fires on *legitimate* traffic. Scored by fraud rate, the first draft
of the view ranked `entry_mode_chip_pin` — 9,562 firings, never once on fraud —
as the worst condition in the catalog and buried the actual misprice at #2. Same
inversion §5 objects to when an absent mitigator is treated as a non-firing one,
and it was only visible because the report was run rather than reasoned about.

## W3.4 Decisions taken that the plan did not specify

| # | Decision | Why |
|---|---|---|
| 1 | Preventive actions are issued on a **raised** case only; notifications also on a restatement | A ring re-evaluated every 15 minutes would otherwise send 96 step-ups a day for one situation. A notification costs an analyst a glance; a step-up costs a customer. Same asymmetry as §7.3, applied to delivery. |
| 2 | The fold window is anchored on `first_event_at`, not slid along `last_event_at` | A case gets a bounded life: a ring active for a month is four weekly cases, not one immortal one, which keeps median triage time meaningful. |
| 3 | Restatement is gated on **strictly** greater | With `>=`, re-running a lane over unchanged data would delete and re-insert every alert's signals forever, churning the exact rows the sum invariant is checked against. |
| 4 | The §8 feature reads `events`, not `action_executions` | §8 says the new event types carry the customer-facing side "into the behavioral log, where the next generation of rules can read them". Reading `action_executions` would need three code changes *and* a denormalised `card_id` — for the feature offered as proof that growth costs rows. |
| 5 | `challenge_failed` is its own event type rather than an attribute | `predicate.py` takes bare column identifiers and rejects a dict value, so `attributes->>'outcome'` is unreachable by design. The right answer is vocabulary (an INSERT into `ref_event_type`), not a hole in the injection boundary. |
| 6 | Queue priority is computed from the **rounded** published factors | A client multiplying the three numbers it was given must land on the number it was given. Computing the product at full precision made the published explanation approximately true, which is the standard §1 refuses for a score bar. |
| 7 | `alert_policy` has no `fold_across_lanes` column | The seeded rules partition subject types by lane, so no subject is ever evaluated in both and the flag would have no reachable behaviour. Add it when a rule set first spans lanes. |
| 8 | `settle()` runs in the test session fixture | §8 is part of the pipeline now, so the built database should represent a complete run. Note the consequence: it dispositions every open case, and suppression only applies to *undispositioned* ones, so `test_hygiene.py` reopens one and says why. |

## W3.5 What is deliberately not done

- **§13 explanation surfaces** — the deterministic copilot and case report. Its
  first constraint is that the copilot reads `action_executions` for the alert in
  view, which was empty until this week, so it had to follow §8 regardless.
- **The `sequence` source kind.** `new_payee_then_drain` is still the one
  hand-seeded feature value, in a labelled block in the generator.
- **Batch/incremental consistency test** (§17).
- **`rule_versions` / `feature_catalog_versions` are still empty**, so a stored
  `rule_version_set` names a version number that resolves to nothing. That is an
  audit gap, not a correctness one, and it is the backend half of "admin
  authoring" — which pairs with the console, and the console is out of scope.
- **The console itself.** This week is backend only: the HTTP surfaces a console
  would bind to exist and are frozen; nothing renders them.

## W3.6 Three things to know before changing this

1. **An alert's signal set must always be exactly one decision's pool.** Update
   `alerts.score` without atomically replacing `alert_signals` and you fail
   `AlertDetail._invariants`, `v_alert_invariants`, and the API starts returning
   500s. `_restate_alert` does both or neither, and that is not refactorable into
   two steps.
2. **`alerts.created_at` is `DEFAULT now()` — wall clock.** All seven fixture
   alerts are created seconds apart, so any window or decay measured on it *looks*
   correct and is wrong for every historical replay. Use `first_event_at` /
   `last_event_at`, which carry `decisions.occurred_at`.
3. **The fire verdict is computed in exactly one place** —
   `conditions.evaluate_rule`, recorded on `ConditionOutcome`, and carried to the
   ledger. A test greps `persist.py` for `fires(`. Recomputing it where the ledger
   is written would be a second implementation of the firing rule, invisible until
   the two disagreed.

## W3.7 Where to start next

Run `.\scripts\bootstrap.ps1` first — **with the venv on PATH**, because the script
calls bare `python` and fails against a global interpreter without the deps. It
ends with 149 passing tests and prints the condition report. Then pick from below.

### First, and it is not a code change: reprice `country_is_new_for_customer`

The report names it, the evidence is in `v_condition_performance`, and Week 4 is
where §10 says the repricing belongs. **Read this before touching it:**

- It contributes to **exactly one** signed-off case — `TXN-48251`, whose 31 is
  `50 − 9 − 6 − 4`. It fires on no other fixture and appears in **no**
  `alert_signals` row, because TXN-48251 raises no alert. `TXN-48300`'s 68 is
  unaffected. So the blast radius is one number, and that number is a *demo
  narrative*, not an invariant.
- Repricing it therefore **changes a signed-off score**, which nothing else this
  week did. Drop it to anything below 19 and T-021's pool goes net-negative, the
  mitigator-only rule applies, and TXN-48251 scores **0** rather than 31 — still
  "mitigating evidence keeps it out of the queue", but a different story on screen.
  Decide which story you want before you pick the number.
- What has to move with it: `fixtures/expected_scores.json` (then re-run
  `scripts/export_expectations.py`), the tables in `README.md` and in §W3.2 and §1
  of this file, and `test_conditions_ledger.py`, which asserts the 31.
- Do it as a new seed file (`0026_reprice_*.sql`) with the report's numbers in the
  comment, not by editing `0010`. The migration ledger is append-only and the
  reason for a price belongs next to the price.

### Then, in dependency order

| Next | Why now | Blocked by |
|---|---|---|
| **Band calibration per subject type** (§10, Week 4) | `db/seeds/0018` seeds the *same* 70/45/0 for all seven subject types and says so in its own `basis` column. After consolidation those are three populations with three score ranges. The score distribution is now sittable in one query over `decisions`. | nothing |
| **The copilot and case report** (§13) | The last big user-facing piece. Its first constraint is that it reads `alert_signals`, `decisions` and `action_executions` *for the alert in view and nothing else* — all three now exist and are populated. Prototype it as deterministic templating over the stored rows; `rule_definitions.clear_text` and `recommended_action_text` are already served on `Action`. | nothing (this was the blocker, and §8 cleared it) |
| **KPI views** (§11) | Every one of the nine tiles now has its prerequisite. The two that were impossible are done: alert volume has a denominator (`decisions.alert_routing`), and block / challenge rates come from `action_executions`. Median triage time works because `case_outcomes.decided_at` is written explicitly rather than defaulted to `now()`. | band calibration, for the distribution tile |
| **The `sequence` source kind** (§3) | Deletes the last hand-seeded feature value — the `HAND_SEEDED_FEATURES` block in `generate_synthetic.py`, which is labelled `TODO(week3)`. One raise site: `features/compiler.py`, first statement of `compile_spec`. The spec can live entirely in `baseline_spec` (JSONB), so it needs no migration. | nothing |
| **Version stores** | `rule_versions` and `feature_catalog_versions` are still empty, so a stored `rule_version_set` names a number that resolves to nothing. A `publish` step that snapshots current definitions into them closes the audit gap without needing any HTTP write endpoints. | nothing |
| **The console** | Everything it binds to is built and frozen: `alert.v1`, `queue.v1`, `executions.v1`, all three generated from Pydantic models and digest- or byte-checked. Admin *write* endpoints belong with it, not before it. | nothing |

### Two things that will bite

- **Calibration must not become an automatic write.** §10 is explicit, and
  `test_condition_report.py` enforces it by scanning the report for SQL writes. A
  recommendation a human applies is the whole audit story; a weight that retunes
  itself means an analyst cannot explain why last week's identical transaction
  scored differently.
- **`alert_policy` and `score_bands` are both data, keyed on `subject_type`.**
  Calibration is an `UPDATE`, not a code change. If you find yourself editing
  Python to change a threshold, a window or a weight, something has gone in the
  wrong place.

## W3.8 Full ledger against `architecture.md`

Every numbered item of Part I and every row of Part II, with where it actually
stands. Verified against the database and the test suite, not from memory.

| # | Item | Status |
|---|---|---|
| §1 | The invariant (score *and* action explain themselves) | **DONE** — enforced in three layers: the Pydantic validator, `v_alert_invariants`, the suite |
| §3 | Feature specs, runners, subject resolution, cluster registry | **MOSTLY** — see the three carve-outs below |
| §4 | Point-in-time selection | **DONE** |
| §5 | Absent and degraded features | **DONE** |
| §6 | Consolidation | **DONE** |
| §7 | Action precedence | **DONE** |
| §8 | Action execution and outcome capture | **DONE** (Week 3) |
| §9 | Alert hygiene — dedup, suppression, prioritization | **DONE** (Week 3) |
| §10 | Population scoring **and calibration** | **HALF** — scoring and the condition report are done; no cutoff is calibrated and no condition is repriced |
| §11 | KPIs and the analytics contract | **NOT STARTED** — all nine tiles. Every prerequisite now exists |
| §12 | The read contract | **DONE**, frozen, digest-pinned, plus two siblings |
| §13 | Explanation surfaces — copilot, case report | **NOT STARTED** |
| §14 | Extension recipe as an executed test | **HALF** — card testing is a real test; **refund/return abuse is not written** |

**§3's three carve-outs.** `sequence` is unimplemented (one raise site,
`compiler.compile_spec`; one affected feature, `new_payee_then_drain`, still
hand-seeded). `expression` features — §3.1's restricted arithmetic over registered
features — were deferred in Week 2 and never picked up; nothing needs them yet.
The batch/incremental consistency test is still unwritten, though
`run_population(as_of=<historical>)` already gives the batch behaviour, so there is
no second implementation to build.

**§14 is the one to watch.** It is described in `architecture.md` as "the central
claim of the whole design", and its acceptance is *both* patterns detecting end to
end via INSERT only. Only card testing exists (`test_extension_cardtesting.py`,
with a psycopg hook that fails the test on any DDL). The refund fixtures are
already in the generator — ~120 refunds plus a planted refund-abuse customer — so
the missing half is a `feature_catalog` row, a rule, and a test in the shape of the
card-testing one. It is probably the cheapest remaining item with the highest
demo value.

### Part II — record-now columns

All of II.2 is present and populated. II.3's tables all exist. Three qualifications:

| Record | State |
|---|---|
| `feature_catalog_versions`, `rule_versions` | Tables exist, **both empty**. A stored `rule_version_set` names a version that resolves to nothing |
| `source_model` on `alert_signals` | Column exists, **never written** (0 rows). Correct for now — it is the seam for a model contributing one explainable signal, and there is no model |
| `fail_mode` on `decisions` | Populated with the *lane's policy* (`open` for inline, NULL for async), never with an observed failure — nothing has failed, because nothing real has run. §2.1's "fail-open, **recorded**" is therefore asserted rather than demonstrated |

### Not in `architecture.md`'s numbered list, but named in it and unbuilt

- **The scheduler.** §15's topology is "one service, one database, a scheduler."
  There is no scheduler; `run_cycle.py` is run by hand. §2.2's 15-minute async
  cadence exists only as `L-203.evaluation_lag`, which is the point-in-time bound,
  not a cadence.
- **The console and admin authoring.** Out of scope by decision. Everything it
  would bind to is built and frozen.
- **§11's "console copy that outruns the system"** — the escalation toast promising
  a model retrain, and the KPI deltas implying a measured prior period. Both are
  strings in a console that does not exist here yet; they are worth fixing at the
  moment it does.

### Open decisions from §18

1, 3 and 5 were settled by building (consolidation policy, prevention asymmetry,
restricted-arithmetic scope). 2, 4 and 8 were answered by assumption in Week 2 —
fail-open as default, no mandatory shadow mode, false-negative rate published with
its synthetic caveat. **6 and 7 are still genuinely open:** the async cycle period
(15 minutes is a placeholder, and it sets the detection-latency floor for every
network pattern) and whether the copilot is deterministic or model-backed —
deterministic is recommended and is what §13 should be built as first.

---

# Week 2 handoff — GlassBox

**Status:** complete. Every Week-2 item in the plan landed; nothing was cut.
**Verified against:** PostgreSQL 16 (docker-compose), Python 3.14, 104 tests green.

---

## 1. What you can run right now

```powershell
.\scripts\bootstrap.ps1
```

Cold, that takes about three minutes and ends with 104 passing tests. Then:

```bash
psql "$GLASSBOX_DSN" -f db/acceptance/verify_scores.sql   # human-readable proof
python -m glassbox serve && curl http://127.0.0.1:8000/alerts
```

Everything below was produced by that pipeline, not written down anywhere.

| Subject | Rules | Score | Band | Action | Note |
|---|---|---:|---|---|---|
| `TXN-48291` | R-114 | **87** | high | `challenge` | |
| `TXN-48300` | R-114 + T-021 | **68** | elevated | `monitor` | R-114 wanted `challenge`; the veto held it |
| `RING-1187` | L-203 | **64** | elevated | `hold` | network subject, discovered from the link layer |
| `ACC-2201` | S-077 | **58** | elevated | `hold` | one condition resolved via the *trigger* |
| `TXN-48251` | T-021 | **31** | low | `allow` | no authority, so no alert |

Population: 9,844 transactions → 9,923 decisions across both lanes, 7 alerts,
138,061 feature values, **0 invariant failures**, 310 decisions (3.1%) carrying
degraded evidence. (Week 3 adds the condition ledger and the routing record; the
feature-value count rises with the challenge-history feature.)

---

## 2. The important thing to understand before touching this

**The generator no longer computes features, and `score_and_verify.sql` no
longer scores.** Both were deleted, on purpose, and that is the change
everything else depends on.

Week 1 derived 19 of 21 features in Python and wrote them straight to
`feature_values`; the SQL scorer then read them back. Two implementations of the
same logic, and §3.1's own argument applies: *if serving and training each
implement a feature separately they will diverge, and the divergence is
invisible.* The catalog is now the only definition. `generate_synthetic.py`
writes raw rows and asserts structural facts about them; `verify_scores.sql` is
read-only and proves the numbers in the database are the numbers on the console.

The one exception is a labelled `HAND_SEEDED_FEATURES` block holding
`new_payee_then_drain` — a `source_kind='sequence'` feature whose runner is
Week 3. One of 21 is honest; nineteen was the situation this replaced.

---

## 3. Six defects the plan did not list

These surfaced only because the features became real queries. Each is fixed and
tested; each has a migration or seed carrying the explanation.

**1. The airline ticket was recorded in the wrong country.**
`generate_synthetic.py` gave the TAP Air purchase `txn_country='PT'`. The old
Python derivation never checked transaction countries, so nobody noticed — but a
real `country_is_new_for_customer` reads Portugal as *already visited* and
T-021 silently loses the +50 that its signed-off 31 is built from. The ticket is
now bought from home (`GB`), which is also what actually happens.

**2. A burst establishes its own novelty.**
`mcc_is_new_for_customer` excludes the subject row but not the four charges
before it. By the fifth gift-card charge — the one R-114 flags — the category has
been used four times in 88 seconds, so the feature reads FALSE and R-114 scores
73, not 87. Fixed by `baseline_lag` (`0019`): a novelty baseline looks at history
up to *as_of − 1 day*, so today's activity cannot establish itself.

**3. `inet` never matches a string set.**
`ip_address::text` renders as `185.220.101.7/32`, so `ip_is_datacenter` returned
FALSE for the one transaction it exists to catch — silently, because a
non-match is not an error. S-077 scored 45 instead of 58. Fixed with
`split_part(…, '/', 1)`.

**4. Filtering the driver by the feature's own predicate breaks windowed features.**
The runner recomputes a feature where its source rows arrive. Inferring that set
from the feature's filter is only valid for monotone accumulators:
`recent_travel_purchase` driven by travel purchases alone was computed **twice in
the entire dataset**, so by the time T-021 read it the value was days stale and
the mitigator degraded for a reason that had nothing to do with the evidence.
Drivers are now unfiltered unless a spec declares `driver_filter` (`0020`).

**5. `is_required` cannot serve both satisfaction and veto establishment.**
The plan's judgment call 3 sets T-021's mitigators to `is_required = TRUE` so
"the veto is established by exonerating evidence". That establishes the veto and
**breaks the score**: satisfaction gates contribution, so a missing mitigator
makes T-021 contribute nothing and `TXN-48251` drops 31 → 0. §5's criterion says
the opposite in as many words — the score must go *up*, because the deduction is
what disappeared. The two concerns are now separate (`0021`): T-021 has no
required conditions, and `veto_established` is defined over the rule's
*mitigating* conditions. Both criteria pass.

**6. Mitigators alone produced negative risk scores.**
Any traveller paying by chip-and-PIN tripped T-021's three mitigators and scored
**−19**. On an additive scale that asserts "safer than nothing", which the model
cannot support. A mitigator is a deduction from an accusation; with no accusation
the pool is empty and the score is 0. Clamping instead would have broken
`sum(signals) == score`, and that invariant is the product.

Two further quality fixes: the cluster builder now **retires** clusters that stop
meeting their rule (previously a removed link left stale coverage asserted), and
`session_geo_jump_km` defaults to 0 (`0022`) — a card with one known location has
a jump of zero, not an unknown one. That single change took degraded decisions
from 9,419 to 310 and made the five genuine degradations visible again.

---

## 4. Decisions taken that differ from the plan

| # | Plan said | Built | Why |
|---|---|---|---|
| 1 | T-021: aggravator not required, mitigators required | No required conditions; veto defined over mitigators | Defect 5 — the plan's split makes §5's criterion unreachable |
| 2 | `verify_scores.sql` loads expectations from JSON | Expectations rendered to `fixtures/expected_scores.sql`, `\i`-included | psql cannot read JSON; this keeps `db/**` free of literal ids, which is the actual criterion |
| 3 | §3.1's seven reducers, plus eleven | Seventeen implemented; `ratio` deliberately absent | No catalogued feature uses `ratio`; a spec asking for it fails loudly rather than returning a number nobody defined |
| 4 | — | `_dimension_subject` planners for merchant/card/customer/device | §14's "INSERT only" claim is false if the planner cannot reach the subject type a new rule names |
| 5 | Tests get an `isolated_db` fixture for mutating tests | Everything runs in a rolled-back transaction | The engine takes a connection, so mutation + evaluation + assertion all fit inside one transaction. Simpler and faster. |

Judgment calls 1, 2, 4, 6, 7 and 9 from the plan were implemented as written.
Call 1's fifth fixture (`TXN-48300`) is worth singling out: §7's criterion names a
veto scoring 31 capping a score of 87, but those are **different subjects**, and a
veto must not reach across evaluations. The new fixture is one subject carrying
both rules — a customer abroad with a newly-registered phone while their card is
being tested online. R-114's four conditions genuinely hold; T-021's three
exonerating conditions genuinely hold; the right answer is to watch the charge,
not challenge it.

---

## 5. What is deliberately not done

*(Status as at the end of Week 2. Items marked **CLOSED** were done in Week 3 —
see §W3.1.)*

- **`new_payee_then_drain`** is `source_kind='sequence'`; the runner raises
  `UnsupportedSourceKind` and the generator hand-seeds the one value. Week 3.
  *(still open — deferred, §W3.5)*
- **`action_executions`** is a table with no machinery. §8 is Week 3. **CLOSED.**
- **Batch/incremental consistency testing** is Week 3 (§17).
  `run_population(as_of=<historical>)` already gives the batch behaviour, so
  there is no second implementation to build. *(still open — deferred, §W3.5)*
- **Alert dedup folding** — `dedup_key` is computed and stored; the folding
  behaviour it enables is Week 3 (§9). **CLOSED.**
- **Acceptance is checked against fixtures, not the population.** §4 and §5 are
  *demonstrated*, not stress-tested. On the labelled cohort the four rules act on
  4 of 158 fraud-labelled transactions with 1 false positive — a deliberately
  unflattering number, since the cohort was sized so most clusters fall below
  R-114's line. A cohort the rules catch entirely would make §11's
  false-negative tile read 0% and prove nothing.

---

## 6. Where to start on Week 3

*(Week 3 has since happened for §8/§9/§10; this section is what it was started
from, and the advice below all held. One thing it did not anticipate: adding a
field for a new read surface is not an `alert.v2` — queue.v1 and executions.v1 are
**siblings**, and alert.v1 was never reopened. Its digest is now pinned in
`test_contract.py`, because byte-equality alone still passes if you change a model
and re-run the exporter.)*

The console binds to `contract/alert.v1.schema.json`. It is **frozen**:
`test_contract.py::test_the_committed_schema_matches_the_models_byte_for_byte`
regenerates it in memory and asserts byte-equality, so it cannot drift by
accident. If the console needs a field the contract does not have, that is
`alert.v2.schema.json` alongside it — never an edit to v1.

Three things to know before changing anything:

1. **Never `ON CONFLICT … DO UPDATE` on `feature_values`.** It silently defeats
   migration `0014` and destroys the value a past decision was made on. A grep
   test enforces it.
2. **To replay a decision, pass its `decided_at` as `replay_as_of`** — not a
   feature's `computed_at`, which would exclude every feature the runner wrote
   after it. That is a real trap; it cost a debugging round here.
3. **Every mitigator must have `default_when_absent = NULL`.** A mitigator that
   defaults to `false` makes §5 unreachable and lets T-021's acceptance test pass
   for the wrong reason. `test_degraded.py` enforces it across the catalog.

Open questions §18 items 2, 4 and 8 were answered by assumption — fail-open as
default, no mandatory shadow mode, false-negative rate published with its
synthetic caveat. If shadow mode flips to mandatory, `0017` seeds R-114 as
`status='shadow'` and `test_precedence.py` needs a branch; nothing else moves.

---

## 7. Files

**Week 2:** `db/migrations/0011`–`0014`, `db/seeds/0015`–`0022`, `db/views/`,
`db/acceptance/`, `src/glassbox/` (23 modules), `scripts/` (8), `tests/` (11
modules, 104 tests). ~7,400 lines. Week 1's `0001`–`0010` are `git mv`'d to
canonical names with their contents untouched — and they **do** apply cleanly,
which the plan flagged as unverified. `score_and_verify.sql` is deleted;
`week1-data-model.md` is marked superseded rather than quietly edited.

**Week 3:** `db/migrations/0023`, `db/seeds/0024`–`0025`,
`db/views/v_decision_routing.sql`, `db/views/v_condition_performance.sql`,
four new `src/glassbox` modules (`engine/exposure.py`, `engine/execute.py`,
`engine/outcomes.py`, `contract/queue.py`, `contract/executions.py`,
`api/routes_queue.py`), two new scripts (`resolve_actions.py`,
`condition_report.py`), two new published contracts
(`contract/queue.v1.schema.json`, `contract/executions.v1.schema.json`), and four
new test modules — 149 tests total. `contract/alert.v1.schema.json` is
byte-identical to Week 2's and now has its sha256 pinned.

One Week-2 test changed, and only its setup:
`test_clusters.py::test_deleting_a_member_changes_the_alert` now closes the open
RING-1187 case before re-evaluating, because §9 folds a repeat evaluation onto the
open case instead of raising a second one. The behaviour under test — coverage is
derived from `cluster_members`, not literal — is unchanged.
