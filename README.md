# GlassBox

An additive, explainable risk decisioning engine. Every score is the sum of the
signals shown next to it, every action names the rule that chose it, and both
properties are enforced in three independent layers.

Detection logic lives in **rows, not code**: a rule is a `rule_definitions` row
plus `rule_conditions` rows, and a feature is a `feature_catalog` row carrying
its own computation spec. The Python in `src/glassbox/` interprets those rows —
it does not encode any particular pattern.

**New here?** [WALKTHROUGH.md](WALKTHROUGH.md) explains the whole system end to
end — in plain English first, with no code and every term defined, then the same
journey again with the file and function at each step. This README assumes you
already know what the system is for.

---

## Quick start

```powershell
.\scripts\bootstrap.ps1
```

That starts Docker Desktop if needed, brings up PostgreSQL 16, installs
dependencies, generates fixtures, migrates and seeds, computes the feature
layer, runs both decisioning lanes, settles the actions they issued, feeds the
outcomes back in as a feature, prints the condition-performance report, the band
calibration and the nine KPI tiles, exports the published contracts and runs the
acceptance suite. Roughly three minutes cold.

That builds the dataset. To see the system *react* to something rather than
rebuild around it, `scripts/demo_burst.py` sends five card-not-present charges
one at a time and the fifth is declined — see **A charge can be stopped** below.

**Or one command, on a machine with only Docker** — no Python, no Node, no
virtualenv:

```bash
docker compose up --build    # database, bootstrap, API on :8000, console on :5173
```

`init` builds the database and exits; `api` waits for it to **succeed** rather
than to start, so the service never comes up in front of an empty one. It
rebuilds every time, which is what makes the demo identical every time — and is
also why a rule authored through the console in a previous session will not
survive it. Runtime rules are rows, not seeds; put one in `db/seeds/` if it
should outlive a rebuild, or bring the stack up without the rebuild:

```bash
docker compose up db api console            # leave the existing database alone
docker compose run --rm --no-deps api python scripts/demo_burst.py
```

Step by step, on a host with Python:

```bash
docker compose up -d db                     # PostgreSQL 16 on :55432
cp .env.example .env
python -m pip install -r requirements.txt
python -m pip install -e .                  # so `python -m glassbox` resolves

python scripts/bootstrap_demo.py            # fixtures, database, both lanes,
                                            # settled actions, and the feature
                                            # that closes the loop — one script,
                                            # and the one the container runs too
python scripts/condition_report.py          # which conditions are mispriced
python scripts/calibrate_bands.py           # where the band cutoffs should sit
python scripts/kpi_report.py                # the nine tiles
python scripts/case_report.py --alert 5 --citations   # a filing draft, sourced

psql "$GLASSBOX_DSN" -f db/acceptance/verify_scores.sql   # 87 / 68 / 64 / 58 / 0
pytest                                                    # 615 tests
python -m glassbox serve                                  # API on :8000, cycle every 30s
```

Then the part that is not a rebuild — charges the system has never seen,
arriving one at a time:

```bash
python scripts/demo_burst.py                # five charges; the fifth is declined
python scripts/demo_burst.py --http         # the same, through a running service
python scripts/demo_burst.py --clean        # take it back out
```

And the same thing in a browser:

```bash
docker compose up -d console                     # :5173
docker compose run --rm console npm test         # 48 tests
docker compose run --rm console npm run build    # -> :8000/console
```

Node is never installed on the host: the packages `console/package-lock.json`
pins live in an image and a named volume, and the source is bind-mounted so an
edit is the thing Vite serves. `cd console && npm install && npm run dev` still
works if you would rather have it locally.

One pairing matters, and it fails quietly if you miss it. The container reaches
the service over the Docker bridge, which a loopback socket refuses — so the API
has to be started as `GLASSBOX_HOST=0.0.0.0 python -m glassbox serve`, or every
request from the console fails exactly as though nothing were running.
`.env.example` carries both halves.

Sign in with `analyst-token` or `admin-token`. Reads are open, so the queue, a
case and the KPI tiles render before you do. See
[console/README.md](console/README.md) for what the console holds itself to and
what the container arrangement decides.

`verify_scores.sql` keeps its psql meta-commands and must be run with `psql`
from the repository root. That is deliberate: two execution paths, `psql` for
human-readable demo output and pytest for assertions over the same facts.

---

## What it does

Five fixtures are planted as **raw rows only** — transactions, events and links.
Every feature value is then computed from the catalog by the feature runner, and
every score is produced by the engine. Nothing below is written down anywhere.

| Subject | Rule | Score | Band | Action | Why it is interesting |
|---|---|---:|---|---|---|
| `TXN-48291` | R-114 | 87 | high | `challenge` | Four conditions, all resolving to different entities |
| `TXN-48300` | R-114 + T-021 | 68 | elevated | **`monitor`** | R-114 wants to challenge; T-021's veto holds it back |
| `RING-1187` | L-203 | 64 | elevated | `hold` | A network subject, discovered from the link layer |
| `ACC-2201` | S-077 | 58 | elevated | `hold` | Resolves a condition against the *triggering row* |
| `TXN-48251` | T-021 | 0 | low | `allow` | The exoneration consumed the accusation; no score to show |

`TXN-48251` scored 31 until the condition report was acted on. Its aggravator was
priced at +50 — a number reverse-engineered in Week 1 so that case's points would
sum to its displayed score — and earned 6.78% precision over 398 firings, 4.4×
the cost per unit of measured precision of any comparable aggravator. Seed `0026`
reprices it to **+12**. The pool is then `12 − 9 − 6 − 4`, the mitigators outweigh
the accusation, and consolidation publishes nothing rather than a negative risk
score. Same story, defensible price, empty bar.

Alongside them sits a scored population: ~9,800 transactions, ~300 declines,
~120 refunds, a 22-decline card-testing burst, a refund-abuse customer and 22
labelled fraud clusters deliberately sized so most fall *below* R-114's line.

Every one of those 9,923 decisions records what became of it — `raised`,
`folded`, `restated`, `suppressed` or `no_authority` — and every condition it
evaluated, fired or not: 79,068 ledger rows. That is what turns "alert volume"
from a count of evaluation cycles into a count of cases, and what makes "which
conditions are mispriced" a query rather than a guess.

It also makes the nine KPI tiles arithmetic instead of illustration. Over the
seven days to the reference instant: 5 cases against 1 the week before, a 95%
false-negative rate over 80 labelled-fraud decisions, 4 preventive actions issued
and 0 prevention false positives out of 4. Every one of those numbers carries its
denominator, its window, and — where it was settled by a script rather than
observed — a flag saying so.

---

## How it fits together

```
db/migrations/  0001-0008  Week 1 schema, unchanged
                0011       feature computation specs + the resolution graph
                0012       decision detail: evaluation, veto, prevention, versions
                0013       version stores, action executions, clusters
                0014       bitemporal feature_values  (the one non-additive migration)
                0023       alert routing, fold state, exposure, condition ledger
                0029       who decided a case: analyst or script
                0030       the shadow gate's columns, and what publishing IS
                0032       provenance on every relation a row can now arrive in,
                           and the watermark that makes a cycle incremental
db/seeds/       0009-0010  the catalog and the four rules
                0015-0021  21 computable specs, 11 resolution edges, rule policy,
                           score bands, novelty baselines, driver filters
                0024-0025  hygiene policy + action routing; the challenge-history
                           feature (INSERTs only — no engine change)
                0026-0028  the repriced condition, the calibrated bands, and the
                           refund-abuse features — all three are UPDATEs and
                           INSERTs applied by hand from a report's evidence
                0031       the definitions that predate the publish step,
                           backfilled through the same function it calls
db/views/       v_alert_invariants.sql       sum(signals) == score
                v_decision_routing.sql       the routing invariant 0023 could not CHECK
                v_condition_performance.sql  §10: cost per unit of precision
                v_kpi_decisions.sql          volume, distribution, recall, fail-open
                v_kpi_cases.sql              dispositions and the triage clock
                v_kpi_executions.sql         what was done, and prevention precision
                v_kpi_rule_attribution.sql   asserted vs carried, after dedup
                v_kpi_reason_codes.sql       the trend, and its baseline window
db/acceptance/  verify_scores.sql          read-only; no hardcoded subject ids

src/glassbox/
  features/   predicate.py  the injection boundary
              aggregations.py  17 named reducers
              compiler.py   spec -> parameterised query
              runner.py     append-only, point-in-time-correct writes; and which
                            features an arriving row in a relation drives
  graph/      builder.py    clusters from the link layer
  engine/     resolver.py   subject -> entity, over a stored graph
              pit.py        the point-in-time read
              conditions.py fire / degrade, and record the verdict once
              scoring.py    per-rule score, before dedup
              consolidate.py one signal per (feature, direction); drop a pool
                            the mitigators consumed
              precedence.py veto -> authority -> severity -> prevention -> cap
              exposure.py   money at risk, bounded at the decision's PIT bound
              persist.py    one decision; the condition ledger; fold/restate/suppress
              execute.py    issue what was authorised, once per case
              outcomes.py   settle challenges, disposition cases (synthetic)
              evaluation.py the order, which is the design
  explain/    evidence.py   the eight relations, and the Quoter every number passes
              copilot.py    three chips, templated
              case_report.py the filing draft, which says it is one
  rules/      validate.py   what an authored rule must fail against
              publish.py    definition -> version -> snapshot, or none of it
  ingest/     records.py    what a row must satisfy before it is written down —
                            shared by both doors AND by the what-if
              authorize.py  decide, then write the decision INTO the row
              arrivals.py   settled transactions, events, link edges
              watermark.py  how far the cycle has consumed, in event time
              cycle.py      graph -> features -> lanes, over what arrived
  scheduler.py              the third of "one service, one database, a scheduler"
  contract/   models.py     alert.v1 — frozen, digest-pinned
              queue.py      queue.v1 — priority, with its factors published
              executions.py executions.v1 — what was done, and its synthetic flag
              kpis.py       kpis.v1 — nine tiles, and the only place a window is defined
              explanation.py explanation.v1 — and the validator that refuses to
                            explain a score without its mitigators
              dispositions.py dispositions.v1 — the analyst's verdict, appended
              simulation.py simulation.v1 — what the engine would say, unstored:
                            a stored subject, a candidate rule, a charge nobody made
              catalog.py    catalog.v1 — the control plane, and what each
                            condition has actually earned
              ingest.py     ingest.v1 — the two doors, and what a cycle did
  api/        fifteen read endpoints, ten writes, three simulations
              routes_rules.py  author, edit, promote, retire — admin only
              routes_ingest.py /authorize, /ingest/*, /cycle — admin only
              auth.py       two demo users; reads open, writes not
              app.py        …and it serves console/dist at /console

console/
  src/api/    openapi.json  generated offline from the app; committed
              schema.d.ts   generated from that; committed
              types.ts      aliases into it, and one hand-written shape
              client.ts     the only place this console talks to the service
  components/ ScoreBar.tsx      the one bar, rendering three payloads
              DecisionFrame.tsx `persisted` is what makes them unconfusable
              SystemStrip.tsx   the only thing that says whether anything runs
              Tile.tsx          a KPI tile, with no copy of its own
  screens/    Queue, Alert, Kpis, Rules, RuleDetail, RuleAuthor, Simulate,
              Authorize — the last of which commits, and says so first
  format.ts   exact arithmetic over the Decimal strings the API sends
  console.test.ts  the claims no render can prove, checked against the source
```

The pipeline order, in one line each:

0. **Arrive** — a charge asks to be authorized, or settled rows are reported.
1. **Graph** — clusters and members, from `entity_links`.
2. **Plan** — one `EvaluationRequest` per (subject, lane), carrying its trigger.
3. **Features** — out of band, into `feature_values`, stamped `as_of` + `computed_at`.
4. **Resolve** — which entities each feature keys on, over the stored graph.
5. **Read** — newest value at or before the bound, under a replay ceiling.
6. **Conditions** — fire, or degrade and record why.
7. **Score** — per rule, *before* dedup.
8. **Consolidate** — one signal per `(feature_key, direction)`.
9. **Band** — from `score_bands`, per subject type.
10. **Precedence** — veto, authority, severity, prevention, cap.
11. **Persist** — one decision per (subject, lane, evaluation), plus every condition it looked at.
12. **Route** — fold onto an open case, restate it, suppress under an investigation, or raise.
13. **Issue** — preventive actions on a raised case only; notifications also on a restatement.
14. **Settle** — outcomes back onto the executions, dispositions onto the cases, events into the log.

`/authorize` runs 0 and 3–13 synchronously for one charge and commits. The
background cycle runs 1–13 on an interval, over whatever arrived since its
watermark. Step 14 is still a script.

---

## The properties worth knowing about

**Resolution is a graph plus a route selector.** A feature keys on an entity the
rule's subject may not be. `resolution_edges` holds the graph;
`feature_catalog.resolution_path` picks the route (`self`, `trigger`, `auto`, or
an explicit `subject.card.account`). The `trigger` root exists because S-077's
subject is an account but "the transfer came from a datacenter IP" is a fact
about one specific transaction.

**Absence is observable.** `default_when_absent` is JSONB: SQL `NULL` means *no
default — write nothing*, and any JSON value including `0` and `false` is a real
answer. Every feature cited by a negative contribution has no default, enforced
by a test. A missing mitigator raises the score, which is the correct arithmetic
and the wrong action — so it also strips preventive authority.

**Resolution failure is never a silent zero.** No entities, or more than the
fan-out policy allows, produces a recorded degradation rather than a partial
score.

**`feature_values` is bitemporal and append-only.** `as_of` is when a fact was
true, `computed_at` is when we learned it. A recomputation is an INSERT. Never
write `ON CONFLICT … DO UPDATE` against that table — it silently destroys the
value a past decision was made on, and a test greps for it.

**The read contract is frozen, and the freeze is enforced by a digest.**
`contract/alert.v1.schema.json` is generated from
`src/glassbox/contract/models.py` and committed; a test regenerates it in memory
and asserts byte-equality *and* checks its sha256. Byte-equality alone passes if
you change a model and re-run the exporter, so the digest is what makes the
freeze real. Never edit it by hand — a breaking change becomes
`alert.v2.schema.json` and v1 keeps being served.

**New read surfaces are siblings, not new versions.** `queue.v1` and
`executions.v1` publish what alert hygiene and action execution produced —
`triggering_events`, exposure, priority, challenge outcomes — without touching a
byte of alert.v1. `Subject`, `Signal`, `Action` and `Evidence` live inside
alert.v1's `$defs` closure, so adding a field to any of them for the queue's
benefit would break the digest. That is why `models.py` was left alone.

**One case, many triggering events.** An alert carries a `dedup_key`, and a
repeat evaluation inside the subject type's `open_window` folds onto it instead
of raising a second case. Running the async cycle N times over a static dataset
produces the same alert count for every N — before this, every run inserted a
new row. If the repeat scores *higher*, the case is **restated**: it re-points at
the worse evaluation and its signal set is replaced in the same operation, never
one without the other, because an alert's signals must always be exactly one
decision's pool or the score bar stops adding up.

**The queue order explains itself.** `priority = score × exposure × recency`,
with all three factors and the formula published on every entry. Exposure is
log-damped (amount spans orders of magnitude, so undamped it decides the order
alone), floored (an unpriced alert must stay reachable), and bounded at the
decision's point-in-time bound rather than `now()` — an unbounded exposure would
drift on every recomputation and make the ordering depend on the wall clock. On
the shipped fixtures a 58 with $11,050 at risk outranks a 68 with $33.50.

**Precision is measured per direction.** A mitigator is right when it fires on
*legitimate* traffic — that is its job. `v_condition_performance` scores an
aggravator by its fraud rate and a mitigator by its legitimate rate, because a
direction-blind precision ranks `entry_mode_chip_pin` — 9,562 firings, never once
on fraud — as the worst condition in the catalog rather than the best. Same
inversion §5 objects to when an absent mitigator is treated as a non-firing one.

**Nothing is challenged twice for the same situation.** Preventive actions are
issued when a case is *raised*, not when it folds; a notification goes out again
on a restatement. A ring re-evaluated every fifteen minutes would otherwise send
the customer 96 step-ups a day, and "block rate" counted off decisions would be
double the number of customers actually affected.

**A negative risk score is not a claim the model can make.** A mitigator is a
deduction from an accusation. When the deductions consume the accusation there is
nothing left to publish, so consolidation drops the pool whole — score 0, empty
signal set. Clamping the score to zero while still showing the signals would break
`sum(signals) == score`, and that invariant is the product. `TXN-48251` is the
case: `12 − 9 − 6 − 4 = −7`, published as 0 with nothing on the bar.

**Every tile names its window, its denominator, and what it cannot tell you.**
Deltas compare against the immediately preceding window of the same length and
nothing else — when the dataset does not reach back that far, the delta is null
and the payload says why. The false-negative rate is exact against planted ground
truth and meaningless beyond it; the challenge pass rate was settled by a script,
not a customer; `fail_mode` records the lane's policy and has never seen a
failure. All three ride with a `synthetic` flag and a caveat rather than a
footnote.

**The explanation surface is deterministic, and can only read one case.** The
copilot and the case report are templating over `alert_signals`, `decisions`,
`action_executions` and five more relations for the alert in view — a cursor
hook in the test suite fails the build if anything else is queried. Two of the
eight are the version stores, added in Week 5 so the report can say whether a
recorded `rule_version_set` actually resolves instead of guessing in either
direction; the lookup is keyed on ids the alert already carries, which is why it
is a pointer being resolved rather than the boundary being widened for
convenience. Every number
in the output passes through a `Quoter` that records the table and primary key it
came from, or the formula if it was derived, and a test extracts every numeric
token and checks it traces back. Mitigators and applied vetoes are not optional:
`CopilotAnswer` raises rather than return an explanation that quotes a score
without them. No language model is involved in any field of any payload, which is
a design choice rather than a limitation — the explanation surface of a glass-box
system should not itself be a black box.

**A rule is published, not saved.** `POST /rules` writes the definition,
snapshots it into `rule_versions` with the actor who published it, and lands the
rule in **shadow** — where the engine scores it on every applicable subject,
records every condition it looked at (`decision_conditions.is_shadow`) and
records the action it would have taken (`decisions.shadow_action`), and lets it
alert nobody and challenge nobody. `POST /rules/{id}/promote` is what makes it
act, and it is a separate call because it is a separate decision. The version
counter moves only when the definition actually moved: a save that changed
nothing is not a new version, because `decisions.rule_version_set` records the
version an evaluation *read* and a counter that ticks on every keystroke makes
that set meaningless. Deleting is retiring — the foreign keys refuse to remove a
rule that ever acted, which is the audit trail defending itself.

**Reads are open; the surfaces that leave a mark are not.** An analyst marks
a case through `POST /alerts/{id}/outcome`, authenticated by a bearer token that
resolves to one of two demo users (`src/glassbox/api/auth.py` — a static map, not
authentication, and it says so). The actor comes from the principal and never
from the body. `alerts.status` stays engine-owned: the engine raises, folds,
restates and suppresses, the analyst owns the verdict, and the queue asks the
question it actually means — *has a person worked this?* A case closed by
`resolve_actions.py` has not been, which is why `case_outcomes.source` exists and
why a synthetic pass does not empty the queue.

**An authored rule has to fail loudly or not at all.** Every way of getting a
rule wrong used to produce a rule that did *nothing*: `conditions.fires()`
returns False for an operator it does not recognise, and for a numeric operator
with no threshold, so a typo yielded a rule that never fired, never errored, and
appeared in the ledger as a condition that simply never matched.
`rules/validate.py` turns twenty-two of those into rejections an author can read —
including the two §5 and §7.3 care about most: a mitigator on a feature carrying
a default (it can never be observed *absent*, so it can never strip preventive
authority) and a `prevent_threshold` below its `review_threshold`. The operator
list it checks against is the same tuple the interpreter dispatches on, and the
action, subject-type and reason-code vocabularies are the same `ref_*` rows the
foreign keys enforce and `GET /reference` serves — so the console's dropdowns
cannot offer something the validator will refuse. Every rule this repo ships is
asserted to validate clean, reconstructed from its stored rows rather than
written out in the test.

**A rule can be tested against history before it exists.** `POST /simulate/rule`
applies a candidate to the control plane inside the same rolled-back scope
`/simulate/subject` uses, loads the engine context *after* the draft is applied,
and runs the ordinary pipeline — there is no "simulation mode" in the evaluator,
because a second evaluation path would be a second answer. It takes an edit as
well as a new rule, which is what makes its diff worth reading: repricing
`session_geo_jump_km` from 18 to 5 reports `TXN-48291` moving 87 → 74 and
`challenge` → `alert` *before* the seed is written. Week 4's `0026` moved a
signed-off score and the blast radius had to be worked out afterwards.

**Dispositions are append-only and the latest one wins.** A correction is a
second row; the first judgement stays in the record. `v_kpi_cases` publishes the
latest as the verdict and still measures the triage clock to the *first* one,
because a correction hours later does not mean triage took hours longer. Every
tile derived from a disposition now reads that provenance instead of asserting
it: the two tiles that used to carry a hardcoded "every disposition here was
written by a script" say how many were, and say nothing at all once none are.

**A simulation is the same engine with nothing written down.**
`POST /simulate/subject` re-derives a subject's decision — same planner, same
point-in-time read, same precedence — inside a scope that rolls back, and
publishes `persisted: false` on the wire rather than leaving a caller to infer it
from the URL. The bar comes from `engine.persist.ranked_signals`, the one
function that also writes a stored alert's signals, so a simulated bar and the
alert it predicts cannot disagree. With `replay_as_of` set to a stored decision's
`decided_at` it answers *"what did that decision see?"* rather than *"what would
we say today?"*, which is the audit question the bitemporal feature store exists
to make answerable.

**A charge can be stopped, and that is new.** Every week before this one
detected fraud correctly and could prevent none of it: transactions arrived
already stamped `auth_result='approved'`, so a `challenge` decision was a note
attached to money that had already moved. §7.3 argues that prevention needs a
higher threshold *because a wrong block costs a customer*, and nothing here
could cost a customer anything. `POST /authorize` closes that: the row is
inserted as presumed-approved (it has to be — `card_cnp_count` counts approved
CNP charges, and a charge that does not count itself is the fifth of five
reading four), the features it drives are recomputed at its own instant, the
inline lane runs, and **precedence decides the `auth_result` before the
transaction commits**. Which actions stop a charge is read off
`ref_action.is_preventive`, not a list in Python. A `challenge` commits as
`declined` with `decline_reason='step_up_required'`, because a step-up nobody
has answered has not been passed — and that is how 3DS behaves. Raw capture is
append-only and the write-back happens inside the same uncommitted transaction
as the insert, so there is no moment at which a blocked charge existed in this
database as an approved one. `scripts/demo_burst.py` is the whole thing in one
screen: five charges twenty seconds apart, four approved, the fifth 87 and
declined, with a step-up issued and every point on the bar.

**The decision invalidates the evidence it was made on, and both survive.** A
charge declined after being counted as approved is no longer an approved CNP
charge, so the scoped feature pass runs a second time after the write-back.
`feature_values` is append-only and bitemporal, so the recomputation is an
INSERT with the same `as_of` and a later `computed_at`: the decision keeps the
evidence it actually saw, the store ends up describing what really happened, and
§4's replay reads whichever of the two it asks for. This is the first place in
the project where bitemporality earns its keep on a write path rather than in an
argument.

**Something is actually running.** §15's topology is "one service, one database,
**a scheduler**", and for four weeks the last third of that was a promise —
`run_cycle.py` was run by hand, which is why §18's decision 6 stayed open.
`glassbox serve` now starts a daemon thread that turns the engine over every
`GLASSBOX_CYCLE_SECONDS` (default 30, `0` disables it, and the test suite sets
`0` because a thread committing into a rolled-back test transaction is the least
debuggable failure this project could have). §2.2's 15 minutes was a production
number chosen against graph-rebuild cost at real volume; here the whole cycle is
under a second, so fifteen minutes would buy nothing and cost the only thing a
prototype needs to show. **A tick evaluates what arrived, not the population** —
`affected_subjects` narrows to the transactions, the entities behind them and
the clusters those entities belong to, because `plan_evaluations` otherwise
re-scores 9,844 transactions to notice one charge. The narrowing is by *subject*
and never by rule or feature, so a re-evaluated subject is still evaluated
against its whole history and an incremental tick and a full pass give the same
answer. On the shipped fixtures a tick that finds one new charge takes ~90 ms
against ~20 s for a full pass, and a tick with nothing to do takes 7.

**Two doors, and they mean opposite things.** `/authorize` asks for a decision;
`/ingest/transactions` reports one somebody else already made. The models carry
the distinction rather than a docstring: an `AuthorizationRequest` has no
`auth_result` and no `decline_reason` because the engine sets them, and a
`TransactionRecord` has both plus `synthetic_label` because planted demo data
should be labelled or it is invisible to every precision number in the system.
They are separate endpoints rather than one with a `decide=true` flag, which
would be one typo away from approving a charge the engine was never asked about.
Events and links have their own doors because two of the four rules cannot be
reached without them: L-203 discovers a ring from `entity_links` and no quantity
of ingested transfers will produce one, and S-077 reads a password reset out of
`events`.

**A device is observed; an account is opened.** An unrecognised `device_id` is
registered at the instant it is first presented — by all three writers, through
one helper, between validation and the insert — while an unknown `card_id` is
refused. That is not a convenience: `device_first_seen_min` is measured from
exactly that instant and is 21 of R-114's 87 points, so refusing an unseen
device would mean the only demonstrable "new device" is one the generator
planted. A link to an account nobody opened *is* refused, and nothing else would
have caught it — `entity_links.to_id` is polymorphic with the type in a
neighbouring column, so there is no foreign key, and a phantom edge builds a
cluster out of nothing whose ring looks exactly like a real one.

**A charge that never happened can be scored, and the feature layer moves with
it.** `POST /simulate/transaction` inserts a fabricated row, runs a feature pass
scoped to the instant it claims to have occurred at, evaluates it, and rolls all
three back. The pass is what makes the answer new rather than borrowed: a sixth
card-not-present charge on `CARD-4417` makes the engine read `card_cnp_count`
**6**, where the stored value at that instant is 5 — and
`mcc_is_new_for_customer` keys on `txn_id`, so without the pass it could not
exist at all and R-114's satisfaction gate would fail for a reason that has
nothing to do with the charge. Which features the pass runs is derived from each
compiled spec's **driver** relation, not from its source: an arriving
transaction recomputes `min_since_password_reset`, whose source is `events`.
Everything it did not recompute is named on the payload with the reason, as are
four limits it cannot get past — a fabricated row cannot establish its own
novelty (`baseline_lag`), cannot move a graph feature, cannot carry a
`synthetic_label`, and is evaluated as a transaction subject in one lane only.
It is **admin-only**: every other defence here is on the payload, and the role
is the one that does not depend on anyone reading it.

**The console asserts nothing the payload does not say, and four of its claims
are checked against its own source.** It is the first surface here that a person
judges by looking rather than by reading a payload, which makes it the easiest
place to break a guarantee the rest of the system spent five weeks enforcing. So:
one score-bar component renders `alert.v1`, `simulation.v1` and `ingest.v1`, and
a test asserts there is exactly one of it — a second bar is the failure mode
`persist.ranked_signals` was made public to prevent, moved somewhere the server's
tests cannot see. The bar sums the contributions as **scaled integers**, because
Pydantic sends `Decimal` as a string and parsing those into floats to add them up
would hand back the exactness `sum(signals) == score` depends on. `persisted` is
what decides the frame around a decision, read off the payload rather than off
which screen is rendering, and it beats `stopped` — a rolled-back decision that
*would* have declined a charge never carries the frame that means money did not
move. Whether the engine is running comes from `GET /cycle` and nowhere else,
with three answers rather than two: running, not running, and *could not ask*.
And `POST /authorize` — the one endpoint that commits and can decline a charge —
has exactly one call site, so the console cannot quietly use it to answer a
hypothetical. That is what `/simulate/transaction` is for, and they are separate
endpoints precisely so a typo cannot turn one into the other.

---

## Extending it: what costs rows, and what costs code

Two detectors are added this way, and both are tests rather than paragraphs.
`tests/test_extension_cardtesting.py` builds a card-testing detector on a
**merchant** subject with two INSERTs; `tests/test_extension_refundabuse.py`
builds a refund-abuse detector on a **customer** subject, with two conditions
AND'd across groups, over two catalog rows seeded in `0028`. A psycopg hook
records every statement executed in either test body and fails if any of them is
DDL, so the claim is checked rather than asserted.

The refund detector also shows the claim's edge. The pattern wants refunds as a
*ratio* of purchases; §3.1 names a `ratio` reducer and `aggregations.py`
deliberately does not implement one. Writing it to make this land would have been
exactly the data-engineering ticket the table below describes — for the very
feature offered as evidence that growth costs rows. So the detector is built from
`count` and `sum`, and the honest cost is that it cannot normalise for customer
size.

The claim has limits, and they matter:

| Change | Cost |
|---|---|
| New rule over existing features | **INSERTs.** Two rows. |
| New feature using an existing reducer | **INSERTs.** A `feature_catalog` row plus a runner pass. |
| New feature needing a **new reducer** | A data-engineering ticket — a Python function in `aggregations.py`. |
| New subject type beyond `ref_subject_type`'s seven | A code change: the planner needs to know how to reach it. |
| New relation to read from | A code change: `ALLOWED_RELATIONS` is a deliberate allow-list. |

§3.1 names seven reducers; the 21 catalogued features need seventeen. That does
not break the design — a named reducer is a Python function, not an expression
language, so no admin-authored text ever becomes SQL. But it does mean "growth
is catalog rows, not code" is true for the common case and not for all cases,
and it is worth saying so out loud.

---

## Tests

```bash
pytest                       # 615 tests, ~120s including a full rebuild
pytest tests/test_degraded.py -v

docker compose run --rm console npm test   # 48 tests, ~3s
```

Tests use the same docker-compose PostgreSQL, in a dedicated `glassbox_test`
database that `conftest.py` drops and recreates at session start, so state is
never inherited. A session fixture migrates, seeds, loads fixtures, runs the
feature layer and runs both lanes once; every test then runs inside a
transaction that is rolled back at teardown, so tests are order-independent and
a test that mutates rules or catalog rows cannot leak.

| Module | Covers |
|---|---|
| `test_resolution.py` | §3.2 — routes, the trigger root, fan-out, AND semantics |
| `test_clusters.py` | §3.3 — derived coverage, stable ids, no literals under `db/` |
| `test_point_in_time.py` | §4 — lag, staleness, replay vs live |
| `test_degraded.py` | §5 — the mitigator policy and the narrowed veto clause |
| `test_consolidation.py` | §6 — one decision, signals that add up, order invariance |
| `test_precedence.py` | §7 — veto cap, demotion, deterministic ties |
| `test_execution.py` | §8 — issuance, settlement, determinism, the outcome-fed feature |
| `test_hygiene.py` | §9 — N runs / one case, restatement, suppression, exposure |
| `test_conditions_ledger.py` | §10 — every condition recorded, and why it does not sum |
| `test_condition_report.py` | §10 — the misprice closed, and that the instrument still finds one |
| `test_calibration.py` | §10 — maximum-gap cutoffs, and that `basis` tells the truth |
| `test_kpis.py` | §11 — nine tiles, denominators, deltas only against a real prior window |
| `test_explain.py` | §13 — the scope hook, the numeric sweep, the mitigator validator |
| `test_contract.py`, `test_api.py` | §12 — the freeze, the digest, four siblings, seven endpoints |
| `test_predicate_safety.py` | §3.1 — six injection shapes rejected, values always bound |
| `test_feature_runner.py` | §3.1 — every value the deleted generator code derived |
| `test_extension_cardtesting.py` | §14 — INSERT-only extension on a merchant, DDL hook |
| `test_extension_refundabuse.py` | §14 — the second pattern, on a customer, AND across groups |
| `test_migrations.py` | the five-column key, no UPSERTs, ledger idempotence — and that views are the exception |
| `test_dispositions.py` | Week 5 — append-only verdicts, latest wins, the clock stays on the first, the caveat that derives itself |
| `test_simulation.py` | Week 5 — the five fixtures re-derived, the bar that matches the stored bar, and the row counts that must not move |
| `test_api_auth.py` | Week 5 — reads open, writes authenticated, the actor that cannot be smuggled in a body |
| `test_catalog_api.py` | Week 5 — the control plane read, the measurement beside the price, and the condition that admits it has never been evaluated |
| `test_rule_validation.py` | Week 5 — the ways to author a rule that does nothing, each a 422; and every shipped rule still validating clean |
| `test_simulate_rule.py` | Week 5 — a candidate rule over history, the reprice diff, and the two control-plane tables that must not move |
| `test_publish.py` | Week 5 — the counter that moves only when the definition does, the previous definition retrievable as it was, and the transitions that are refused |
| `test_simulate_transaction.py` | Week 5 — a charge that never happened, the scoped feature pass watched writing and watched being gone, and the ground truth that cannot be fabricated |
| `test_authorize.py` | Week 5 — the burst that is stopped, the row that commits as `declined`, and the two answers the bitemporal store keeps |
| `test_ingest.py` | Week 5 — three doors, retries that are not errors, and the phantom edge no foreign key would catch |
| `test_cycle.py` | Week 5 — a tick that reacts, a tick that costs nothing, and the narrowing that makes an interval possible |
| `test_shadow.py` | Week 5 — a shadow rule contributes nothing and records everything, including the veto it is not allowed to cast |
| `test_openapi.py` | Week 5 — the document the console's types come from is current, every contract is reachable from a route, and nothing dangles |

And the console's own, which check what a server-side test cannot see:

| Module | Covers |
|---|---|
| `format.test.ts` | the bar sums exactly, where a float sum would not |
| `ScoreBar.test.tsx` | the arithmetic on screen, a payload that does not add up, and that no mitigator is ever hidden |
| `DecisionFrame.test.tsx` | `persisted` decides the frame — and beats `stopped`, so a rolled-back decline never reads as a real one |
| `Tile.test.tsx` | tile copy comes off the payload; no delta without its baseline window; an absent value is an absence, not a zero |
| `SystemStrip.test.tsx` | three answers about liveness, including "could not ask" |
| `console.test.ts` | one bar, one `/authorize` call site, one source of liveness, no redeclared payload shapes, none of the copy §11 flags |

---

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `GLASSBOX_DSN` | `postgresql://glassbox:glassbox@localhost:55432/glassbox` | Dev database |
| `GLASSBOX_TEST_DSN` | …`/glassbox_test` | Dropped and recreated by the test session |
| `GLASSBOX_NOW` | `2026-01-15T15:00:00+00:00` | The fixtures' reference instant |
| `GLASSBOX_CYCLE_SECONDS` | `30` | Background cycle interval. `0` disables it — which is what the test suite sets |
| `GLASSBOX_API_TOKENS` | two demo users | `token:actor:role,…` |

Port 55432 rather than 5432 so a locally-installed PostgreSQL does not collide.

An ingested or authorized charge is dated at `GLASSBOX_NOW` unless it says
otherwise, and that matters more than it looks: every window feature (90s, 24h,
30d) is measured against history pinned to 2026-01-15, so a charge dated at wall
clock would see a card with no past at all. The cycle's watermark is event time
for the same reason.

---

## Known gaps

- `new_payee_then_drain` is `source_kind='sequence'`; the sequence runner is
  still unbuilt. It is the single value the generator hand-seeds, in a labelled
  `HAND_SEEDED_FEATURES` block. S-077's other three features are computed for
  real.
- **Only `transaction` has a calibrated band cutoff.** `account` and `network`
  have one scoring subject each on this dataset, and a cutoff derived from n=1 is
  n=1 wearing a calibration's clothes. Both keep the inherited 70/45 and say
  `UNCALIBRATED` in `score_bands.basis`, which `engine/bands.py` reads on every
  decision. `test_calibration.py` fails if any of them stops saying so.
- **The calibrated cutoffs change no outcome on this dataset.** 70/45 → 75/40
  produces an identical partition; the lines moved *away* from the observed
  scores rather than across them. That is a defensive recalibration and claiming
  more of it would be the overstatement §16 warns about.
- **Band cutoffs are maximum-gap, not risk-appetite.** They support "no observed
  subject sits near this line" and nothing stronger. A cutoff that encodes an
  appetite needs dispositions at volume, and §8's denominators here are single
  digits.
- **One reprice in the project's history is unversioned, and it is the one that
  moved a signed-off score.** Seed `0026` repriced `country_is_new_for_customer`
  from +50 to +12 before a version counter was ever bumped, so T-021 version 1
  names two definitions. Seed `0031` backfills the *current* definition at
  version 1 rather than inventing a version 2 — a retroactive version would
  leave every stored decision pointing at a v1 no snapshot exists for, which
  breaks the thing it was meant to fix. The loss is recorded in `0026` and in
  `HANDOFF.md` §W4.2, and it is the last change a version set cannot
  distinguish.
- **Editing a rule deletes the condition ledger behind its old conditions.**
  `decision_conditions` references `rule_conditions` with `ON DELETE CASCADE`,
  and an edit replaces conditions wholesale — so repricing a condition destroys
  the firing history that found the misprice. The *definition* survives in
  `rule_versions`; the per-firing evidence does not. Harmless on a dataset
  rebuilt from scratch, and named in `rules/publish.py` rather than discovered
  later (WEEK5-PLAN D8).
- `ratio` is named by §3.1 but unused by any catalogued feature, so it is
  deliberately not implemented — a spec asking for it fails loudly at compile
  time rather than returning a number nobody defined.
- **Challenge outcomes are synthetic.** Nothing external answers a step-up here,
  so `scripts/resolve_actions.py` settles them deterministically against
  `transactions.synthetic_label`. Every such row is stamped `synthetic = TRUE`,
  and `executions.v1` puts the flag on the wire so no surface can present a
  synthetic pass rate as a measured one.
- **§8's denominators are single digits.** A full pass authorises four challenges
  and two holds, so prevention precision is n=4. Every rate the resolver prints
  carries its denominator for that reason.
- **The prevention-false-positive count is zero, and that is a result.** All six
  preventive actions land on fraud-labelled subjects, so no challenge passes and
  nothing is dispositioned `confirmed_legit`. The join §8 exists for works — it
  simply has nothing to find on this dataset, and `test_execution.py` exercises it
  on a constructed case rather than pretending otherwise.
- **Suppression is unreachable on the shipped fixtures.** All seven alerting
  subjects have exactly one `dedup_key`, so a second *distinct* rule set never
  arrives. `test_hygiene.py` inserts one to reach the path.
- Acceptance is checked against fixtures, not the population. §4 and §5 are
  *demonstrated*, not stress-tested; the two extension passes are the partial
  substitute.
- **The false-negative rate is 95%, and that is the generator working.** The
  labelled cohort was deliberately sized so most clusters fall below R-114's
  line. A cohort the rules caught entirely would make the tile read 0% and prove
  nothing.
- **Nothing ships in shadow, so the shadow columns are NULL across the whole
  population.** The gate is exercised by `test_shadow.py`, which shadows R-114
  and watches `TXN-48291` go from 87 / `challenge` to 0 / `allow` with the
  would-be answer moving into `decisions.shadow_action`. A consequence worth
  knowing: `v_condition_performance` does not separate shadow firings from live
  ones, which is invisible here and would dilute `mean_contribution` for a
  condition measured across a promotion (WEEK5-PLAN D9).
- **A stopped charge has no second act.** `/authorize` declines a challenged
  charge with `step_up_required`, which is what 3DS does — and in a real system
  the customer authenticates and retries, and the retry is a new authorization
  that sees the challenge in the card's history. Nothing here answers a step-up:
  `resolve_actions.py` still settles them against `synthetic_label`, and there
  is no endpoint for "the customer passed". So the demo shows a charge being
  stopped and never shows one being released.
- **Dimension rows cannot be ingested, on purpose, and it has a cost.** A device
  is observed and so is created; a card, account, customer or merchant is
  *opened*, which is an onboarding act this system does not model, so an unknown
  one is refused. The consequence for a live demo is specific: a mule ring needs
  four accounts, and a genuinely new ring cannot be ingested — the ring demo has
  to reuse existing accounts, which `test_cycle.py` does.
- **Ingested events are not idempotent.** `transactions` dedupe on `txn_id` and
  `entity_links` on what the edge means; `events` have neither a primary key
  worth using nor a natural one, so re-sending a batch appends it again. The
  reducer that reads them (`age_minutes_latest`) is insensitive to a repeat at
  the same instant, and inventing a key to make the receipt tidier would be
  inventing a fact — so the receipt publishes `idempotent: false` instead.
- **The cycle is at-least-once.** A tick that dies advances no watermark and the
  next one re-reads the same window. That is safe rather than lucky — the
  feature runner is append-only and §9's folding means a re-evaluated subject
  produces the same alert count — but it does mean a tick can repeat work, and
  exactly-once here would be machinery bought for nothing.
- **The inline lane's 50 ms p99 is still a design target.** `/authorize`
  publishes `latency_ms` and it is honest about what it includes: on the shipped
  fixtures an approving charge is ~50 ms and one that raises a case and issues a
  step-up is ~200 ms, on a laptop, against a local Postgres, with the scoped
  feature pass inside the measurement. That is a number you can now argue with,
  which is more than it was, and it is not a throughput claim.
- **Still deferred:** the `sequence` source kind, which would delete the last
  hand-seeded feature value; and the batch/incremental consistency test.
  §15's "one service, one database, a scheduler" is now all three, and §18's
  decision 6 is answered at 30 seconds — a demo number, not a production one.
  The console is built and no longer deferred.
- **The published contract schemas contain dangling `$ref`s, and still do.**
  `export_contract_schema.py` uses a document-root-absolute `ref_template` while
  Pydantic nests `$defs` per model, so `#/$defs/Signal` in `alert.v1` resolves to
  nothing. True since Week 2 and harmless until something tries to resolve one —
  which is what a TypeScript generator does. The console is therefore generated
  from the **OpenAPI document** (`scripts/export_openapi.py`), where FastAPI
  hoists every model into `components.schemas`; `alert.v1`'s digest never comes
  near a build tool. That routes around the problem rather than fixing it, and
  fixing it is a choice between `alert.v2` and re-pinning the digest.
- **Every collection field in every published contract is optional in its
  schema** — `AlertDetail.signals` included, because a `default_factory` makes a
  Pydantic field not-required. The server always sends them; a generated client
  is nonetheless correctly typed as though they might be absent, and every
  consumer must defensively default. It cannot be fixed without changing the
  models, which would move `alert.v1`'s bytes.
- **There is still no CI, and it is now the largest gap here.** No `.github/` at
  all. The digest freeze, the DDL hook, the cursor hook, the `ON CONFLICT` grep
  and the console's source scans all run only when a human runs them, and there
  are now two suites and two package managers. `console/package.json` pins
  exactly and commits a lockfile; `requirements.txt` pins nothing and has none,
  so the two halves of this repository disagree about reproducibility.
- **Further defects are recorded in [WEEK5-PLAN.md](WEEK5-PLAN.md)**, none of
  them owned by this list. Closed in session 1: dispositions carry provenance.
  In session 2: an authored rule has something to fail against. In session 3:
  the version counter moves when a definition moves and the definition is kept
  (**D1**), and a shadow rule is scored, recorded and allowed to act on nothing
  (**D2**). Session 4 closed no defect — it is the third simulation — and
  answered **O4**. The ingest work that followed found and fixed **D10**: the
  cluster builder allocated ids by candidate index, so the second device-fanout
  cluster took `RING-1187` from the first. Unreachable for four weeks because the
  fixtures build exactly one cluster, and reachable the moment links can arrive
  over HTTP. Session 5 found **D11** — `bootstrap.ps1` threw on a healthy
  `docker compose up -d`, because a stderr warning under
  `ErrorActionPreference = 'Stop'` is a terminating error; fixed, and it only
  ever fired on a cold start.
- **A hypothetical charge is scored as a transaction and nothing else.**
  `POST /simulate/transaction` evaluates the fabricated row in one lane, as a
  `transaction` subject. The card, account, customer and merchant it references
  are not re-evaluated, so S-077 and L-203 never appear in the answer even though
  a real cycle would have reached them. Graph and cluster features are read at
  their stored value, because their driver is the link layer rather than
  `transactions`. Every one of those is named on the payload's `limits` rather
  than left to be discovered.
- **A rule what-if is bounded and says so.** `POST /simulate/rule` evaluates the
  most recent 2,000 subjects of the rule's own subject type by default, and
  publishes `subjects_available`, `subjects_evaluated`, `sample_cap` and
  `truncated` so every rate under it carries the denominator it was computed
  over. `would_alert` counts evaluations that *would* raise a case, not cases:
  §9's folding, restatement and suppression all happen at persist time and
  nothing is persisted, so it is an upper bound and the payload names it as one.
- `week1-data-model.md` is **superseded by the seed files** where the two
  disagree — catalog size, three `entity_type` values, and the price of
  `country_is_new_for_customer`. It is kept as a Week-1 artifact rather than
  quietly edited.
- `architecture.md` is likewise a **planning artifact, kept unedited**; where it
  and the code disagree, `HANDOFF.md` records why. The four that matter:
  §12's example payload shows `triggering_events`, which lives on `queue.v1`
  because alert.v1 is frozen and has no field for it; §8's table lists a
  `delivered` execution outcome, which migration `0013`'s CHECK spells
  `completed`; §9 describes `open_window` as part of the dedup key, which is
  instead applied as a predicate at fold time so that a published key does not
  change meaning as time passes; and §13's acceptance names a report generated
  for `TXN-48251`, which after the reprice has an empty pool and no alert — the
  test binds to `TXN-48300`, which carries all three of T-021's mitigators and
  the veto.
