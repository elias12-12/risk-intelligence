# Handoff — GlassBox

**Status:** Session 6 complete — demo readiness. The system had every capability
it needed and could not be shown: the control a promoted rule needed did not
exist, four views disagreed about a case's verdict, and running it took four
terminals. **Verified against:** PostgreSQL 16 (docker-compose), **615 Python
tests and 48 console tests green**, `alert.v1`'s digest unmoved since Week 2.

**Next:** the first item is still not a feature: **there is still no CI**, and
there are now two suites and two package managers that only run when a human
runs them. `docker compose up` makes that cheaper to fix than it was.

---

# Session 6 — demo readiness

`SESSION6-PLAN.md` is the input. Two items were defects found by querying the
live database; the rest were places where the surface was correct and
unpresentable. Every §number below is that plan's.

## S6.1 The two defects

**§1 — a promoted rule had no verb, and nothing said so.** Two rules were
authored, simulated, published and promoted through the console. No alert
appeared and no KPI moved. Both halves of the reason were correct behaviour:
`GET /cycle` is a status read that evaluates nothing, and `POST /cycle` consumes
what has ARRIVED — on a caught-up database it reports "nothing has arrived since
the last cycle" and stops. The cycle reacts to data. A new rule is not data.
`scripts/run_cycle.py --lane async` was the operation that would have worked,
from a terminal, on a machine with Python.

`POST /cycle/rescore?lane=` is the missing verb: a full-population pass that
ignores watermarks, admin-only, published as `RescoreReport` — a SIBLING of
`CycleReport` rather than a widening of it, because a re-score has no `since` and
runs neither the cluster nor the feature stage, and reporting those as zeros
would say "they ran and found nothing".

Synchronous, because it was measured rather than assumed: **async is 169
subjects in ~0.3s; inline_sync is ~9,850 in ~13s.** The plan's ~9,900 figure was
the inline lane. A job id and a polling console would be machinery bought for a
wait that does not exist.

One thing the tests found that the plan did not anticipate: `as_of` must be the
LATER of `reference_now()` and the frontier. `as_of` binds the point-in-time
read, so a re-score at `reference_now()` on a database whose newest charge
arrived after it — anything through `/authorize`, including the demo burst —
evaluates every subject as of an instant before that charge existed. That is this
endpoint's own bug one layer down.

**§2 — four views disagreed about a case's verdict.** Week 5 moved `v_kpi_cases`
to latest-wins so an analyst could correct the synthetic settler, and did not
move the three views that had COPIED its verdict CTE. For eight weeks,
dispositioning a case moved the false-positive rate, validation outcomes and
median triage while per-rule precision, prevention FP/TP and condition precision
stayed frozen on the script's original verdict. Half a screen responded.

**The fix is an edit to the three view files, not a seed** — which is the
opposite of what the plan proposed, and `migrate.py` settles it: views are
re-applied on EVERY run and applied LAST, so a seed redefining them would be
overwritten seconds later in the same migrate. `migrate.py:41-51` says as much —
"a view is edited in place; there is no `v_kpi_cases_2.sql`". The append-only
rule for `db/` covers migrations and seeds, not view definitions.

`tests/test_dispositions.py` gained three tests, and they were checked by
reverting one view and watching them fail. Two assert the DATA agrees; the third
asserts the SOURCE does, comparing the four copies of the CTE against each other.
The defect was duplication drifting, and only the third one catches it before it
reaches a screen.

## S6.2 One command

`docker compose up` brings up database, bootstrap, API and console on a machine
with only Docker. `Dockerfile` at the root builds one image; `init` runs
`scripts/bootstrap_demo.py` and exits, `api` waits for it to SUCCEED rather than
to start.

`scripts/bootstrap_demo.py` is new and is the point: fixtures, database, both
lanes, settled actions, and the feature that closes the loop — five steps that
lived only in `bootstrap.ps1`, which is PowerShell and cannot run in a container.
A second copy of them in a compose command would have been a second answer to
"what does a built GlassBox look like", drifting exactly as the four verdict CTEs
did. `bootstrap.ps1` now calls it.

**The database is rebuilt on every `up`**, by decision. Identical demo every
time; the cost is that runtime rules do not survive one. `RF-401` and `C-301`
were authored through the console and exist in no seed file.

Three things that would each have looked like the service being down:
`GLASSBOX_HOST=0.0.0.0` is mandatory in a container; the in-network DSN is
`db:5432` while the published `55432` keeps host tooling and `pytest` working
unchanged; and the console's `GLASSBOX_API` becomes `http://api:8000`.

**The console service has no `depends_on`, deliberately.** It proxies per request
rather than talking to the API at boot, so a dependency buys ordering nobody
observes — and costs a rebuilt database every time somebody runs
`docker compose run --rm console npm test`. Found by doing exactly that. Ad-hoc
commands against `api` want `--no-deps` for the same reason.

## S6.3 What the screens gained

**§5 KPI window controls.** `GET /kpis` already accepted `window_days` and
`as_of`; the console simply never sent them. Neither is sent until touched, so an
untouched screen produces the byte-identical payload it did before. Setting the
window to 30 and watching every delta correctly disappear — with the payload's
own `baseline_absent_reason` printed — is the demo beat. (The plan said that
reason was dropped by the console; it was already rendered.)

**§6 tile copy, console-side.** `requires` is no longer rendered — it stays on
the wire, because removing a published field to tidy a screen is a contract
change — and `basis` is replaced by two lines: what the tile means, and how it
was computed. `console/src/copy/tiles.ts` is the one place this console writes
words of its own about a number, and it is fenced: keyed on `tile.key` with the
payload as fallback, so a tenth tile added server-side explains itself on the day
it ships. Caveats are rewritten, never dropped, and the derived provenance note
keeps its counts.

**§7 reason codes.** `ref_reason_code.description` was already populated and
already published. What was missing was DIRECTION, and it is now derived rather
than listed: a code is mitigating when every `rule_conditions` row that can cite
it prices it downward. `VETO_APPLIED` and `DEGRADED_EVIDENCE` come from the veto
pass and no condition prices them, so `engine/precedence.py` now names them as
constants and the catalog reads those. A list of four codes typed into a screen
would have disagreed with the rules the first time one was repriced.

**§8 the replay ceiling is not broken.** The plan asked whether the placeholder
text fails to parse. It parses; the endpoint returns 200. The real finding is
worse and more interesting: **1 of 186,733 feature rows has `computed_at` at or
before the sample data's era.** Everything was computed in one pass at build
time, so any ceiling set back in the fixtures' own period is before every feature
row exists and the engine correctly finds nothing — score 0, every time. The
control is correct and unusable on a freshly built database. It is behind an
"advanced" toggle with that explanation, and the backend parameter is untouched:
it is the audit capability, not a live-demo control.

**§13 three scenarios, one call site.** The burst, the veto and ordinary traffic.
The charges are rendered as a table BEFORE anything is sent — the details are the
argument, and a decline whose charge nobody saw looks arbitrary. Scenarios 2 and
3 were verified against the real committing endpoint on a fresh build: the veto
returns `approved / 0 / allow` with `VETO_APPLIED`, and ordinary traffic returns
`approved / 0` with no signals at all. `console.test.ts` pins `api.authorize(` to
exactly one occurrence in one file.

There is deliberately no "clean up" button. This console has no endpoint that
erases raw capture, and adding one to tidy a demo would be the first thing in the
system that can make a committed charge stop having happened.
`scripts/demo_burst.py --clean` remains the way.

## S6.4 Three things to know before changing this

**The plan's §16 notes are partly stale, and the live database had moved.** By
the time the work started, `RF-401` and `C-301` HAD fired (something had been run
against the dev database), there were 13 alerts rather than 7, and the frontier
sat past the watermarks. The code findings were unaffected — `GET /cycle` is
still a read, and there was still no full-population endpoint — but the evidence
sections describe a database that had moved on. The claim that `WALKTHROUGH.md`
says T-021's aggravator is +50 does not hold either: the walkthrough's only `+50`
is a correct historical reference to the misprice that was found.

**`test_walkthrough.py` will fail on a line number you moved.** It did, three
times, for edits nowhere near the documentation: adding constants to
`precedence.py` and a model to `catalog.py` shifted `decide()`, `RuleDraft` and
`read_reference()`. 150 of the 615 tests are that file.

**Two contracts changed, both additively.** `ingest.v1` gained `RescoreReport`;
`catalog.v1` gained `ReasonCodeValue`. `alert.v1`'s bytes have not moved.
Regenerate with `scripts/export_contract_schema.py`, then
`scripts/export_openapi.py`, then `npm run types` — in that order, and commit all
three, in the same commit.

---

Newest week first. Each week's account is left as it was written; where a later
week moved something, the later account says so rather than editing the earlier
one.

---

# Week 5 — roles, dispositions, simulation, rule authoring, arrival, console

Six sessions rather than five: session 4b was not in the plan and was asked for
after session 4 landed, on a premise worth recording verbatim — *"if we can't
demo a detected fraudulent transaction, and there is no engine running, what is
the purpose of all of this?"* Half of that was wrong and the other half was
worse than stated. `bootstrap.ps1` did detect fraud end to end; what nothing
could do was **react** or **prevent**.

`WEEK5-PLAN.md` is the session-by-session record, and it is superseded by this
section. It keeps the open questions, the forty-three decisions taken along the
way, and the twelve defects found while taking them.

## W5.1 What changed, in one paragraph each

**Session 1 — the first write path in the project's history.** An authenticated
analyst can mark a case, and `POST /simulate/subject` re-derives a decision
without persisting it. `case_outcomes.source` (migration `0029`) is what makes a
disposition's provenance recoverable, and it turned out to be load-bearing twice:
three KPI tiles derive their caveat from it, and the queue filters on it, because
`resolve_actions.py` dispositions every open case in one synthetic pass and a
queue keyed on "has any disposition" would be empty after a normal bootstrap.
Closes **D3**.

**Session 2 — an authored rule has something to fail against.** `rules/validate.py`
rejects twenty-two shapes, every one of which previously produced a rule that did
*nothing* rather than a rule that failed — `conditions.fires()` returns False for
an operator it does not recognise, so a typo yielded a rule that never fired,
never errored, and appeared in the ledger as a condition that simply never
matched. `POST /simulate/rule` applies a candidate to real history inside a scope
that rolls back. Closes **D4**, answers **O1**, finds and guards **D7**.

**Session 3 — publishing, and the shadow gate, together.** A rule is published,
not saved: the definition is written, snapshotted into `rule_versions` with the
actor, and landed in **shadow**, where it is scored on every applicable subject
and acts on nobody. The version counter moves only when the definition moves.
Closes **D1** and **D2**, answers **O3**, finds **D8** and **D9**.

**Session 4 — a charge that never happened.** `POST /simulate/transaction`
inserts a fabricated row, runs a feature pass scoped to the instant it claims to
have occurred at, evaluates it and rolls all three back. The pass is what makes
the answer new rather than borrowed. Answers **O4**: admin only.

**Session 4b — arrival, prevention, and the scheduler.** `POST /authorize`
decides a charge *before the row is committed*, so a declined charge is never an
approved transaction; `POST /ingest/{transactions,events,links}` takes settled
rows, which is the only way to reach L-203 and S-077; `glassbox serve` runs the
cycle on an interval. Answers §18's decision 6 at 30 seconds. Finds and fixes
**D10** — the cluster builder handed the second cluster the first one's id,
unreachable for four weeks because the fixtures build exactly one cluster.

**Session 5 — the console.** `console/`: Vite, React, 24 modules, 40 tests,
bound to nine published contracts and asserting nothing they do not say. Answers
**D6** by routing around it, finds **D11** and **D12**.

## W5.2 The console, and the four claims it had to keep

The console is the first surface in this project that a person judges by looking
rather than by reading a payload, which makes it the easiest place to break a
guarantee the server spent five weeks enforcing. Four of them are enforced in
`console/src/console.test.ts` as checks on the source itself, in the same spirit
as the DDL hook and the cursor hook.

**One bar, three payloads.** `AlertDetail`, `SimulatedDecision` and
`AuthorizationOutcome` carry `Signal`, `Action` and `Evidence` unmodified and
deliberately (decision 8), so one component renders a stored alert, a what-if and
a live authorization. A second bar is the failure mode `persist.ranked_signals`
was made public to prevent (decision 13), moved up a layer where the server's
tests cannot see it — so a test asserts `bar-seg` appears in exactly one file.

**The bar adds up exactly, and the console had to work for it.** Pydantic
serialises `Decimal` as a *string*, which is precisely what lets
`contract/models.py` check `sum(signals) == score` with `==` and no tolerance.
Parsing those into JavaScript numbers to add them would hand the guarantee back:
`0.1 + 0.2 !== 0.3`, and a repriced condition is one seed file away from putting
a fractional contribution on a bar. `format.ts` sums them as scaled integers
(decision 39). Same argument `queue.py` makes about publishing rounded factors —
an explanation that is approximately true is the standard §1 refuses.

**`persisted` decides the frame, and it beats everything else.** The plan's
criterion is that a simulated decision and a real one are *never visually
confusable*: same bar, different frame. The frame is read off the payload's own
field rather than from which screen is rendering, because `/simulate/*` publishes
`persisted: false` on the wire exactly so a caller does not infer it from the
URL. Decision 40 is the sharp edge of it — a rolled-back decision that *would*
have declined a charge must never carry the frame that means money did not move,
so `persisted` wins in the direction that claims less.

**Nothing asserts liveness, and there are three answers, not two.** Whether the
engine is running comes from `GET /cycle`. Running, not running, and *we could
not ask* — `/cycle` needs a token, so a signed-out console genuinely does not
know. The third answer is the one worth having: it is also what the strip says
when the service is unreachable, which is the moment a console is most tempted to
keep showing the last cheerful thing it heard.

## W5.3 D6, and why the answer is "leave it alone"

The nine files under `contract/` contain dangling `$ref`s, and have since Week 2:
`export_contract_schema.py` asks Pydantic for `ref_template="#/$defs/{model}"`,
which is document-root-absolute, while Pydantic nests the referenced definitions
under each model's own `$defs`. Harmless until something tries to *resolve* a
`$ref` — which is what a TypeScript generator does.

The plan named three honest options and said to choose in the plan rather than in
a build script. The choice is the third: **generate the client from the OpenAPI
document, and leave the published schemas as the frozen artifacts they are.**

Fixing the exporter would move `alert.v1`'s pinned digest, which is the deliberate
act the pin exists to force — and spending the project's single most load-bearing
signal on `$ref` plumbing is not what it is for. `alert.v2` would spend a version
number on the same. FastAPI's document hoists every model into
`components.schemas`, so every reference resolves without a byte of the frozen
files being touched, and it is derived from the ROUTES, which is what the console
actually calls.

`scripts/export_openapi.py` writes it **offline** from the app object — no server,
no database — and `tests/test_openapi.py` holds three things: the committed copy
is current, every published contract is reachable from some route, and nothing
dangles. That last one is asserted rather than assumed, because if it ever stopped
being true, generating from here would silently produce the same broken output.

**D6 stays open.** Nothing here fixes the published schemas. It routes around
them, and the day somebody wants them resolvable, that work is still waiting.

## W5.4 What you can run right now

```powershell
.\scripts\bootstrap.ps1        # ~5 min cold, 454 tests
```

```bash
python -m glassbox serve                      # API on :8000, cycle every 30s
python scripts/demo_burst.py --http           # five charges; the fifth is declined

cd console
npm install && npm run dev                    # :5173, proxying /api -> :8000
npm test                                      # 40 tests
npm run build                                 # -> served at :8000/console
```

Sign in with `analyst-token` or `admin-token`. Reads are open, so the queue, a
case and the KPI tiles render before you do.

**Set `GLASSBOX_CYCLE_SECONDS` deliberately for console work.** The default 30
means rows appear that no click caused, which is correct for a demo and
confusing while building a queue screen. `0` disables it, which is what the test
suite sets — and the console will tell you which you picked, off `/cycle`.

The five signed-off scores are unmoved through the whole week:

| Subject | Rules | Score | Band | Action |
|---|---|---:|---|---|
| `TXN-48291` | R-114 | **87** | high | `challenge` |
| `TXN-48300` | R-114 + T-021 | **68** | elevated | `monitor` |
| `RING-1187` | L-203 | **64** | elevated | `hold` |
| `ACC-2201` | S-077 | **58** | elevated | `hold` |
| `TXN-48251` | T-021 | **0** | low | `allow` |

## W5.5 Two defects worth knowing about, found in session 5

**D11 — `bootstrap.ps1` died on a healthy bring-up.** `docker compose up -d`
writes `level=warning msg="No services to build"` to stderr, and under
`$ErrorActionPreference = 'Stop'` a native command writing anything to stderr
becomes a terminating `NativeCommandError`. So the container started, Postgres
came up healthy, and the script threw. The file already guards the `docker info`
poll against exactly this and explains why in a comment; the `docker compose`
line two dozen lines below never got the same treatment. **Fixed**, and it only
ever fired on a cold start — which is the honest reason four weeks of
`-SkipDocker` runs never hit it.

**D12 — every collection field in every published contract is optional in its
schema.** Pydantic marks a field with a `default_factory` as not-required, so
`alert.v1`'s `required` list is nine names and `signals` is not among them. The
server always sends it; a client generated from the schema is nonetheless
correctly typed `Signal[] | undefined` for a field that is never absent. **Open,
and it cannot be fixed without unfreezing** — making those fields required
changes the model, which changes `alert.v1`'s bytes and its digest. That is a
genuine `alert.v2` decision, not a typing convenience. The console defaults each
one to the same empty value the server would have sent, and says so where it
does; asserting presence would be a client deciding it knows better than the
contract it binds to.

## W5.6 Three things to know before changing the console

1. **`src/api/types.ts` contains no hand-written payload shape except one.**
   Every export is an alias into the generated `schema.d.ts`. The exception is
   `CycleState`, because `GET /cycle` has no `response_model` and OpenAPI can
   only type it as an open object — and a test pins that it stays the only one.
   If you find yourself typing `interface Signal`, the import is three levels
   deep and that is not a reason.
2. **Regenerate both files together.** `python scripts/export_openapi.py`, then
   `npm run types`, then commit both. `test_openapi.py` fails if the document is
   stale, which is the tripwire — without it a route whose `response_model`
   changed leaves the console typechecking green against a shape the service no
   longer serves.
3. **The §11 copy scan means the flagged strings cannot appear anywhere in
   `console/src`, including in a comment explaining them.** That is not
   over-reach; it is what makes the test real. `screens/Alert.tsx` paraphrases
   and says why — a test its own subject can defeat by citing it is not a test.

## W5.7 What is deliberately not done

- **There is no CI, and this is now the largest gap in the project.** No
  `.github/` at all. The digest freeze, the DDL hook, the cursor hook, the
  `ON CONFLICT` grep, the console's source scans — every enforcement mechanism
  the project's claims rest on runs only when a human runs it, and there are now
  two suites and two package managers. `requirements.txt` pins nothing and has no
  lockfile; `console/package.json` pins exactly and commits one (decision 43), so
  the two halves of the repository now disagree about reproducibility. A Postgres
  service container plus `pytest` plus `npm test` is roughly thirty lines.
- **A stopped charge still has no second act.** Nothing answers a step-up, so
  the demo shows a charge being declined and never one being released after the
  customer authenticates.
- **`D8` and `D9` are still open**, both with reasons in `WEEK5-PLAN.md`: an edit
  cascades away its own condition ledger, and `v_condition_performance` pools
  shadow firings with live ones.
- **The `sequence` source kind**, and with it the last hand-seeded feature value.
- **The batch/incremental consistency test.**
- **No error boundary in the console.** One that swallowed a `ContractViolation`
  would hide the single failure this system most wants to be loud.

## W5.8 Ledger against `architecture.md`

Unchanged from Week 4 — every numbered item of Part I is **DONE**, and Week 5
added nothing to that list because Week 5 was not a Part I week. What it changed
is the two things §15 and §18 described and no code had ever done: the topology's
third component now exists (`scheduler.py`), and the console and admin authoring
that four handoffs called "out of scope by decision" are built.

| Named in `architecture.md`, previously unbuilt | Status |
|---|---|
| §15's scheduler | **DONE** (session 4b) — `GLASSBOX_CYCLE_SECONDS`, default 30 |
| The console and admin authoring | **DONE** (sessions 2, 3, 5) |
| §11's "console copy that outruns the system" | **DONE** — neither string was ever written, and a source scan keeps it that way |
| §18 decision 6, the async cycle period | **ANSWERED** at 30 seconds, labelled a demo number |
| Part II: `rule_versions`, `feature_catalog_versions` | **POPULATED** (session 3) — they were empty through Weeks 2–4 |
| Part II: `source_model` on `alert_signals` | still never written, correctly — there is no model |

---

# Week 4 — calibration, KPIs, the second extension, the explanation surface

## W4.1 What changed, in one paragraph each

**§10 — the report's own finding is applied, and it moved a signed-off score.**
`country_is_new_for_customer` was +50, sized backwards in Week 1 so T-021's points
would sum to its displayed 31. Seed `0026` reprices it to **+12**: its measured
precision (6.78% over 398 firings) at the cost per precision point the catalog's
comparable aggravators actually charge. `points_per_precision_point` falls 7.4 →
1.8, level with `session_geo_jump_km` at 1.7 and `mcc_is_new_for_customer` at 1.6.
Seed `0027` calibrates the transaction band cutoffs to **75 / 40** and marks every
other subject type UNCALIBRATED in its own `basis`. This is the first thing in the
project to change a number that had been signed off, and §W4.2 is its blast radius.

**§11 — nine tiles, each computed from stored rows and each naming its window.**
Five `v_kpi_*` views carry the classification; `contract/kpis.py` carries the
window arithmetic and is the only place a window is defined. Every rate publishes
its numerator and denominator, every delta compares against the immediately
preceding window of the same length or is null, and the two tiles whose numbers
are synthetic say so on the wire rather than in a footnote.

**§13 — the copilot and the case report, deterministic, no model.** Three chips
and a filing draft, templated over the alert in view and nothing else. Every
number in the output passes through a `Quoter` that records the table and primary
key it came from — or, for a derived number, the formula — so "quoted, never
restated" is mechanical rather than careful. §18's open decision 7 is settled by
building: **no language model is involved in any field of any payload**, and
`model_backed: false` is published so a client can tell.

**§14 — both patterns now detect end to end via INSERT only.** `RF-401`, refund
abuse, on a **customer** subject with two conditions AND'd across groups. Seed
`0028` adds two catalog rows; the rule is authored inside the test, under the same
DDL hook card testing uses, which is now shared from `conftest.py`.

## W4.2 The repricing, and the defect it exposed

`TXN-48251` was **31**. It is now **0**, with an empty signal set.

The arithmetic: `12 − 9 − 6 − 4 = −7`. The plan and the Week-3 handoff both
predicted 0 via the mitigator-only rule — and both were wrong about the mechanism,
which is how the defect surfaced. Week 2's guard was *"no aggravating signal at
all → drop the pool"*. TXN-48251 **has** an aggravator, so the pool survived and
the engine published **−7**: a negative risk score, in the one decision out of
9,923 where the mitigators outweigh a repriced aggravator.

`engine/consolidate.py` now tests the **pool's sum** rather than the presence of an
aggravator. It is the same argument Week 2 wrote and never generalised: a mitigator
is a deduction from an accusation, and when the deductions consume the accusation
there is no accusation left. Dropping the pool keeps `sum(signals) == score` exact
where clamping the score to zero would have broken it —
`test_consolidation.py::test_the_drop_is_on_the_pool_sum_not_the_presence_of_an_aggravator`
pins it so it cannot be narrowed back.

Everything else held: `TXN-48300`'s 68 is untouched (this condition does not fire
there), and 87 / 64 / 58 are unmoved.

What moved with it: `fixtures/expected_scores.json` (and the regenerated
`.sql`), `test_degraded.py`, `test_precedence.py`, `test_conditions_ledger.py`,
`test_condition_report.py`, `README.md`, and the fixture tables in §W3.2 and §1
below — all amended in place with a note, not rewritten.

**Two tests read better after the change than before it.** §5's criterion is now
`0 → 2` rather than `31 → 40`: deleting the travel mitigator is the difference
between no case and a case, which is the sharpest form of "a missing mitigator
raises the score". And `TXN-48251` now demonstrates **two** of the three reasons
the condition ledger does not sum to the score — the satisfaction gate *and* the
net-negative drop — where before it showed one.

## W4.3 What you can run right now

```powershell
.\scripts\bootstrap.ps1        # run with the venv on PATH; ~3 min, 215 tests
```

```bash
psql "$GLASSBOX_DSN" -f db/acceptance/verify_scores.sql   # 87 / 68 / 64 / 58 / 0
python scripts/condition_report.py                       # no material misprice left
python scripts/calibrate_bands.py                        # recommends; never writes
python scripts/kpi_report.py --verbose                   # nine tiles, with derivations
python scripts/case_report.py --alert 5 --citations      # every number, sourced
python scripts/case_report.py --alert 5 --copilot        # the three chips
python -m glassbox serve
curl http://127.0.0.1:8000/kpis
curl http://127.0.0.1:8000/alerts/5/copilot
```

The five signed-off scores, with the one that moved:

| Subject | Rules | Score | Band | Action | Note |
|---|---|---:|---|---|---|
| `TXN-48291` | R-114 | **87** | high | `challenge` | |
| `TXN-48300` | R-114 + T-021 | **68** | elevated | `monitor` | the veto held it; §13's acceptance now binds here |
| `RING-1187` | L-203 | **64** | elevated | `hold` | |
| `ACC-2201` | S-077 | **58** | elevated | `hold` | |
| `TXN-48251` | T-021 | **0** | low | `allow` | was 31; repriced by `0026`, pool dropped |

Population: 9,844 transactions → 9,923 decisions, 7 alerts, **0 negative scores**,
0 invariant failures in `v_alert_invariants` or `v_decision_routing`. On a 7-day
window: 5 cases against 1 in the preceding week, a 95% false-negative rate over 80
labelled-fraud decisions, and 4 preventive actions issued.

## W4.4 The KPI numbers worth knowing, and why two of them look bad

**False-negative rate 95% (76/80).** That is the generator working as designed:
the labelled cohort was deliberately sized so most clusters fall below R-114's
line. A cohort the rules caught entirely would make this tile read 0% and prove
nothing. It is exact on this dataset and meaningless beyond it, and the tile says
so on the wire.

**Prevention false positives: 0 of 4.** A result, not an absence. Every preventive
action here lands on a fraud-labelled subject, so no challenge passes and nothing
is dispositioned `confirmed_legit`. The join §8 exists for works; it has nothing
to find. The denominator is published for exactly that reason.

**Fail-open 96.75%.** This is `decisions.fail_mode`, which holds the inline lane's
POLICY and has never recorded an observed failure, because nothing real has run.
The tile carries that sentence in its caveat. It is the one number in the set that
would be actively misleading without it.

## W4.5 Decisions taken that the plan did not specify

| # | Decision | Why |
|---|---|---|
| 1 | The consolidation guard tests the pool's SUM, not the presence of an aggravator | The reprice produced a −7. Week 2's guard was the right argument applied to one case of it; see §W4.2. |
| 2 | Band cutoffs by **maximum gap**, not percentile | The transaction distribution is bimodal with a 56-point empty region. p95 is 12 and p99 is 68 — a percentile rule would band every single-condition firing `elevated` and promote the veto fixture to `high`. A cutoff belongs in the empty region, where every available value gives the identical partition. |
| 3 | `account` and `network` left uncalibrated, and labelled so | One scoring subject each. A cutoff derived from n=1 is n=1 wearing a calibration's clothes, and `engine/bands.py` reads this table on every decision. |
| 4 | The condition report's recommendation is gated on a **materiality threshold** against the catalog's MEDIAN cost | Anchoring on the cheapest condition anchors on a fixture: `device_first_seen_min` earns 97% over 36 firings, almost all planted. The report now says "no aggravator is materially mispriced" when that is true, which a report that can only report one finding could not. |
| 5 | Five KPI views, not seven; the windowing lives in `contract/kpis.py` | A view takes no parameters, so the aggregation cannot live in SQL if every tile must name its window and its predecessor. The views carry the CLASSIFICATION — what counts as a case, as ground truth, as preventive — and one module carries the arithmetic. |
| 6 | `KpiPart` carries its own numerator and denominator | §11's "block / challenge / fail-open rate" is one row covering three rates whose denominators differ. Sharing the tile's denominator across them would be the exact error the denominator rule exists to prevent. |
| 7 | Constraint 3 is enforced as a **raising validator**, not a template convention | "An explanation that lists only aggravators is wrong even when every line is true" is a claim about the payload, so the payload refuses to be built. Same mechanism as alert.v1's `sum(signals) == score`. |
| 8 | Stored prose is cited **whole**, and the numeric sweep checks containment | `clear_text` contains "90 days"; that 90 belongs to whoever wrote the rule. Exempting prose from the sweep instead would have left the obvious loophole — an unsourced number hiding inside a sentence. |
| 9 | The `what should I do first` chip names the veto | `recommended_action_text` belongs to the rule that carried the SEVERITY. On TXN-48300 that is R-114, which wanted to challenge. Quoting it alone would advise an analyst to do the thing the system deliberately declined to do, in the system's own voice. |
| 10 | §13's acceptance moved from `TXN-48251` to `TXN-48300` | §13 asks for a report naming T-021's three mitigators and the veto. After the reprice TXN-48251 has an empty pool and no alert, so it cannot carry that test. TXN-48300 has all three mitigators, the veto signal, and `vetoed_by = T-021`. |
| 11 | Refund abuse uses `count` + `sum`, not a ratio | The pattern wants refunds over purchases. §3.1's `ratio` is deliberately unimplemented, and adding it to make this land would have been the data-engineering ticket §14 says such a pattern costs — for the very feature offered as evidence that it does not. |

## W4.6 What is deliberately not done

- **The `sequence` source kind.** `new_payee_then_drain` is still the one
  hand-seeded feature value. One raise site, `features/compiler.compile_spec`.
- **Batch/incremental consistency test** (§17). `run_population(as_of=…)` already
  gives the batch behaviour, so there is no second implementation to build.
- **`rule_versions` / `feature_catalog_versions` are still empty.** The case
  report now *says* so rather than printing a version number that resolves to
  nothing, which turns a silent audit gap into a stated one — but it is still a
  gap. A `publish` step that snapshots current definitions closes it.
- **The scheduler.** §15's topology is "one service, one database, a scheduler".
  `run_cycle.py` is still run by hand, and §18's decision 6 (the async cycle
  period) is still open.
- **The console, and admin write endpoints.** Out of scope by decision. Five
  contracts now exist for it to bind to, all generated from Pydantic models and
  byte-checked, and `alert.v1`'s digest has not moved through any of it.
- **A model behind the copilot.** Settled the other way, on §13's own argument.
  If one is introduced it sits behind the same five constraints with the
  arithmetic still computed outside it.
- **T-021's `clear_text` reads oddly on TXN-48300, and that is left alone.** It
  says "Already below the line", which was written for TXN-48251 and is false of
  a case scoring 68. The copilot quotes it verbatim, which is correct behaviour —
  the sentence belongs to whoever authored the rule, and a surface that silently
  improved it would be restating rather than quoting. Fixing it is a seed file
  editing rule prose, and it is a content change to a signed-off fixture, so it
  is named here rather than made quietly.

## W4.7 Three things to know before changing this

1. **`consolidate` drops a pool that does not net positive, and that is not a
   clamp.** Clamping the score to zero while still publishing the signals breaks
   `sum(signals) == score`, which is the product. If you need a case to show a
   score with mitigators outweighing aggravators, the answer is repricing, not
   loosening the guard.
2. **The explanation surface may read six relations and no more.**
   `explain/evidence.ALLOWED_RELATIONS` is the list, and `test_explain.py` proves
   it with a cursor hook that fails on `transactions`, `feature_values`,
   `case_outcomes` or any `v_kpi_*`. If a chip needs a fact from outside that set,
   the fact belongs on the alert, not in a second query.
3. **Every number that reaches an explanation must pass through `Quoter.q`.**
   There is deliberately no other way to turn a value into text in that package.
   An f-string that interpolates a Decimal directly will pass review and fail
   `test_every_number_traces_to_a_citation`.

## W4.8 Ledger against `architecture.md`

| # | Item | Status |
|---|---|---|
| §1 | The invariant | **DONE** — three layers, and now a fourth: `CopilotAnswer` refuses an explanation that drops a mitigator |
| §3 | Feature specs, runners, resolution, clusters | **MOSTLY** — `sequence` and `expression` still unimplemented; consistency test unwritten |
| §4 | Point-in-time selection | **DONE** |
| §5 | Absent and degraded features | **DONE** |
| §6 | Consolidation | **DONE** — guard generalised in Week 4, see §W4.2 |
| §7 | Action precedence | **DONE** |
| §8 | Action execution and outcome capture | **DONE** (Week 3) |
| §9 | Alert hygiene | **DONE** (Week 3) |
| §10 | Population scoring **and calibration** | **DONE** (Week 4) — condition repriced in `0026`, bands calibrated in `0027` |
| §11 | KPIs and the analytics contract | **DONE** (Week 4) — nine tiles, `kpis.v1`, `GET /kpis` |
| §12 | The read contract | **DONE** — frozen, digest-pinned, now with **four** siblings |
| §13 | Explanation surfaces | **DONE** (Week 4) — deterministic, `explanation.v1` |
| §14 | Extension recipe as an executed test | **DONE** (Week 4) — both patterns, DDL-hooked |

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

*(Week 4 note: `TXN-48251` now scores **0**. Seed `0026` repriced
`country_is_new_for_customer` from +50 to +12 on this week's own report, and the
pool no longer nets positive. See §W4.2. The table above is left as Week 3 wrote
it.)*

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

*(**Done in Week 4**, as seed `0026`. The prediction below — that dropping it
under 19 makes T-021's pool net-negative and the case score 0 — was right about
the number and wrong about the mechanism: the mitigator-only rule did not apply,
because the pool still had an aggravator in it, and the engine published −7 until
`consolidate.py` was generalised. §W4.2 has the account. Kept unedited because
being wrong about a mechanism in a way the next week caught is exactly what this
document is for.)*

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

*(Week 4 note: now **0** — see §W4.2. Left as Week 2 wrote it.)*

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
