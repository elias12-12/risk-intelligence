# GlassBox — end to end

**Who this is for.** Part 1 explains what the system does, in plain English, with
every piece of jargon defined the first time it appears. There is no code in it.
A stakeholder can stop at the end of Part 1 and have an accurate picture.

Part 2 walks the *same* journey again and names the exact file and function at
each step. Part 3 is the change cookbook — "I want to do X, what do I touch".
Part 4 is how to run and debug it. Part 5 is the honest account of which numbers
are real and which are simulated, which anyone quoting a figure from this system
needs to read.

If you read one thing, read [§1.3](#13-one-transactions-journey) and
[§2.2](#22-the-pipeline-stage-by-stage). They are the same story twice.

---

# PART 1 — What this system does

## 1.1 The problem, in one page

A bank moves money. Some of that movement is fraud. A fraud system has to decide,
for every transaction, whether to let it through, ask the customer to prove
themselves, or stop it — and it has to decide in the time it takes a card
terminal to beep.

Getting that wrong is expensive in **two opposite directions**, and this is the
tension the whole design is organised around:

- Let fraud through, and the bank loses money.
- Stop a legitimate customer, and you have declined someone's card in a
  restaurant abroad. They may never trust the card again.

Most systems in this space are judged only on the first. This one is built around
a third requirement that matters more than either in a regulated business:

> **Every decision must come with a complete, human-readable explanation of how it
> was reached — and that explanation must reconstruct both the score and the
> action exactly.**

That is the entire meaning of the name. "GlassBox" is the opposite of "black
box": you can see through it. If the system holds a customer's transfer, an
analyst can see the four pieces of evidence that caused it, what each one was
worth, which rule authorised the hold, and what the system could *not* see at the
time. Six months later, when someone asks *"why was this customer declined on 14
January?"*, the answer is retrievable and exact.

Two consequences follow, and they explain a lot of the design's shape:

**The score has to be a sum.** Not a model output, not a weighted blend — a
literal addition of visible parts. `34 + 21 + 18 + 14 = 87`. If a machine-learning
model is ever introduced, it enters as *one line in that addition* with its own
number, never as the score itself.

**Detection logic lives in database rows, not in program code.** Adding a new
fraud pattern is meant to cost `INSERT` statements, not a software release. The
Python in this repository is an *interpreter* of those rows; it does not know what
"card testing" or "mule ring" means.

## 1.2 The vocabulary

These words are used precisely throughout the code and the rest of this document.

| Term | What it means here |
|---|---|
| **Transaction** | One movement of money: a card purchase, a transfer, a refund. The raw material. |
| **Event** | Something that happened that is *not* money moving: a password reset, a new device seen, a step-up challenge failed. Fraud patterns often live in these. |
| **Entity** | A thing the bank knows about: a card, an account, a customer, a device, a merchant. |
| **Subject** | The thing being judged in one particular decision. Usually a transaction, but it can be an account, a customer, or a **network**. |
| **Network** | A *group* of entities that look connected — four accounts opened from the same device, say. The system discovers these itself; nobody writes them down. |
| **Feature** | One measured fact about an entity, expressed as a number or a yes/no. "How many card-not-present charges has this card had in the last 90 seconds" is a feature. Features are the vocabulary rules are written in. |
| **Feature catalog** | The list of every feature the system knows, *including how to compute each one*. It is a database table, not code. |
| **Condition** | A test on one feature: `card_cnp_count >= 5`. Each condition carries a point value. |
| **Rule** | A named bundle of conditions with a threshold and an intended action. `R-114` is "card-not-present burst". Rules are database rows. |
| **Signal** | One condition that actually fired, rendered as a sentence a human can read, carrying its point value. Signals are what an analyst sees. |
| **Aggravating / mitigating** | A signal worth **positive** points argues *for* the transaction being fraud. A signal worth **negative** points argues *against* it — evidence of innocence. Both are first-class. |
| **Score** | The sum of the signals. Nothing more. |
| **Band** | A label put on the score: `low`, `elevated`, `high`. The cutoffs differ by subject type and are stored in a table. |
| **Action** | What the system does: `allow`, `monitor`, `alert`, `challenge`, `hold`, `block`, in increasing severity. |
| **Preventive action** | The three that actually touch a customer — `challenge`, `hold`, `block`. Held to a higher standard than merely raising an alert. |
| **Veto** | A rule whose job is to argue *for* the customer. When its evidence is established, it caps how severe the action may be, no matter how high the score. |
| **Alert / case** | A decision that reached a human's review queue. One case can gather many repeat evaluations of the same situation. |
| **Disposition** | The analyst's verdict on a case: confirmed fraud, false positive, inconclusive. This is how the system learns whether it was right. |
| **Lane** | Which of two speeds a decision runs at. **Inline** happens while the card terminal waits (milliseconds, can prevent). **Async** happens afterwards (seconds to minutes, can investigate patterns that need more than one transaction to be visible). |
| **Point-in-time** | The discipline of only using facts that were *knowable at the moment of the decision*. Violating this makes a system look far better in testing than it is in production. |
| **Degraded** | A decision made while some piece of evidence was missing or too old. Recorded explicitly, never silently ignored. |

## 1.3 One transaction's journey

Follow a real card charge through the system. This is `TXN-48291`, one of the
five worked examples that ship with the repository.

**Step 1 — Something happens.** A $468 card-not-present purchase is attempted at a
gift-card merchant. The raw row lands in the database — amount, merchant, card,
device, IP, location. Nothing is judged yet, and nothing is derived: the system
stores what happened, not what it thinks about it.

**Step 2 — The system looks for groups.** Before judging anything, it rebuilds its
picture of which entities are connected — which accounts share a device, which
share a counterparty. Some fraud is only visible as a shape across several
accounts, and no single transaction shows it.

**Step 3 — It decides what to judge.** It builds a list: every transaction gets
judged in the fast lane; every account, customer and discovered network gets
judged in the slow lane. Each entry on that list records *what triggered it*, so
the cadence is reconstructable later.

**Step 4 — It computes the facts.** For every feature the relevant rules mention,
it works out the value — how many charges in 90 seconds, how new is this device,
how far is this location from the last one. Every computed value is stamped with
two timestamps: **when the fact was true**, and **when we learned it**. Those are
different, and keeping both is what makes an audit possible after data is
corrected.

**Step 5 — It works out which entity each fact belongs to.** This is subtler than
it sounds. The subject is a *transaction*, but "how many charges has this card
had" is a fact about a **card**, and "how many accounts has this device opened" is
a fact about a **device**. The system walks a stored map of relationships to get
from the transaction to the right entity. If it cannot get there, that is recorded
as a failure — never quietly treated as zero.

**Step 6 — It reads the facts as they were.** It deliberately refuses to see
anything that had not happened yet at the moment of the decision. A value that is
older than the feature's stated shelf life is treated as missing rather than used.

**Step 7 — Each condition fires or does not.** Four of `R-114`'s conditions hold.
Where evidence was missing, the condition contributes nothing *and the fact of the
gap is recorded*.

There is an asymmetry here worth understanding, because it is the design's
ethical core. If an **aggravating** fact is missing, the system under-accuses —
fine. If a **mitigating** fact is missing, the score goes *up*, because a
deduction disappeared. That is correct arithmetic and a dangerous basis for
action: the system now looks more suspicious of a customer precisely because it
lost the evidence that would have cleared them. So a rule missing mitigating
evidence **may still raise an alert, but loses the right to challenge, hold or
block**. It may look; it may not act.

**Step 8 — Each rule scores itself.** `R-114` scores 87 from its four conditions.

**Step 9 — The scores are combined into one.** A transaction can trip several
rules. It gets **one** score and **one** decision, not one per rule. Combining
them is not obvious: simply adding everything double-counts evidence two rules
both cite, and taking the highest throws away the fact that three independent
rules fired. The system pools the individual signals, keeps the strongest claim
per piece of evidence *in each direction*, and adds those up — so a piece of
evidence is counted once, and a mitigating claim is never swallowed by an
aggravating one about the same fact.

One rule holds absolutely: **if the deductions consume the accusation, the system
publishes nothing at all** — score zero, empty explanation. A negative risk score
would mean "safer than nothing", which the model cannot support.

**Step 10 — The action is decided, and it is a different question.** The score
answers *how risky*. The action answers *what to do*, and is computed separately:

1. **Vetoes go first.** If a rule arguing for the customer has its evidence
   established, severity is capped — and a line explaining that appears in the
   explanation, so a high score sitting next to a soft action is never unexplained.
2. **Only rules that crossed their own threshold get a say.** A rule that
   contributed evidence but did not trigger has no authority over the action.
3. **The most severe action among those rules wins**, and the system records
   *which rule* chose it.
4. **Preventive actions need a higher bar than alerts do**, because a wrong alert
   costs an analyst ten minutes and a wrong block costs a customer.

**Step 11 — Everything is written down.** One decision row. Every condition it
looked at, fired or not. If a rule had authority, an alert.

**Step 12 — Repeats are folded, not duplicated.** A network re-examined every
fifteen minutes would otherwise raise the same alert ninety-six times a day. A
repeat within the case's open window attaches to the existing case instead. If the
repeat scores *worse*, the case is updated to point at the worse evaluation.

**Step 13 — The action is issued.** A step-up challenge goes out over a named
channel; an analyst notification is routed by severity. Crucially, preventive
actions are issued when a case is **raised**, not when it folds — the customer
gets one step-up, not ninety-six.

**Step 14 — Outcomes come back.** Did the customer pass the challenge or abandon
it? Did the analyst call it fraud? Both are recorded, and both become inputs the
next generation of rules can read.

**Step 15 — A human asks why.** The analyst opens the case. They see the score
bar, every signal with its points, what was missing, which rule chose the action,
and — for a vetoed case — why the system held back. They can ask three questions
("why was this flagged", "what would clear it", "what should I do first") and get
answers assembled *only* from this case's stored rows. They can generate a filing
draft that cites every number back to the row it came from.

## 1.4 What the system refuses to do

Four refusals, each enforced by machinery rather than by convention. They are the
most load-bearing part of the design.

**It will not serve an explanation that does not add up.** If the signals do not
sum to the score, the API returns an error rather than a payload. This is checked
in three independent places: the server, a database view, and the test suite.

**It will not act without naming who decided.** Any action other than "allow"
must name the rule that authorised it, or the payload is refused.

**It will not explain a score without its mitigating evidence.** An explanation
listing only the reasons to be suspicious is wrong *even if every line in it is
true*. The explanation object raises rather than be built that way.

**It will not retune itself.** Analysis tools recommend price and threshold
changes; a human applies them as a reviewed change with the evidence written
beside it. A weight that silently adjusts itself means nobody can explain why last
week's identical transaction scored differently.

---

# PART 2 — The same journey, in the code

## 2.1 Shape of the repository

```
db/          the system's actual logic — schema, catalog, rules, thresholds
src/glassbox/  the interpreter that reads db/ and does what it says
scripts/     things you run
tests/       215 of them; several are the enforcement mechanism, not just checks
contract/    published JSON schemas — the frozen promise to any UI
fixtures/    generated sample data and the pinned expected results
```

**The single most important thing to internalise:** business logic is in `db/`,
mechanism is in `src/`. If you find yourself editing Python to change a threshold,
a window, a weight or a fraud pattern, something has gone in the wrong place.

`db/` is an **append-only ledger** applied in filename order by
[migrate.py](scripts/migrate.py). Migrations (`0001`–`0023`) create structure;
seeds (`0009`–`0028`) put logic in it. You never edit an applied file — you add
the next number. That is why the repricing of a condition is
[0026_reprice_country_novelty.sql](db/seeds/0026_reprice_country_novelty.sql) and
not an edit to `0010`: the reason for a price has to survive next to the price.

## 2.2 The pipeline, stage by stage

The order is owned by one module —
[engine/evaluation.py](src/glassbox/engine/evaluation.py) — and its docstring is
the authoritative statement of it. Stages map to Part 1's steps as noted.

| # | Stage | Where | What it does, and its one rule |
|---|---|---|---|
| 0 | **Build the graph** (step 2) | [graph/builder.py:51](src/glassbox/graph/builder.py#L51) `build()` | Derives `clusters` and `cluster_members` from the link layer. Networks are discovered, never hardcoded — a test greps `db/` for literal cluster ids and fails if one appears. |
| 0.5 | **Plan** (step 3) | [evaluation.py:83](src/glassbox/engine/evaluation.py#L83) `plan_evaluations()` | One `EvaluationRequest` per (subject, lane), each carrying its trigger. The per-subject-type SQL is [`_SUBJECT_SQL`](src/glassbox/engine/evaluation.py#L123) — this is the map from "subject type" to "how do I find one". |
| 1 | **Compute features** (step 4) | [features/runner.py](src/glassbox/features/runner.py) | Runs **out of band**, not inside a decision. Reads a spec from `feature_catalog`, compiles it to SQL via [compiler.py](src/glassbox/features/compiler.py), writes append-only rows stamped `as_of` (when true) and `computed_at` (when learned). |
| 2 | **Resolve** (step 5) | [engine/resolver.py:111](src/glassbox/engine/resolver.py#L111) `plan_route()`, [:192](src/glassbox/engine/resolver.py#L192) `resolve_many()` | Walks `resolution_edges` from the subject to the entity a feature keys on. Failure produces a recorded degradation, never a silent zero. |
| 3 | **Read, point-in-time** (step 6) | [engine/pit.py:61](src/glassbox/engine/pit.py#L61) `read_many()`, [:178](src/glassbox/engine/pit.py#L178) `bound_for()` | Newest value at or before the bound. **Two** ceilings: `bound_at` (what had happened) and `replay_as_of` (what we had learned). Beyond `max_staleness`, a value counts as absent. |
| 4 | **Evaluate conditions** (step 7) | [engine/conditions.py:93](src/glassbox/engine/conditions.py#L93) `evaluate_rule()` | Fire or degrade. The §5 asymmetry lives here: an absent **mitigator** sets `preventive_authority = False`. The fire verdict is computed **exactly once**, at [conditions.py:51](src/glassbox/engine/conditions.py#L51) `fires()`; a test forbids `persist.py` from recomputing it. |
| 5 | **Score per rule** (step 8) | [engine/scoring.py:18](src/glassbox/engine/scoring.py#L18) `score_rule()` | Computed **before** deduplication, deliberately — otherwise a rule's authority over the action would depend on dedup ordering. |
| 6 | **Consolidate** (step 9) | [engine/consolidate.py:21](src/glassbox/engine/consolidate.py#L21) `consolidate()` | One signal per `(feature_key, direction)`, keeping the largest magnitude; `asserted_by_rules` remembers every claimant. **A pool that does not net positive is dropped whole.** |
| 7 | **Band** | [engine/bands.py:13](src/glassbox/engine/bands.py#L13) `band_for()` | Reads the `score_bands` table. Recalibration is an `UPDATE`, not a release. |
| 8 | **Precedence** (step 10) | [engine/precedence.py:33](src/glassbox/engine/precedence.py#L33) `decide()` | Veto → authority → severity → prevention → cap, in that fixed order. |
| 9 | **Persist** (step 11) | [engine/persist.py:75](src/glassbox/engine/persist.py#L75) `write_batch()` | One decision, always. The condition ledger, always. An alert only if a rule had authority. |
| 10 | **Route** (step 12) | [persist.py:130](src/glassbox/engine/persist.py#L130) `_route_alert()` | `raised` / `folded` / `restated` / `suppressed` / `no_authority`. Every decision says what became of it — this is the denominator "alert volume" needs. |
| 11 | **Issue** (step 13) | [engine/execute.py:78](src/glassbox/engine/execute.py#L78) `write_executions()` | Preventive actions on a **raised** case only; notifications also on a restatement. |
| 12 | **Settle** (step 14) | [engine/outcomes.py:98](src/glassbox/engine/outcomes.py#L98) `settle()` | Challenge outcomes onto executions, dispositions onto cases. **Simulated here** — see Part 5. |

Stages 2 and 3 run per *batch* of 400, not per subject, which is why a full pass
over ten thousand transactions is a few dozen queries rather than tens of
thousands.

## 2.3 The read side — what a UI would bind to

Five **published contracts** live in [contract/](contract/). Each is a JSON
schema generated from Python models by
[export_contract_schema.py](scripts/export_contract_schema.py) and **committed**.
A test regenerates each in memory and asserts byte-equality; `alert.v1`
additionally has its SHA-256 pinned, because byte-equality alone still passes if
you change a model *and* re-run the exporter.

| Contract | Model | Serves |
|---|---|---|
| `alert.v1` | [contract/models.py](src/glassbox/contract/models.py) | **Frozen.** One alert with its signals, action and evidence. The two invariants are enforced as a raising validator. |
| `queue.v1` | [contract/queue.py:121](src/glassbox/contract/queue.py#L121) | The review queue, ordered by `score × exposure × recency` — with all three factors published so an analyst can see why a 72 outranks an 88. |
| `executions.v1` | [contract/executions.py:61](src/glassbox/contract/executions.py#L61) | What was done to the customer and how it resolved. Carries the `synthetic` flag. |
| `kpis.v1` | [contract/kpis.py:162](src/glassbox/contract/kpis.py#L162) `read_kpis()` | Nine analytics tiles. **The only place a reporting window is defined.** |
| `explanation.v1` | [contract/explanation.py](src/glassbox/contract/explanation.py) | The copilot answers and the case report. |

**"Sibling, not successor"** is the rule that keeps this workable. Four contracts
have been added since `alert.v1` was frozen and not one byte of it moved. If a UI
needs a field `alert.v1` lacks, it goes on a sibling contract — never as an edit,
and a genuine breaking change becomes `alert.v2` served alongside `v1`.

Eight HTTP endpoints, all `GET`, all read-only:

```
GET /health
GET /alerts                     alert.v1     routes_alerts.py
GET /alerts/{id}                alert.v1     routes_alerts.py
GET /queue                      queue.v1     routes_queue.py
GET /alerts/{id}/executions     executions.v1
GET /kpis                       kpis.v1      routes_kpis.py
GET /alerts/{id}/copilot        explanation.v1  routes_explain.py
GET /alerts/{id}/report         explanation.v1  routes_explain.py
```

There are **no write endpoints**. Rule authoring and disposition submission belong
with the console, which is out of scope.

## 2.4 The explanation surface (step 15)

[src/glassbox/explain/](src/glassbox/explain/) is small and has two unusual
properties worth understanding before touching it.

**It can only read six tables.**
[`ALLOWED_RELATIONS`](src/glassbox/explain/evidence.py) is `alerts`, `decisions`,
`alert_signals`, `alert_subjects`, `action_executions`, `rule_definitions` — all
for the alert in view. A test installs a database-cursor hook, records every
statement the copilot executes, and fails the build if any other table appears. No
cross-case inference, no re-deriving a feature.

**Every number must pass through the `Quoter`.** There is deliberately no other
way to turn a value into text in that package. `q()` returns the string *and*
records where it came from — table and primary key, or the formula if it was
derived. A test then extracts every numeric token from the output and checks it
traces back. An f-string that interpolates a number directly will pass code review
and fail the suite.

- [copilot.py:44](src/glassbox/explain/copilot.py#L44) `answer_chips()` — the
  three questions.
- [case_report.py:36](src/glassbox/explain/case_report.py#L36) `build_report()` —
  the filing draft, whose draft notice must appear *inside* the Markdown or the
  object refuses to be built.

There is **no language model anywhere in this path**, and `model_backed: false`
is published so a client can tell. That is a design decision, not a shortcoming:
the explanation surface of a glass-box system should not itself be a black box.

## 2.5 The analysis tools

Three scripts read the population and **recommend**. None of them writes. Tests
scan their source for SQL write keywords and fail if one appears.

| Script | Question it answers |
|---|---|
| [condition_report.py](scripts/condition_report.py) | Which conditions are mispriced — what the catalog charges per unit of measured precision. This is what found the `+50` misprice. |
| [calibrate_bands.py](scripts/calibrate_bands.py) | Where the `low`/`elevated`/`high` cutoffs should sit, per subject type. |
| [kpi_report.py](scripts/kpi_report.py) | The nine tiles, in a terminal, with every denominator and caveat. |

Their output becomes a **hand-written seed file** with the evidence in the
comment. `0026` and `0027` are exactly that, and reading them is the fastest way
to understand how a change is supposed to be justified in this repository.

---

# PART 3 — The change cookbook

## 3.1 What costs rows and what costs code

| Change | Cost | Where |
|---|---|---|
| New rule over existing features | **Two INSERTs.** No release. | `rule_definitions` + `rule_conditions` |
| New feature using an existing reducer | **One INSERT plus a runner pass.** | `feature_catalog` row with its computation spec |
| Change a threshold, price, band or dedup window | **An UPDATE**, as a new seed file. | `rule_conditions`, `score_bands`, `alert_policy` |
| New feature needing a **new reducer** | A data-engineering ticket. | A Python function in [aggregations.py](src/glassbox/features/aggregations.py) |
| New subject type beyond the seven | A code change. | `_SUBJECT_SQL` in [evaluation.py:123](src/glassbox/engine/evaluation.py#L123) |
| Reading from a new table | A code change. | `ALLOWED_RELATIONS` in [predicate.py](src/glassbox/features/predicate.py) — a deliberate allow-list |

The first claim is *tested*, not asserted:
[test_extension_cardtesting.py](tests/test_extension_cardtesting.py) and
[test_extension_refundabuse.py](tests/test_extension_refundabuse.py) each build a
complete working detector inside the test body while a hook records every
statement and fails on any schema change.

## 3.2 Worked example — adding a fraud pattern

Copy [test_extension_refundabuse.py](tests/test_extension_refundabuse.py). It is
the most complete example, because it uses a subject type none of the demo cases
use and needs two conditions combined with AND.

1. **Add the feature(s)** to `feature_catalog` as a new seed file. Copy the shape
   from [0028](db/seeds/0028_seed_refund_abuse_features.sql). You must specify how
   it is computed (`source_kind`, `source_relation`, `aggregation`,
   `filter_predicate`, `window_spec`), which entity it keys on (`subject_key`),
   how a rule's subject reaches that entity (`resolution_path`), and what happens
   when it is missing (`default_when_absent`).
2. **Run the feature layer**: `python scripts/run_features.py --feature <key>`.
   Check the values discriminate before writing any rule.
3. **Add the rule** — one `rule_definitions` row, N `rule_conditions` rows.
4. **Run a lane** and inspect the decision.
5. **Write the test**, with the `no_ddl` fixture from
   [conftest.py](tests/conftest.py) so the "INSERTs only" claim stays checked.

**Three traps, each of which has already cost someone a debugging session:**

- **Every mitigating feature must have `default_when_absent = NULL`.** A mitigator
  that defaults to `false` makes the whole missing-evidence policy unreachable.
  Enforced across the catalog by `test_degraded.py`.
- **Never narrow a feature's driver to its own filter.** The runner recomputes a
  feature where its source rows arrive; filter that down to only matching rows and
  a windowed feature goes stale everywhere else. Declare `driver_filter`
  explicitly only when you genuinely need it.
- **Never `ON CONFLICT … DO UPDATE` on `feature_values`.** It destroys the value a
  past decision was made on. A test greps for it.

## 3.3 Where things must not go

- **A threshold, weight or window in Python.** It belongs in a table.
- **A fixture identifier under `db/`.** Nothing in the schema or seeds may name
  `RING-1187` or `TXN-48291`. A test enforces it, and it is what keeps the model
  general rather than fitted to the demo.
- **A field added to `alert.v1`.** Add a sibling contract.
- **A second implementation of a computed verdict.** The fire verdict, the
  priority formula and the cost-per-precision anchor each exist in exactly one
  place, with a test pinning that.
- **An automatic calibration write.** Analysis recommends; a human applies.

---

# PART 4 — Running, testing, debugging

## 4.1 From nothing to running

```powershell
.\scripts\bootstrap.ps1        # ~3 minutes; run with the virtualenv on PATH
```

Starts PostgreSQL in Docker, installs dependencies, generates sample data,
applies every migration and seed, computes the feature layer, runs both lanes,
settles the actions, prints the three analysis reports, exports the contracts, and
runs the test suite.

## 4.2 The pieces, individually

```bash
python scripts/generate_synthetic.py                # sample data
python scripts/reset_db.py                          # rebuild: migrate, seed, load, features
python scripts/run_cycle.py --lane inline_sync      # the fast lane
python scripts/run_cycle.py --lane async            # the slow lane
python scripts/resolve_actions.py                   # settle outcomes
python scripts/condition_report.py                  # mispricing analysis
python scripts/calibrate_bands.py                   # band-cutoff analysis
python scripts/kpi_report.py --verbose              # the nine tiles
python scripts/case_report.py --alert 5 --citations # explanation, fully sourced
python -m glassbox serve                            # the read API on :8000
```

`python -m glassbox <command>` is a thin front door onto the same scripts.

## 4.3 Proving it works

```bash
psql "$GLASSBOX_DSN" -f db/acceptance/verify_scores.sql
```

Human-readable proof, run from the repository root: the engine's computed scores
against the signed-off expectations, the rationale behind each alert, and a zero
count on both invariants. Then:

```bash
pytest                        # 215 tests, ~90 seconds including a full rebuild
```

The suite drops and rebuilds a **separate** database at session start, runs the
whole pipeline once, then runs each test inside a transaction that is rolled back.
Tests are order-independent and a test that mutates rules cannot leak.

Several tests are **enforcement, not verification** — they are the only thing
standing between the design and a plausible-looking violation:

| Test | What it makes impossible |
|---|---|
| `test_contract.py` | Changing `alert.v1` without it being a deliberate, visible act |
| `test_explain.py` | An explanation reading another case, or printing an unsourced number |
| `test_extension_*.py` | The "INSERTs, not code" claim quietly becoming false |
| `test_predicate_safety.py` | Admin-authored text reaching SQL |
| `test_migrations.py` | An UPSERT destroying decision history |
| `test_clusters.py` | A fixture id appearing under `db/` |

## 4.4 Debugging by symptom

| Symptom | Look at |
|---|---|
| A score is not what you expect | `decision_conditions` — every condition the decision looked at, fired or not, with the value it saw |
| A feature reads as missing | `read_status` in that table: `absent`, `stale`, `unresolvable` or `fanout_error` each mean something different |
| A rule fires on the wrong entity | `resolution_path` on the catalog row, and `resolution_edges` |
| An alert did not appear | `decisions.alert_routing` — every decision says why not |
| The API returns 500 | An invariant was violated. `SELECT * FROM v_alert_invariants WHERE NOT sum_ok` |
| Something looks structurally wrong | `SELECT * FROM v_decision_routing` — returns **zero rows** when healthy |

---

# PART 5 — What is real and what is simulated

Anyone quoting a number out of this system needs this section. It is not
boilerplate modesty; some of these figures are exact and meaningless at the same
time, and both halves matter.

## 5.1 Real, and would work unchanged on production data

The schema; the feature computation layer; the entity resolution graph;
point-in-time reading; condition evaluation and the degraded-evidence policy;
scoring, consolidation and precedence; alert deduplication, suppression and
priority; action issuance; all five contracts; the API; and the entire explanation
surface. **None of these ever look at a synthetic label.** Point them at real
transaction rows and they run.

## 5.2 Simulated, and clearly marked as such

Only **three** places in the non-test code read the planted fraud label:

| Site | Powers | On real data it becomes |
|---|---|---|
| [v_kpi_decisions.sql](db/views/v_kpi_decisions.sql) | the false-negative tile | a confirmed-fraud join; recall stops being measurable without a sampled audit |
| [v_condition_performance.sql](db/views/v_condition_performance.sql) | condition precision | falls back to case-disposition precision, already in the view |
| [engine/outcomes.py](src/glassbox/engine/outcomes.py) | settling challenges | a real step-up integration |

Three published figures are structurally simulated, and each carries a flag and a
caveat on the wire so no surface can present them as measured:

- **False-negative rate (95%).** Exact against planted fraud, meaningless outside
  this dataset. The high number is the sample data working as designed — the
  fraud cohort was deliberately sized so most of it falls *below* the detection
  line. A dataset the rules caught entirely would make this tile read 0% and prove
  nothing.
- **Challenge pass rate.** Nothing external answers a step-up here; a script
  settles them. Every such row is stamped `synthetic = TRUE`.
- **Fail-open rate (96.75%).** This records the fast lane's *policy*, not any
  observed failure. Nothing has ever failed, because nothing real has run.

## 5.3 Numbers derived from this dataset

The **method** transfers; the **numbers** do not. The `+12` condition price, the
`75 / 40` band cutoffs and every rule threshold were derived from this population
by tools that recompute against whatever data they are pointed at.

## 5.4 What this proves, and what it does not

**Proves:** scores are additive and fully decomposable, verified in SQL; four
structurally different fraud shapes are representable in the same model; mitigating
evidence can hold a transaction below the line and cap the action; new patterns
arrive by `INSERT`; and every analytics figure is computed from stored rows rather
than asserted.

**Does not prove:** throughput or real latency (the millisecond budget is a design
target, not a measurement); detection quality (synthetic fraud is fraud we already
knew how to describe); resilience (nothing has failed because nothing real has
run); or that analysts trust it.

Stating the second list is not modesty. A prototype that overstates what it proves
is worse than no prototype, because it moves decisions forward on evidence that
does not exist.

---

## Where to go next

- [README.md](README.md) — the shorter tour, and the current known gaps.
- [HANDOFF.md](HANDOFF.md) — week-by-week account of what was built and every
  judgment call, newest first. Read §W4.5 for decisions the plan did not specify.
- [architecture.md](architecture.md) — the original design document, kept
  unedited as a planning artifact. Where it and the code disagree, `HANDOFF.md`
  records why.
