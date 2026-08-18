# GlassBox

An additive, explainable risk decisioning engine. Every score is the sum of the
signals shown next to it, every action names the rule that chose it, and both
properties are enforced in three independent layers.

Detection logic lives in **rows, not code**: a rule is a `rule_definitions` row
plus `rule_conditions` rows, and a feature is a `feature_catalog` row carrying
its own computation spec. The Python in `src/glassbox/` interprets those rows —
it does not encode any particular pattern.

**New here?** [WALKTHROUGH.md](WALKTHROUGH.md) explains the whole system end to
end — plain English first with every term defined, then the same journey with
the file and function at each step. This README assumes you know what the system
is for.

---

## Run it

```bash
docker compose up
```

That is the whole of the setup. No Python, no Node, no virtualenv — only Docker.
It brings up PostgreSQL 16, builds the database, and serves:

| | |
|---|---|
| **Console** | <http://localhost:5173/console/> |
| **API** | <http://localhost:8000> (docs at `/docs`) |
| **Database** | `localhost:55432` — 55432, not 5432, so a local PostgreSQL does not collide |

Sign in with `analyst-token` or `admin-token`. Reads are open, so the dashboard,
a case and the KPI tiles render before you do.

The `init` service builds the database and exits; `api` waits for it to
**succeed** rather than to start, so the service never comes up in front of an
empty one. The database build is roughly three minutes; the first run also
builds the two images. Add `--build` after changing a Dockerfile — compose
reuses an existing image otherwise.

`init` rebuilds every time, which is what makes the demo identical every time —
and is also why a rule authored through the console in a previous session does
not survive it. Runtime rules are rows, not seeds. To keep what is there:

```bash
docker compose up db api console      # leave the existing database alone
```

### See it react

The bootstrap builds a dataset. To watch the system respond to something it has
never seen — five card-not-present charges, one at a time, the fifth declined:

```bash
docker compose run --rm --no-deps api python scripts/demo_burst.py
```

See **A charge can be stopped** below for what that demonstrates.

### On a host with Python

```bash
docker compose up -d db                     # PostgreSQL 16 on :55432
cp .env.example .env
python -m pip install -r requirements.txt
python -m pip install -e .                  # so `python -m glassbox` resolves

python scripts/bootstrap_demo.py            # fixtures, database, both lanes,
                                            # settled actions, and the feature
                                            # that closes the loop — the same
                                            # script the container runs
python scripts/condition_report.py          # which conditions are mispriced
python scripts/calibrate_bands.py           # where the band cutoffs should sit
python scripts/kpi_report.py                # the nine tiles
python scripts/case_report.py --alert 5 --citations   # a filing draft, sourced

psql "$GLASSBOX_DSN" -f db/acceptance/verify_scores.sql   # 87 / 68 / 64 / 58 / 0
pytest                                                    # 615 tests
python -m glassbox serve                                  # API on :8000, cycle every 30s

python scripts/demo_burst.py                # five charges; the fifth is declined
python scripts/demo_burst.py --http         # the same, through a running service
python scripts/demo_burst.py --clean        # take it back out
```

On Windows, `.\scripts\bootstrap.ps1` does all of the above in one command.

`verify_scores.sql` keeps its psql meta-commands and must be run with `psql`
from the repository root. That is deliberate: two execution paths, `psql` for
human-readable demo output and pytest for assertions over the same facts.

One pairing fails quietly if you miss it. A container reaches the service over
the Docker bridge, which a loopback socket refuses — so a host-run API must be
started as `GLASSBOX_HOST=0.0.0.0 python -m glassbox serve`, or every request
from the console fails exactly as though nothing were running. `.env.example`
carries both halves.

### The console

Served at **`:5173/console/`** by the `console` service. Node is never installed
on the host: the packages `console/package-lock.json` pins live in an image and
a named volume, and the source is bind-mounted, so an edit is the thing Vite
serves.

```bash
docker compose run --rm console npm test     # 85 tests, ~3s
docker compose run --rm console npm run build
```

`cd console && npm install && npm run dev` works too if you would rather have it
locally. `app.py` also serves a built `console/dist` at **`:8000/console`** —
but only when the API runs **on the host**, because the api container is built
without the console directory. In Docker, the console is `:5173`.

See [console/README.md](console/README.md) for what the console holds itself to.

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
priced at +50 — reverse-engineered in Week 1 so that case's points would sum to
its displayed score — and earned 6.78% precision over 398 firings, 4.4× the cost
per unit of measured precision of any comparable aggravator. Seed `0026` reprices
it to **+12**. The pool is then `12 − 9 − 6 − 4`, the mitigators outweigh the
accusation, and consolidation publishes nothing rather than a negative score.

Alongside them sits a scored population: ~9,800 transactions, ~300 declines,
~120 refunds, a 22-decline card-testing burst, a refund-abuse customer and 22
labelled fraud clusters deliberately sized so most fall *below* R-114's line.

Every one of those 9,923 decisions records what became of it — `raised`,
`folded`, `restated`, `suppressed` or `no_authority` — and every condition it
evaluated, fired or not: 79,068 ledger rows. That is what turns "alert volume"
from a count of evaluation cycles into a count of cases, and "which conditions
are mispriced" from a guess into a query.

It also makes the nine KPI tiles arithmetic instead of illustration. Over the
seven days to the reference instant: 5 cases against 1 the week before, a 95%
false-negative rate over 80 labelled-fraud decisions, 4 preventive actions issued
and 0 prevention false positives out of 4. Every number carries its denominator,
its window, and — where a script settled it rather than an observation — a flag
saying so.

---

## How it fits together

A directory-level map. [WALKTHROUGH.md](WALKTHROUGH.md) walks the same ground
file by file, and `HANDOFF.md` records why each piece is shaped as it is.

| Where | What is in it |
|---|---|
| `db/migrations/` | `0001-0008` Week 1 schema · `0011-0014` computation specs, the resolution graph, decision detail, version stores, executions, clusters, and bitemporal `feature_values` (the one non-additive migration) · `0023` alert routing, fold state, exposure, the condition ledger · `0029-0032` who decided a case, the shadow gate, provenance, and the watermark that makes a cycle incremental |
| `db/seeds/` | `0009-0010` the catalog and the four rules · `0015-0021` the computable specs, 11 resolution edges, rule policy, score bands, novelty baselines, driver filters · `0024-0028` hygiene policy, action routing, the repriced condition, the calibrated bands, refund-abuse features — INSERTs and UPDATEs applied by hand from a report's evidence · `0031` definitions predating the publish step |
| `db/views/` | the invariants, condition performance, and the five KPI views the tiles are computed from |
| `db/acceptance/` | `verify_scores.sql` — read-only, no hardcoded subject ids |
| `src/glassbox/features/` | the injection boundary, 17 named reducers, spec → parameterised query, and an append-only point-in-time-correct runner |
| `src/glassbox/graph/` | clusters from the link layer |
| `src/glassbox/engine/` | resolve → point-in-time read → conditions → score → consolidate → band → precedence → persist → execute → settle. `evaluation.py` holds the order, which is the design |
| `src/glassbox/explain/` | the eight relations, the `Quoter` every number passes through, the copilot's three chips, and the case report that says it is a draft |
| `src/glassbox/rules/` | what an authored rule must fail against, and definition → version → snapshot or none of it |
| `src/glassbox/ingest/` | both doors, shared record validation, arrivals, the event-time watermark, and the cycle |
| `src/glassbox/contract/` | `alert.v1` (frozen, digest-pinned) and its eight siblings: queue, executions, kpis, explanation, dispositions, simulation, catalog, ingest |
| `src/glassbox/api/` | 15 read endpoints, 12 writes, 3 simulations. `auth.py` holds two demo users; reads are open |
| `console/src/api/` | `openapi.json` → `schema.d.ts` → `types.ts`, generated offline and committed. `client.ts` is the only place the console talks to the service |
| `console/src/components/` | `ScoreBar` (one bar, three payloads), `DecisionFrame` (`persisted` is what makes them unconfusable), `SystemStrip` (the only thing that says whether anything runs), `Tile`, `HeadlineStrip` |
| `console/src/screens/` | `Dashboard` — the queue and the measurement tiles on one surface, two sub-tabs — plus `Alert`, `Rules`, `RuleDetail`, `RuleAuthor`, `Simulate`, and `Authorize`, which commits and says so first |

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
14. **Settle** — outcomes onto executions, dispositions onto cases, events into the log.

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
by a test. A missing mitigator raises the score, which is correct arithmetic and
the wrong action — so it also strips preventive authority.

**Resolution failure is never a silent zero.** No entities, or more than the
fan-out policy allows, produces a recorded degradation rather than a partial
score.

**`feature_values` is bitemporal and append-only.** `as_of` is when a fact was
true, `computed_at` is when we learned it. A recomputation is an INSERT. Never
write `ON CONFLICT … DO UPDATE` against that table — it silently destroys the
value a past decision was made on, and a test greps for it.

**The read contract is frozen, and a digest enforces the freeze.**
`contract/alert.v1.schema.json` is generated from `contract/models.py` and
committed; a test regenerates it in memory and asserts byte-equality *and*
checks its sha256. Byte-equality alone passes if you change a model and re-run
the exporter, so the digest is what makes the freeze real. A breaking change
becomes `alert.v2.schema.json` and v1 keeps being served.

**New read surfaces are siblings, not new versions.** `queue.v1` and
`executions.v1` publish what alert hygiene and action execution produced —
`triggering_events`, exposure, priority, challenge outcomes — without touching a
byte of alert.v1. `Subject`, `Signal`, `Action` and `Evidence` live inside
alert.v1's `$defs` closure, so adding a field to any of them for the queue's
benefit would break the digest. That is why `models.py` was left alone.

**One case, many triggering events.** An alert carries a `dedup_key`, and a
repeat evaluation inside the subject type's `open_window` folds onto it instead
of raising a second case, so running the async cycle N times over a static
dataset produces the same alert count for every N. If the repeat scores
*higher*, the case is **restated**: it re-points at the worse evaluation and its
signal set is replaced in the same operation, never one without the other,
because an alert's signals must always be exactly one decision's pool or the
score bar stops adding up.

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
on fraud — as the worst condition in the catalog rather than the best.

**Nothing is challenged twice for the same situation.** Preventive actions are
issued when a case is *raised*, not when it folds; a notification goes out again
on a restatement. A ring re-evaluated every fifteen minutes would otherwise send
the customer 96 step-ups a day, and "block rate" counted off decisions would be
double the number of customers actually affected.

**A negative risk score is not a claim the model can make.** When the deductions
consume the accusation there is nothing left to publish, so consolidation drops
the pool whole — score 0, empty signal set. Clamping to zero while still showing
the signals would break `sum(signals) == score`, and that invariant is the
product. `TXN-48251` is the case: `12 − 9 − 6 − 4 = −7`, published as 0 with
nothing on the bar.

**Every tile names its window, its denominator, and what it cannot tell you.**
Deltas compare against the immediately preceding window of the same length and
nothing else — when the dataset does not reach back that far, the delta is null
and the payload says why. The false-negative rate is exact against planted ground
truth and meaningless beyond it; the challenge pass rate was settled by a script,
not a customer; `fail_mode` records the lane's policy and has never seen a
failure. All three ride with a `synthetic` flag and a caveat rather than a
footnote.

**The explanation surface is deterministic, and can only read one case.** The
copilot and the case report template over eight relations for the alert in view
— a cursor hook in the test suite fails the build if anything else is queried.
Every number in the output passes through a `Quoter` that records the table and
primary key it came from, or the formula if it was derived, and a test extracts
every numeric token and checks it traces back. Mitigators and applied vetoes are
not optional: `CopilotAnswer` raises rather than return an explanation that
quotes a score without them. No language model is involved in any field of any
payload — a design choice rather than a limitation, because the explanation
surface of a glass-box system should not itself be a black box.

**A rule is published, not saved.** `POST /rules` writes the definition,
snapshots it into `rule_versions` with the actor who published it, and lands the
rule in **shadow** — where the engine scores it on every applicable subject,
records every condition it looked at (`decision_conditions.is_shadow`) and the
action it would have taken (`decisions.shadow_action`), and lets it alert nobody
and challenge nobody. `POST /rules/{id}/promote` is what makes it act, and it is
a separate call because it is a separate decision. The version counter moves only
when the definition actually moved: `decisions.rule_version_set` records the
version an evaluation *read*, and a counter that ticks on every keystroke makes
that set meaningless. Deleting is retiring — the foreign keys refuse to remove a
rule that ever acted, which is the audit trail defending itself.

**Reads are open; the surfaces that leave a mark are not.** An analyst marks a
case through `POST /alerts/{id}/outcome`, authenticated by a bearer token that
resolves to one of two demo users (`api/auth.py` — a static map, not
authentication, and it says so). The actor comes from the principal and never
from the body. `alerts.status` stays engine-owned: the engine raises, folds,
restates and suppresses, the analyst owns the verdict, and the queue asks what it
actually means — *has a person worked this?* A case closed by `resolve_actions.py`
has not been, which is why `case_outcomes.source` exists and why a synthetic pass
does not empty the queue.

**An authored rule has to fail loudly or not at all.** Every way of getting a
rule wrong used to produce a rule that did *nothing*: `conditions.fires()`
returns False for an operator it does not recognise, and for a numeric operator
with no threshold, so a typo yielded a rule that never fired, never errored, and
appeared in the ledger as a condition that simply never matched.
`rules/validate.py` turns twenty-two of those into rejections an author can read
— including the two §5 and §7.3 care about most: a mitigator on a feature
carrying a default (it can never be observed *absent*, so it can never strip
preventive authority) and a `prevent_threshold` below its `review_threshold`. The
operator list it checks against is the same tuple the interpreter dispatches on,
and the action, subject-type and reason-code vocabularies are the same `ref_*`
rows the foreign keys enforce and `GET /reference` serves — so the console's
dropdowns cannot offer something the validator will refuse.

**A rule can be tested against history before it exists.** `POST /simulate/rule`
applies a candidate to the control plane inside the same rolled-back scope
`/simulate/subject` uses, loads the engine context *after* the draft is applied,
and runs the ordinary pipeline — there is no "simulation mode" in the evaluator,
because a second evaluation path would be a second answer. It takes an edit as
well as a new rule, which is what makes its diff worth reading: repricing
`session_geo_jump_km` from 18 to 5 reports `TXN-48291` moving 87 → 74 and
`challenge` → `alert` *before* the seed is written.

**Dispositions are append-only and the latest one wins.** A correction is a
second row; the first judgement stays in the record. `v_kpi_cases` publishes the
latest as the verdict and still measures the triage clock to the *first* one,
because a correction hours later does not mean triage took hours longer. Every
tile derived from a disposition reads that provenance instead of asserting it.

**A simulation is the same engine with nothing written down.**
`POST /simulate/subject` re-derives a subject's decision — same planner, same
point-in-time read, same precedence — inside a scope that rolls back, and
publishes `persisted: false` on the wire rather than leaving a caller to infer it
from the URL. The bar comes from `engine.persist.ranked_signals`, the one
function that also writes a stored alert's signals, so a simulated bar and the
alert it predicts cannot disagree. With `replay_as_of` set to a stored decision's
`decided_at` it answers *"what did that decision see?"* rather than *"what would
we say today?"* — the audit question the bitemporal store exists to make
answerable.

**A charge can be stopped, and that is new.** Every week before this one detected
fraud correctly and could prevent none of it: transactions arrived already
stamped `auth_result='approved'`, so a `challenge` was a note attached to money
that had already moved. `POST /authorize` closes that: the row is inserted as
presumed-approved (it has to be — `card_cnp_count` counts approved CNP charges,
and a charge that does not count itself is the fifth of five reading four), the
features it drives are recomputed at its own instant, the inline lane runs, and
**precedence decides the `auth_result` before the transaction commits**. Which
actions stop a charge is read off `ref_action.is_preventive`, not a list in
Python. A `challenge` commits as `declined` with
`decline_reason='step_up_required'`, because a step-up nobody has answered has
not been passed — and that is how 3DS behaves. The write-back happens inside the
same uncommitted transaction as the insert, so there is no moment at which a
blocked charge existed in this database as an approved one.

**The decision invalidates the evidence it was made on, and both survive.** A
charge declined after being counted as approved is no longer an approved CNP
charge, so the scoped feature pass runs a second time after the write-back.
`feature_values` is append-only and bitemporal, so the recomputation is an INSERT
with the same `as_of` and a later `computed_at`: the decision keeps the evidence
it actually saw, and the store ends up describing what really happened. This is
the first place bitemporality earns its keep on a write path rather than in an
argument.

**Something is actually running.** `glassbox serve` starts a daemon thread that
turns the engine over every `GLASSBOX_CYCLE_SECONDS` (default 30; `0` disables
it, and the test suite sets `0` because a thread committing into a rolled-back
test transaction is the least debuggable failure this project could have). **A
tick evaluates what arrived, not the population** — `affected_subjects` narrows
to the transactions, the entities behind them and the clusters those entities
belong to, because `plan_evaluations` otherwise re-scores 9,844 transactions to
notice one charge. The narrowing is by *subject* and never by rule or feature, so
a re-evaluated subject is still evaluated against its whole history and an
incremental tick and a full pass give the same answer. On the shipped fixtures a
tick that finds one new charge takes ~90 ms against ~20 s for a full pass, and a
tick with nothing to do takes 7.

**Two doors, and they mean opposite things.** `/authorize` asks for a decision;
`/ingest/transactions` reports one somebody else already made. The models carry
the distinction rather than a docstring: an `AuthorizationRequest` has no
`auth_result` and no `decline_reason` because the engine sets them, and a
`TransactionRecord` has both plus `synthetic_label` because planted demo data
should be labelled or it is invisible to every precision number in the system.
They are separate endpoints rather than one with a `decide=true` flag, which
would be one typo away from approving a charge the engine was never asked about.
Events and links have their own doors because two of the four rules cannot be
reached without them.

**A device is observed; an account is opened.** An unrecognised `device_id` is
registered at the instant it is first presented — by all three writers, through
one helper, between validation and the insert — while an unknown `card_id` is
refused. That is not a convenience: `device_first_seen_min` is measured from
exactly that instant and is 21 of R-114's 87 points. A link to an account nobody
opened *is* refused, and nothing else would have caught it —
`entity_links.to_id` is polymorphic with the type in a neighbouring column, so
there is no foreign key, and a phantom edge builds a cluster out of nothing whose
ring looks exactly like a real one.

**A charge that never happened can be scored, and the feature layer moves with
it.** `POST /simulate/transaction` inserts a fabricated row, runs a feature pass
scoped to the instant it claims to have occurred at, evaluates it, and rolls all
three back. The pass is what makes the answer new rather than borrowed: a sixth
card-not-present charge on `CARD-4417` makes the engine read `card_cnp_count`
**6**, where the stored value at that instant is 5. Which features the pass runs
is derived from each compiled spec's **driver** relation, not from its source: an
arriving transaction recomputes `min_since_password_reset`, whose source is
`events`. Everything it did not recompute is named on the payload with the
reason, as are four limits it cannot get past. It is **admin-only**: every other
defence here is on the payload, and the role is the one that does not depend on
anyone reading it.

**The console asserts nothing the payload does not say, and its claims are
checked against its own source.** It is the first surface here that a person
judges by looking rather than by reading a payload, which makes it the easiest
place to break a guarantee the rest of the system spent five weeks enforcing. So
`console.test.ts` greps the console for nine of them: one score-bar component
renders `alert.v1`, `simulation.v1` and `ingest.v1` and there is exactly one of
it; `POST /authorize` — the one endpoint that commits and can decline a charge —
has exactly one call site; liveness has one source and no fallback; no payload
shape is redeclared; the queue filters and never sorts, because a column header
that reordered it would override a published, explained order with an unexplained
one; and a headline figure is formatted by the same code as the tile it restates,
so the two cannot disagree. The bar sums contributions as **scaled integers**,
because Pydantic sends `Decimal` as a string and parsing those into floats would
hand back the exactness `sum(signals) == score` depends on. `persisted` decides
the frame around a decision and beats `stopped`, so a rolled-back decision that
*would* have declined a charge never carries the frame that means money did not
move.

---

## Extending it: what costs rows, and what costs code

Two detectors are added this way, and both are tests rather than paragraphs.
`tests/test_extension_cardtesting.py` builds a card-testing detector on a
**merchant** subject with two INSERTs; `tests/test_extension_refundabuse.py`
builds a refund-abuse detector on a **customer** subject, with two conditions
AND'd across groups. A psycopg hook records every statement executed in either
test body and fails if any of them is DDL, so the claim is checked rather than
asserted.

The refund detector also shows the claim's edge. The pattern wants refunds as a
*ratio* of purchases; §3.1 names a `ratio` reducer and `aggregations.py`
deliberately does not implement one — writing it to make this land would have
been exactly the data-engineering ticket the table below describes, for the very
feature offered as evidence that growth costs rows. So it is built from `count`
and `sum`, and the honest cost is that it cannot normalise for customer size.

| Change | Cost |
|---|---|
| New rule over existing features | **INSERTs.** Two rows. |
| New feature using an existing reducer | **INSERTs.** A `feature_catalog` row plus a runner pass. |
| New feature needing a **new reducer** | A data-engineering ticket — a Python function in `aggregations.py`. |
| New subject type beyond `ref_subject_type`'s seven | A code change: the planner needs to know how to reach it. |
| New relation to read from | A code change: `ALLOWED_RELATIONS` is a deliberate allow-list. |

§3.1 names seven reducers; `aggregations.py` implements seventeen, and the 24
catalogued features use sixteen of them. That does not break the design — a named
reducer is a Python function, not an expression language, so no admin-authored
text ever becomes SQL. But it does mean "growth is catalog rows, not code" is
true for the common case and not for all cases.

---

## Tests

```bash
pytest                                     # 615 tests, ~120s including a rebuild
pytest tests/test_degraded.py -v

docker compose run --rm console npm test   # 85 tests, ~3s
```

Tests use the same docker-compose PostgreSQL, in a dedicated `glassbox_test`
database that `conftest.py` drops and recreates at session start, so state is
never inherited. A session fixture migrates, seeds, loads fixtures, runs the
feature layer and runs both lanes once; every test then runs inside a transaction
rolled back at teardown, so tests are order-independent and a test that mutates
rules or catalog rows cannot leak.

The Python suite covers §3–§15 module by module, and four of its hooks are worth
knowing about because they fail the build on things a reviewer would otherwise
have to notice: a psycopg hook that rejects DDL inside the two extension tests, a
cursor hook that rejects any relation outside the explanation allow-list, a grep
for `ON CONFLICT` against `feature_values`, and the sha256 digest on
`alert.v1.schema.json`.

The console's own suite checks what a server-side test cannot see:

| Module | Covers |
|---|---|
| `format.test.ts` | the bar sums exactly, where a float sum would not |
| `ScoreBar.test.tsx` | the arithmetic on screen, a payload that does not add up, and that no mitigator is ever hidden |
| `DecisionFrame.test.tsx` | `persisted` decides the frame — and beats `stopped`, so a rolled-back decline never reads as a real one |
| `Tile.test.tsx` | copy comes off the payload; no delta without its baseline window; an absent value is an absence, not a zero; and a caveat is never folded behind a disclosure |
| `SystemStrip.test.tsx` | three answers about liveness, including "could not ask" |
| `copy/tiles.test.ts` | the tile grouping drops nothing, duplicates nothing, and files an unknown tile rather than hiding it |
| `Dashboard.test.tsx` | one held set of figures behind one button; the sub-tab that does not reset the window; the filter that narrows without reordering |
| `routing.test.tsx` | the app is mounted under `/console`, and the router is told so |
| `App.routes.test.tsx` | every route resolves under that prefix, every nav href carries it, and the whole navigation can be clicked through |
| `console.test.ts` | the nine source-level claims listed above |

---

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `GLASSBOX_DSN` | `postgresql://glassbox:glassbox@localhost:55432/glassbox` | Dev database |
| `GLASSBOX_TEST_DSN` | …`/glassbox_test` | Dropped and recreated by the test session |
| `GLASSBOX_NOW` | `2026-01-15T15:00:00+00:00` | The fixtures' reference instant |
| `GLASSBOX_CYCLE_SECONDS` | `30` | Background cycle interval. `0` disables it — which is what the test suite sets |
| `GLASSBOX_API_TOKENS` | two demo users | `token:actor:role,…` |
| `GLASSBOX_HOST` | loopback | Must be `0.0.0.0` for a containerised console to reach a host-run API |

An ingested or authorized charge is dated at `GLASSBOX_NOW` unless it says
otherwise, and that matters more than it looks: every window feature (90s, 24h,
30d) is measured against history pinned to 2026-01-15, so a charge dated at wall
clock would see a card with no past at all. The cycle's watermark is event time
for the same reason.

---

## Known gaps

- `new_payee_then_drain` is `source_kind='sequence'` and the sequence runner is
  unbuilt. It is the single value the generator hand-seeds, in a labelled
  `HAND_SEEDED_FEATURES` block. S-077's other three features are computed.
- **Only `transaction` has a calibrated band cutoff.** `account` and `network`
  have one scoring subject each, and a cutoff derived from n=1 is n=1 wearing a
  calibration's clothes. Both keep the inherited 70/45 and say `UNCALIBRATED` in
  `score_bands.basis`; `test_calibration.py` fails if either stops saying so.
- **The calibrated cutoffs change no outcome on this dataset.** 70/45 → 75/40
  produces an identical partition; the lines moved *away* from the observed
  scores rather than across them.
- **Band cutoffs are maximum-gap, not risk-appetite.** They support "no observed
  subject sits near this line" and nothing stronger. A cutoff encoding an
  appetite needs dispositions at volume, and §8's denominators are single digits.
- **One reprice is unversioned, and it is the one that moved a signed-off
  score.** Seed `0026` repriced `country_is_new_for_customer` from +50 to +12
  before a version counter existed, so T-021 version 1 names two definitions.
  Seed `0031` backfills the *current* definition at version 1 rather than
  inventing a version 2 — a retroactive version would leave stored decisions
  pointing at a v1 no snapshot exists for. Recorded in `0026` and `HANDOFF.md`
  §W4.2.
- **Editing a rule deletes the condition ledger behind its old conditions.**
  `decision_conditions` references `rule_conditions` with `ON DELETE CASCADE`
  and an edit replaces conditions wholesale, so repricing a condition destroys
  the firing history that found the misprice. The definition survives in
  `rule_versions`; the per-firing evidence does not. Named in `rules/publish.py`.
- `ratio` is named by §3.1 but unused, so it is deliberately not implemented — a
  spec asking for it fails at compile time rather than returning a number nobody
  defined.
- **Challenge outcomes are synthetic.** Nothing external answers a step-up, so
  `scripts/resolve_actions.py` settles them against `transactions.synthetic_label`.
  Every such row is stamped `synthetic = TRUE` and `executions.v1` puts the flag
  on the wire.
- **§8's denominators are single digits.** A full pass authorises four challenges
  and two holds, so prevention precision is n=4.
- **The prevention-false-positive count is zero, and that is a result.** All six
  preventive actions land on fraud-labelled subjects, so nothing is dispositioned
  `confirmed_legit`. The join works; it has nothing to find here, and
  `test_execution.py` exercises it on a constructed case.
- **Suppression is unreachable on the shipped fixtures.** All seven alerting
  subjects have exactly one `dedup_key`. `test_hygiene.py` inserts one to reach
  the path.
- Acceptance is checked against fixtures, not the population. §4 and §5 are
  *demonstrated*, not stress-tested.
- **The false-negative rate is 95%, and that is the generator working.** The
  labelled cohort was sized so most clusters fall below R-114's line. A cohort the
  rules caught entirely would read 0% and prove nothing.
- **Nothing ships in shadow, so the shadow columns are NULL across the
  population.** `test_shadow.py` shadows R-114 and watches `TXN-48291` go from 87
  / `challenge` to 0 / `allow`. A consequence:
  `v_condition_performance` does not separate shadow firings from live ones,
  which is invisible here and would dilute `mean_contribution` for a condition
  measured across a promotion.
- **A stopped charge has no second act.** `/authorize` declines with
  `step_up_required`, which is what 3DS does — but nothing here answers a
  step-up, so the demo shows a charge being stopped and never one being released.
- **Dimension rows cannot be ingested, on purpose.** A device is observed and so
  is created; a card, account, customer or merchant is *opened*, an onboarding act
  this system does not model. The cost is specific: a mule ring needs four
  accounts, so a genuinely new ring cannot be ingested and the ring demo reuses
  existing accounts.
- **Ingested events are not idempotent.** `transactions` dedupe on `txn_id` and
  `entity_links` on what the edge means; `events` have neither a primary key worth
  using nor a natural one, so re-sending a batch appends it again. Inventing a key
  would be inventing a fact, so the receipt publishes `idempotent: false`.
- **The cycle is at-least-once.** A tick that dies advances no watermark and the
  next re-reads the window. Safe rather than lucky — the feature runner is
  append-only and §9's folding means a re-evaluated subject produces the same
  alert count.
- **The inline lane's 50 ms p99 is a design target.** On the shipped fixtures an
  approving charge is ~50 ms and one that raises a case and issues a step-up is
  ~200 ms, on a laptop, against a local Postgres, with the scoped feature pass
  inside the measurement. Not a throughput claim.
- **Still deferred:** the `sequence` source kind, and the batch/incremental
  consistency test.
- **The published contract schemas contain dangling `$ref`s.**
  `export_contract_schema.py` uses a document-root-absolute `ref_template` while
  Pydantic nests `$defs` per model, so `#/$defs/Signal` in `alert.v1` resolves to
  nothing. True since Week 2 and harmless until something tries to resolve one —
  which is what a TypeScript generator does. The console is therefore generated
  from the **OpenAPI document**, where FastAPI hoists every model into
  `components.schemas`. That routes around the problem rather than fixing it.
- **Every collection field in every published contract is optional in its
  schema** — `AlertDetail.signals` included, because a `default_factory` makes a
  Pydantic field not-required. The server always sends them; a generated client
  is correctly typed as though they might be absent. It cannot be fixed without
  moving `alert.v1`'s bytes.
- **`:8000/console` only works when the API runs on the host.** The api image is
  built without the console directory and the api service mounts no volumes, so
  `_mount_console` finds no `console/dist` inside the container and registers no
  routes. In Docker the console is `:5173/console/`.
- **There is still no CI, and it is the largest gap here.** No `.github/` at all.
  The digest freeze, the DDL hook, the cursor hook, the `ON CONFLICT` grep and the
  console's source scans all run only when a human runs them, and there are now
  two suites and two package managers. `console/package.json` pins exactly and
  commits a lockfile; `requirements.txt` pins nothing and has none, so the two
  halves of this repository disagree about reproducibility.
- **A hypothetical charge is scored as a transaction and nothing else.**
  `POST /simulate/transaction` evaluates the fabricated row in one lane, as a
  `transaction` subject; the card, account, customer and merchant it references
  are not re-evaluated, so S-077 and L-203 never appear even though a real cycle
  would reach them. Named on the payload's `limits`.
- **A rule what-if is bounded and says so.** `POST /simulate/rule` evaluates the
  most recent 2,000 subjects of the rule's subject type by default and publishes
  `subjects_available`, `subjects_evaluated`, `sample_cap` and `truncated`.
  `would_alert` counts evaluations that *would* raise a case, not cases — §9's
  folding happens at persist time and nothing is persisted, so it is an upper
  bound and the payload names it as one.
- **Further defects are recorded in [WEEK5-PLAN.md](WEEK5-PLAN.md).** Closed
  since: dispositions carry provenance; an authored rule has something to fail
  against; the version counter moves when a definition moves (**D1**); a shadow
  rule is scored, recorded and allowed to act on nothing (**D2**); the cluster
  builder allocated ids by candidate index (**D10**); and `bootstrap.ps1` threw
  on a healthy `docker compose up -d` because a stderr warning under
  `ErrorActionPreference = 'Stop'` is a terminating error (**D11**).
- `week1-data-model.md` is **superseded by the seed files** where the two
  disagree, and `architecture.md` is a **planning artifact kept unedited**; where
  it and the code disagree, `HANDOFF.md` records why.
