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
[§2.3](#23-the-pipeline-stage-by-stage). They are the same story twice.

**On the code citations.** Every line-anchored link in Part 2 names the symbol
defined at that line, and [test_walkthrough.py](tests/test_walkthrough.py) fails
the build when one drifts. It exists because eleven of the seventeen citations in
the previous revision had quietly gone stale — still rendering, still confident,
pointing a reader at the wrong function.

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
| **Authorization** | The inline decision made at the moment a charge arrives, *before the charge is recorded as approved*. This is the difference between a system that detects and one that prevents. |
| **Arrival** | How data gets in. There are exactly two doors: authorization (decide, then write) and ingest (settled rows that already happened). |
| **Shadow** | The state every newly written rule lands in. It is scored on every applicable subject and recorded in full, and it acts on nobody until a human promotes it. |
| **Simulation** | Answering "what would happen if" without anything surviving the answer. Three kinds: re-score a subject, try a candidate rule against real history, price a charge that never occurred. |
| **Point-in-time** | The discipline of only using facts that were *knowable at the moment of the decision*. Violating this makes a system look far better in testing than it is in production. |
| **Degraded** | A decision made while some piece of evidence was missing or too old. Recorded explicitly, never silently ignored. |

## 1.3 One transaction's journey

Follow a real card charge through the system. This is `TXN-48291`, one of the
five worked examples that ship with the repository.

**Step 0 — Which door it came through.** Everything starts with data arriving,
and there are two doors that mean different things.

- **The authorization door** is a charge asking permission. The system decides
  *before* the row is committed as approved, so a declined charge is never an
  approved transaction that was regretted afterwards.
- **The ingest door** is settled history — transactions that already happened,
  events, and links between entities. Some fraud shapes are only visible across
  several settled rows, so this door is what makes the slow lane possible.

The old shape of this system had only the second door, which meant it could
describe fraud accurately and never stop any.

**Step 1 — Something happens.** A $468 card-not-present purchase is attempted at a
gift-card merchant, carrying its amount, merchant, card, device, IP and location.
Through the ingest door the raw row simply lands and nothing is judged yet.
Through the authorization door the row is written as *presumed* approved and the
next several steps all happen before anyone is told the answer — and if any of
them fails, the whole thing is rolled back and the charge never existed.

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
3. **Rules still in shadow are scored and recorded, and get no say at all.**
   A rule written today cannot act today.
4. **The most severe action among those rules wins**, and the system records
   *which rule* chose it.
5. **Preventive actions need a higher bar than alerts do**, because a wrong alert
   costs an analyst ten minutes and a wrong block costs a customer.

**Step 11 — At the authorization door, the action becomes the answer.** A
`challenge`, `hold` or `block` is written back onto the charge as its
authorization result, so the row that lands in history says *declined* rather
than *approved, with a note*. Then the features are recomputed, because the
decision just changed one of the facts they measure.

**Step 12 — Everything is written down.** One decision row. Every condition it
looked at, fired or not. If a rule had authority, an alert.

**Step 13 — Repeats are folded, not duplicated.** A network re-examined on a
clock would otherwise raise the same alert every cycle, all day. A repeat within
the case's open window attaches to the existing case instead. If the repeat
scores *worse*, the case is updated to point at the worse evaluation.

**Step 14 — The action is issued.** A step-up challenge goes out over a named
channel; an analyst notification is routed by severity. Crucially, preventive
actions are issued when a case is **raised**, not when it folds — the customer
gets one step-up, not one per cycle.

**Step 15 — Outcomes come back.** Did the customer pass the challenge or abandon
it? Did the analyst call it fraud? Both are recorded, and both become inputs the
next generation of rules can read.

**Step 16 — A human asks why.** The analyst opens the case in the browser. They
see the score bar, every signal with its points, what was missing, which rule
chose the action, and — for a vetoed case — why the system held back. They can
ask three questions ("why was this flagged", "what would clear it", "what should
I do first") and get answers assembled *only* from this case's stored rows. They
can generate a filing draft that cites every number back to the row it came from.

**Step 17 — And a human answers back.** They record a verdict on the case. An
admin can write a new rule, watch what it *would have* done against real history
before saving it, publish it into shadow, and promote it once they believe it.
None of that is a software release; all of it is recorded with who did it.

**Step 18 — It runs on a clock.** The service ticks on an interval, picks up
whatever has arrived since the last tick, and re-evaluates what that arrival
touched. Nothing in the journey above needs a human to start it.

## 1.4 What the system refuses to do

Six refusals, each enforced by machinery rather than by convention. They are the
most load-bearing part of the design.

**It will not serve an explanation that does not add up.** If the signals do not
sum to the score, the API returns an error rather than a payload. This is checked
in three independent places: the server, a database view, and the test suite —
and a fourth, in the browser, which re-checks the arithmetic and says so on
screen rather than rendering a bar that lies.

**It will not act without naming who decided.** Any action other than "allow"
must name the rule that authorised it, or the payload is refused.

**It will not explain a score without its mitigating evidence.** An explanation
listing only the reasons to be suspicious is wrong *even if every line in it is
true*. The explanation object raises rather than be built that way.

**It will not let a new rule act on the day it is written.** Publishing lands a
rule in shadow, where it is scored on everything and authorises nothing. Leaving
shadow is a separate, recorded act by a named person.

**It will not let a simulation leave a trace.** Every what-if runs inside a
transaction that is rolled back, including the ones that fabricate a charge and
compute features for it. An autocommit connection is refused outright, because
on one the rollback would silently be a commit.

**It will not retune itself.** Analysis tools recommend price and threshold
changes; a human applies them as a reviewed change with the evidence written
beside it. A weight that silently adjusts itself means nobody can explain why last
week's identical transaction scored differently.

---

# PART 2 — The same journey, in the code

## 2.1 Shape of the repository

```
db/            the system's actual logic — schema, catalog, rules, thresholds
src/glassbox/  the interpreter that reads db/ and does what it says
scripts/       things you run
tests/         the Python suite; several are the enforcement mechanism, not just checks
contract/      nine published JSON schemas — the frozen promise to any client
console/       the browser surface, bound to those schemas and asserting nothing more
fixtures/      generated sample data and the pinned expected results
```

**The single most important thing to internalise:** business logic is in `db/`,
mechanism is in `src/`. If you find yourself editing Python to change a threshold,
a window, a weight or a fraud pattern, something has gone in the wrong place.

`db/` is an **append-only ledger** applied in filename order by
[migrate.py](scripts/migrate.py). Migrations (`0001`–`0032`) create structure;
seeds (`0009`–`0031`) put logic in it. You never edit an applied file — you add
the next number. That is why the repricing of a condition is
[0026_reprice_country_novelty.sql](db/seeds/0026_reprice_country_novelty.sql) and
not an edit to `0010`: the reason for a price has to survive next to the price.

## 2.2 The two doors

Nothing in Part 2's pipeline runs until something arrives, and what a caller is
allowed to claim differs sharply by door. Both are in
[src/glassbox/ingest/](src/glassbox/ingest/).

| Door | Entry point | What it means |
|---|---|---|
| **Authorize** | [`authorize()`](src/glassbox/ingest/authorize.py#L122) | A charge asking permission. Nine ordered steps inside one transaction: register the device, insert the charge as *presumed* approved, run a feature pass at the charge's own instant, evaluate the inline lane, choose an action, **write that action back as the authorization result**, persist, recompute the features the decision just changed, commit. All of it or none of it. |
| **Ingest** | [`ingest()`](src/glassbox/ingest/arrivals.py#L85) | Settled transactions, events and links. Every row is validated against the reference vocabulary and the foreign keys *before* insertion by [`prepare()`](src/glassbox/ingest/records.py#L144), and a rejected row names its reason rather than raising a constraint error at the driver. |

What arrived is tracked by a **watermark** per stream —
[`advance()`](src/glassbox/ingest/watermark.py#L53) and
[`frontier()`](src/glassbox/ingest/watermark.py#L77) — so a cycle knows what is
new without rescanning history. [`run_cycle()`](src/glassbox/ingest/cycle.py#L76)
turns that into work via
[`affected_subjects()`](src/glassbox/ingest/cycle.py#L146), and
[`Scheduler`](src/glassbox/scheduler.py#L89) runs it on
[`interval_seconds()`](src/glassbox/scheduler.py#L53) — `GLASSBOX_CYCLE_SECONDS`,
default 30, `0` to disable. It is off unless `glassbox serve` asks for it,
because a background thread committing into the middle of the test suite's
rolled-back transactions would be the least debuggable failure this project
could have.

## 2.3 The pipeline, stage by stage

The order is owned by one module —
[evaluation.py](src/glassbox/engine/evaluation.py) — and its docstring is the
authoritative statement of it. Stages map to Part 1's steps as noted.

| # | Stage | Where | What it does, and its one rule |
|---|---|---|---|
| 0 | **Build the graph** (step 2) | `graph/builder.py` — [`build()`](src/glassbox/graph/builder.py#L71) | Derives `clusters` and `cluster_members` from the link layer. Networks are discovered, never hardcoded — a test greps `db/` for literal cluster ids and fails if one appears. |
| 0.5 | **Plan** (step 3) | `engine/evaluation.py` — [`plan_evaluations()`](src/glassbox/engine/evaluation.py#L103) | One `EvaluationRequest` per (subject, lane), each carrying its trigger. The per-subject-type SQL is [`_SUBJECT_SQL`](src/glassbox/engine/evaluation.py#L143) — the map from "subject type" to "how do I find one". |
| 1 | **Compute features** (step 4) | `features/runner.py` — [`IncrementalRunner`](src/glassbox/features/runner.py#L53) | Runs **out of band**, not inside a decision. Reads a spec from `feature_catalog`, compiles it to SQL via [`compile_spec()`](src/glassbox/features/compiler.py#L146), writes append-only rows stamped `as_of` (when true) and `computed_at` (when learned). |
| 2 | **Resolve** (step 5) | `engine/resolver.py` — [`plan_route()`](src/glassbox/engine/resolver.py#L111), [`resolve_many()`](src/glassbox/engine/resolver.py#L192) | Walks `resolution_edges` from the subject to the entity a feature keys on. Failure produces a recorded degradation, never a silent zero. |
| 3 | **Read, point-in-time** (step 6) | `engine/pit.py` — [`read_many()`](src/glassbox/engine/pit.py#L61), [`bound_for()`](src/glassbox/engine/pit.py#L178) | Newest value at or before the bound. **Two** ceilings: `bound_at` (what had happened) and `replay_as_of` (what we had learned). Beyond `max_staleness`, a value counts as absent. |
| 4 | **Evaluate conditions** (step 7) | `engine/conditions.py` — [`evaluate_rule()`](src/glassbox/engine/conditions.py#L107) | Fire or degrade. The missing-mitigator asymmetry lives here: an absent mitigator sets `preventive_authority = False`. The fire verdict is computed **exactly once**, at [`fires()`](src/glassbox/engine/conditions.py#L65); a test forbids `persist.py` from recomputing it. |
| 5 | **Score per rule** (step 8) | `engine/scoring.py` — [`score_rule()`](src/glassbox/engine/scoring.py#L18) | Computed **before** deduplication, deliberately — otherwise a rule's authority over the action would depend on dedup ordering. |
| 6 | **Consolidate** (step 9) | `engine/consolidate.py` — [`consolidate()`](src/glassbox/engine/consolidate.py#L21) | One signal per `(feature_key, direction)`, keeping the largest magnitude; `asserted_by_rules` remembers every claimant. **A pool that does not net positive is dropped whole.** |
| 7 | **Band** | `engine/bands.py` — [`band_for()`](src/glassbox/engine/bands.py#L13) | Reads the `score_bands` table. Recalibration is an `UPDATE`, not a release. |
| 8 | **Precedence** (step 10) | `engine/precedence.py` — [`decide()`](src/glassbox/engine/precedence.py#L46) | Veto → authority → severity → prevention → cap, in that fixed order. A shadow rule reaches this stage and is denied authority in it. |
| 9 | **Persist** (step 12) | `engine/persist.py` — [`write_batch()`](src/glassbox/engine/persist.py#L76) | One decision, always. The condition ledger, always. An alert only if a rule had authority. |
| 10 | **Route** (step 13) | `engine/persist.py` — [`_route_alert()`](src/glassbox/engine/persist.py#L131) | `raised` / `folded` / `restated` / `suppressed` / `no_authority`. Every decision says what became of it — this is the denominator "alert volume" needs. |
| 11 | **Issue** (step 14) | `engine/execute.py` — [`write_executions()`](src/glassbox/engine/execute.py#L78) | Preventive actions on a **raised** case only; notifications also on a restatement. |
| 12 | **Settle** (step 15) | `engine/outcomes.py` — [`settle()`](src/glassbox/engine/outcomes.py#L101) | Challenge outcomes onto executions, dispositions onto cases. **Simulated here** — see Part 5. |

Stages 2 and 3 run per *batch* of 400, not per subject, which is why a full pass
over ten thousand transactions is a few dozen queries rather than tens of
thousands.

## 2.4 Simulation — the same pipeline, with nothing surviving

[engine/simulate.py](src/glassbox/engine/simulate.py) runs the stages above and
throws the result away.
[`simulation_scope()`](src/glassbox/engine/simulate.py#L89) is that discipline
made structural: every simulation goes through it, including the one that writes
nothing, because a rule that applies only where it is currently needed is a rule
nobody can rely on later.

| Call | Fabricates | Answers |
|---|---|---|
| [`simulate_subject()`](src/glassbox/engine/simulate.py#L100) | nothing | "what would this subject score right now?" |
| [`simulate_rule()`](src/glassbox/engine/simulate.py#L181) | a rule | "what would this candidate rule have done against real history?" — with a sampled population, a score distribution, worked examples and a diff against what was actually decided |
| [`simulate_transaction()`](src/glassbox/engine/simulate.py#L337) | a charge | "what would happen to a charge that never occurred?" — including [`scoped_feature_pass()`](src/glassbox/engine/simulate.py#L424) at the instant it claims to have happened at, which is what makes the answer new rather than borrowed |

An autocommit connection is refused outright, because `conn.transaction()` on one
is a no-op that silently commits — the single line standing between "simulate a
rule" and "publish a rule".

## 2.5 Authoring — validate, publish, shadow, promote

The control plane is writable, and three things stand between a typed rule and a
customer.

**Validation.** [`validate_draft()`](src/glassbox/rules/validate.py#L106) rejects
twenty-two shapes, and the reason it exists is that every one of them previously
produced a rule that did *nothing* rather than a rule that failed —
[`fires()`](src/glassbox/engine/conditions.py#L65) returns `False` for an
operator it does not recognise, so a typo'd `>==` yielded a rule that never
fired, never errored, and appeared in the ledger as a condition that simply never
matched. Indistinguishable from a rule that works and finds nothing.
[`ensure_valid()`](src/glassbox/rules/validate.py#L273) is the raising form the
routes call.

**Publishing.** [`publish_rule()`](src/glassbox/rules/publish.py#L141) applies the
definition *and* snapshots it into `rule_versions` with the actor, in one
transaction. Either both or neither: a saved rule with no snapshot is a rule whose
stored decisions point at a version that does not exist. Crucially,
[`apply_definition()`](src/glassbox/rules/publish.py#L68) is the same function
`simulate_rule` calls inside its rolled-back scope, so what an admin tested is
what an admin publishes, down to the statement — the only difference is the
commit.

**Shadow.** A published rule lands in `shadow`. It is scored on every applicable
subject, recorded in the ledger, and denied action authority at stage 8.
[`promote_rule()`](src/glassbox/rules/publish.py#L174) is the separate, recorded
act that lets it act.

## 2.6 The read and write surface

Nine **published contracts** live in [contract/](contract/). Each is a JSON
schema generated from Python models by
[export_contract_schema.py](scripts/export_contract_schema.py) and **committed**.
A test regenerates each in memory and asserts byte-equality; `alert.v1`
additionally has its SHA-256 pinned, because byte-equality alone still passes if
you change a model *and* re-run the exporter.

| Contract | Model | Serves |
|---|---|---|
| `alert.v1` | [`AlertDetail`](src/glassbox/contract/models.py#L115) | **Frozen since Week 2.** One alert with its signals, action and evidence. Both invariants enforced as a raising validator. |
| `queue.v1` | [`QueueEntry`](src/glassbox/contract/queue.py#L67) | The review queue, ordered by `score × exposure × recency` — all three factors published so an analyst can see why a 72 outranks an 88. |
| `executions.v1` | [`read_executions()`](src/glassbox/contract/executions.py#L61) | What was done to the customer and how it resolved. Carries the `synthetic` flag. |
| `kpis.v1` | [`read_kpis()`](src/glassbox/contract/kpis.py#L204) | Nine analytics tiles. **The only place a reporting window is defined.** |
| `explanation.v1` | [`answer_chips()`](src/glassbox/explain/copilot.py#L44) | The copilot answers and the case report. |
| `dispositions.v1` | [`CaseVerdict`](src/glassbox/contract/dispositions.py#L72) | An analyst's verdict, including the correction history, so a client never re-derives it. |
| `simulation.v1` | [`SimulatedDecision`](src/glassbox/contract/simulation.py#L86) | All three what-ifs, each publishing `persisted: false` on the wire so a caller never infers it from the URL. |
| `catalog.v1` | [`RuleDraft`](src/glassbox/contract/catalog.py#L605) | The control plane as data: rules, features, and the reference vocabulary from [`read_reference()`](src/glassbox/contract/catalog.py#L537) that populates an authoring form. |
| `ingest.v1` | [`AuthorizationOutcome`](src/glassbox/contract/ingest.py#L186) | The two doors and the cycle. |

**"Sibling, not successor"** is the rule that keeps this workable. Eight contracts
have been added since `alert.v1` was frozen and not one byte of it moved. If a
client needs a field `alert.v1` lacks, it goes on a sibling — never as an edit,
and a genuine breaking change becomes `alert.v2` served alongside `v1`.

### The endpoints

**Reads stay open.** Every `GET` was public before there were any writes and
still is: they publish decisions about synthetic subjects, and gating them would
buy friction and nothing else. The surfaces that leave a mark need a bearer
token, and [`ROLES`](src/glassbox/api/auth.py#L35) is *ordered* — admin can do
everything analyst can — enforced by
[`require_role()`](src/glassbox/api/auth.py#L103).

```
GET    /health                        —          open
GET    /alerts                        alert.v1   open
GET    /alerts/{id}                   alert.v1   open
GET    /queue                         queue.v1   open
GET    /alerts/{id}/executions        executions.v1   open
GET    /kpis                          kpis.v1    open
GET    /alerts/{id}/copilot           explanation.v1  open
GET    /alerts/{id}/report            explanation.v1  open
GET    /alerts/{id}/outcome           dispositions.v1 open
GET    /rules  /rules/{id}  /features  /reference     catalog.v1      open

POST   /alerts/{id}/outcome           dispositions.v1  analyst
GET    /cycle                         —                analyst
POST   /simulate/subject              simulation.v1    analyst

POST   /simulate/rule                 simulation.v1    admin
POST   /simulate/transaction          simulation.v1    admin
POST   /rules                         catalog.v1       admin   → lands in shadow
PUT    /rules/{id}                    catalog.v1       admin
POST   /rules/{id}/promote            catalog.v1       admin
DELETE /rules/{id}                    —                admin
POST   /features/{key}/publish        catalog.v1       admin
POST   /authorize                     ingest.v1        admin   → can decline a charge
POST   /ingest/{transactions,events,links}   ingest.v1  admin
POST   /cycle                         ingest.v1        admin
```

`POST /authorize` and `POST /simulate/transaction` are deliberately separate
endpoints rather than one with a flag, so that a typo cannot turn a hypothetical
into a charge.

**This is not authentication.** Bearer tokens compared against a hardcoded map,
over plain HTTP, with no expiry or rotation. It establishes *which of two demo
users is acting* so the audit columns have something true in them. Part 5 says so
again, because it would be malpractice in front of real money.

## 2.7 The explanation surface (step 16)

[src/glassbox/explain/](src/glassbox/explain/) is small and has two unusual
properties worth understanding before touching it.

**It can only read eight relations.**
[`ALLOWED_RELATIONS`](src/glassbox/explain/evidence.py#L50) is `alerts`,
`decisions`, `alert_signals`, `alert_subjects`, `action_executions`,
`rule_definitions`, `rule_versions` and `feature_catalog_versions` — all for the
alert in view. A test installs a database-cursor hook, records every statement
the copilot executes, and fails the build if any other relation appears. No
cross-case inference, no re-deriving a feature.

**Every number must pass through the `Quoter`.** There is deliberately no other
way to turn a value into text in that package. `q()` returns the string *and*
records where it came from — table and primary key, or the formula if it was
derived. A test then extracts every numeric token from the output and checks it
traces back. An f-string that interpolates a number directly will pass code review
and fail the suite.

- [`answer_chips()`](src/glassbox/explain/copilot.py#L44) — the three questions.
- [`build_report()`](src/glassbox/explain/case_report.py#L39) — the filing draft,
  whose draft notice must appear *inside* the Markdown or the object refuses to
  be built.

The report states its own audit gaps rather than papering over them: a decision
citing a rule version that no published snapshot sits behind is named as such.

There is **no language model anywhere in this path**, and `model_backed: false`
is published so a client can tell. That is a design decision, not a shortcoming:
the explanation surface of a glass-box system should not itself be a black box.

## 2.8 The console

[console/](console/) is React and Vite, bound to the nine contracts and asserting
nothing they do not say. Its types are generated from the service's own OpenAPI
document, so a route whose response model changed cannot leave the console
typechecking green against a shape the service no longer serves.

Four of its claims are enforced as scans over its own source in
[console.test.ts](console/src/console.test.ts), in the same habit as the DDL and
cursor hooks:

- **One score bar, three payloads.** `alert.v1`, `simulation.v1` and `ingest.v1`
  share `Signal`, `Action` and `Evidence` deliberately, so one component renders
  a stored alert, a what-if and a live authorization. A test asserts the bar
  exists in exactly one file.
- **The bar adds up exactly.** Contributions arrive as *strings* — which is what
  lets the server check `sum(signals) == score` with no tolerance — and are
  summed as scaled integers rather than parsed into floats. A payload that does
  not add up is reported on screen, not rendered quietly.
- **`persisted` decides the frame.** A simulated decision and a real one are
  never visually confusable, and the frame comes from the payload's own field
  rather than from which screen is rendering.
- **Nothing asserts liveness.** Whether the engine is running comes from
  `GET /cycle` and nowhere else, and there are three answers, not two: running,
  not running, and *we could not ask* — because `/cycle` needs a token and a
  signed-out console genuinely does not know.

It runs in Docker so that Node is never installed on the host; see
[console/README.md](console/README.md) for what that arrangement decides and
§4.2 for the commands.

## 2.9 The analysis tools

Three scripts read the population and **recommend**. None of them writes. Tests
scan their source for SQL write keywords and fail if one appears.

| Script | Question it answers |
|---|---|
| [condition_report.py](scripts/condition_report.py) | Which conditions are mispriced — what the catalog charges per unit of measured precision. This is what found the `+50` misprice. |
| [calibrate_bands.py](scripts/calibrate_bands.py) | Where the `low`/`elevated`/`high` cutoffs should sit, per subject type. |
| [kpi_report.py](scripts/kpi_report.py) | The nine tiles, in a terminal, with every denominator and caveat. |

Their output becomes a **hand-written seed file** with the evidence in the
comment. [0026](db/seeds/0026_reprice_country_novelty.sql) and
[0027](db/seeds/0027_calibrate_score_bands.sql) are exactly that, and reading
them is the fastest way to understand how a change is supposed to be justified in
this repository.

---

# PART 3 — The change cookbook

## 3.1 What costs rows and what costs code

| Change | Cost | Where |
|---|---|---|
| New rule over existing features | **Two INSERTs**, or one `POST /rules`. No release either way. | `rule_definitions` + `rule_conditions` |
| New feature using an existing reducer | **One INSERT plus a runner pass.** | `feature_catalog` row with its computation spec |
| Change a threshold, price, band or dedup window | **An UPDATE**, as a new seed file. | `rule_conditions`, `score_bands`, `alert_policy` |
| New feature needing a **new reducer** | A data-engineering ticket. | A Python function in [aggregations.py](src/glassbox/features/aggregations.py) |
| New subject type beyond the seven | A code change. | [`_SUBJECT_SQL`](src/glassbox/engine/evaluation.py#L143) |
| Reading from a new table in a feature | A code change. | [`ALLOWED_RELATIONS`](src/glassbox/features/predicate.py#L26) — a deliberate allow-list |
| A new fact in an explanation | Put it on the alert. | Not a second query — [`ALLOWED_RELATIONS`](src/glassbox/explain/evidence.py#L50) is enforced by a cursor hook |

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

**Or do steps 3–4 through the API**, which is the same thing with the guardrails
attached: `POST /simulate/rule` shows what the candidate would have done against
real history, `POST /rules` validates and publishes it into shadow, and
`POST /rules/{id}/promote` lets it act. The seed-file path remains correct for
anything that ships with the repository, because a rule authored at runtime is
not in the migration ledger.

**Three traps, each of which has already cost someone a debugging session:**

- **Every mitigating feature must have `default_when_absent = NULL`.** A mitigator
  that defaults to `false` makes the whole missing-evidence policy unreachable.
  Enforced across the catalog by `test_degraded.py`, and for authored rules by
  [`validate_draft()`](src/glassbox/rules/validate.py#L106).
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
  priority formula, the cost-per-precision anchor and the score bar each exist in
  exactly one place, with a test pinning that.
- **An automatic calibration write.** Analysis recommends; a human applies.
- **A payload shape hand-written in the console.** Every type is an alias into
  the generated schema; [types.ts](console/src/api/types.ts) declares exactly one
  shape by hand and says why, and a test pins that it stays the only one.

---

# PART 4 — Running, testing, debugging

## 4.1 From nothing to running

```powershell
.\scripts\bootstrap.ps1        # ~5 minutes cold; run with the virtualenv on PATH
```

Starts PostgreSQL in Docker, installs dependencies, generates sample data,
applies every migration and seed, computes the feature layer, runs both lanes,
settles the actions, prints the three analysis reports, exports the contracts, and
runs the Python suite. It deliberately does **not** build the console — that is a
Node image, and the script's promise is a demoable database.

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
python -m glassbox serve                            # the API on :8000, cycle every 30s
```

`python -m glassbox <command>` is a thin front door onto the same scripts.

**The live demo** — five charges arriving one at a time on a device nobody has
seen. The first four are approved; the fifth scores 87 and is **declined** with a
step-up. Nothing about it is in the fixtures.

```bash
python scripts/demo_burst.py                # end to end, in process
python scripts/demo_burst.py --http         # the same, through a running service
python scripts/demo_burst.py --clean        # take it back out
```

**Everything at once**, on a machine that has Docker and nothing else — no
Python, no Node, no virtualenv. This is the shortest path to a running demo:

```bash
docker compose up --build    # database, bootstrap, API on :8000, console on :5173
```

`init` runs `scripts/bootstrap_demo.py` — the same five steps `bootstrap.ps1`
runs on a host — and `api` waits for it to **succeed** rather than to start, so
the service never comes up in front of an empty database. It **rebuilds the
database every time**, which is what makes the demo identical every time and is
also why a rule you authored through the console last night will not be there:
runtime rules are rows, not seeds.

**The console alone**, which runs in Docker so that Node is never installed on
the host:

```bash
docker compose up -d console                     # :5173
docker compose run --rm console npm test         # 48 tests
docker compose run --rm console npm run build    # -> :8000/console
```

The console has no `depends_on`, deliberately: it proxies per request rather
than talking to the API at boot, and inheriting `api`'s bootstrap would mean
running the console's test suite rebuilt the demo database first.

Sign in with `analyst-token` or `admin-token`. Reads are open, so the queue, a
case and the KPI tiles render before you do.

**Two settings to choose deliberately, not inherit:**

| Variable | Why it matters |
|---|---|
| `GLASSBOX_CYCLE_SECONDS` | The default `30` means rows appear that no click caused. Correct for a demo, confusing while building a screen. `0` disables it. The console reads which you picked off `GET /cycle` rather than assuming. |
| `GLASSBOX_HOST` | `127.0.0.1` by default, because this process commits and can decline a charge. The containerised console reaches it over the Docker bridge, which a loopback socket **refuses** — so it needs `GLASSBOX_HOST=0.0.0.0`, or every request fails exactly as though nothing were running. |

## 4.3 Proving it works

```bash
psql "$GLASSBOX_DSN" -f db/acceptance/verify_scores.sql
```

Human-readable proof, run from the repository root: the engine's computed scores
against the signed-off expectations, the rationale behind each alert, and a zero
count on both invariants. The five signed-off cases:

| Subject | Rules | Score | Band | Action |
|---|---|---:|---|---|
| `TXN-48291` | R-114 | **87** | high | `challenge` |
| `TXN-48300` | R-114 + T-021 | **68** | elevated | `monitor` — R-114 wanted `challenge`; the veto held it |
| `RING-1187` | L-203 | **64** | elevated | `hold` |
| `ACC-2201` | S-077 | **58** | elevated | `hold` |
| `TXN-48251` | T-021 | **0** | low | `allow` — the mitigators consume the accusation |

Then:

```bash
pytest                                                # 615 tests, ~2 min
docker compose run --rm console npm test              # 48 tests, ~3s
```

150 of that 615 are `test_walkthrough.py` checking this document's own links, one
parametrised case per link — so the total moves when this file does, and the
number to watch is the 465 that test the system.

The Python suite drops and rebuilds a **separate** database at session start,
runs the whole pipeline once, then runs each test inside a transaction that is
rolled back. Tests are order-independent and a test that mutates rules cannot
leak.

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
| `test_rule_validation.py` | An authored rule that fails silently instead of loudly |
| `test_shadow.py` | A rule acting before somebody promoted it |
| `test_openapi.py` | The console typechecking green against a shape the service no longer serves |
| `test_walkthrough.py` | This document citing a line that has moved |
| `console.test.ts` | A second score bar, a second `/authorize` call site, a hardcoded liveness claim |

**There is no CI.** Every mechanism above runs only when a human runs it, and
there are now two suites and two package managers. It is the largest gap in the
project.

## 4.4 Debugging by symptom

| Symptom | Look at |
|---|---|
| A score is not what you expect | `decision_conditions` — every condition the decision looked at, fired or not, with the value it saw |
| A feature reads as missing | `read_status` in that table: `absent`, `stale`, `unresolvable` or `fanout_error` each mean something different |
| A rule fires on the wrong entity | `resolution_path` on the catalog row, and `resolution_edges` |
| A rule fires on nothing at all | It is probably in `shadow`. `SELECT rule_id, status FROM rule_definitions` |
| An alert did not appear | `decisions.alert_routing` — every decision says why not |
| The API returns 500 | An invariant was violated. `SELECT * FROM v_alert_invariants WHERE NOT sum_ok` |
| Something looks structurally wrong | `SELECT * FROM v_decision_routing` — returns **zero rows** when healthy |
| The console shows every request failing | The API is bound to loopback and the console is in a container. `GLASSBOX_HOST=0.0.0.0` |
| The console's status strip says it cannot ask | Correct behaviour when signed out — `/cycle` needs a token, and it will not guess |
| A console dependency looks stale | The named volume outlives a rebuild; the entrypoint reinstalls when `package-lock.json` moves. `docker compose down -v` forces it |

---

# PART 5 — What is real and what is simulated

Anyone quoting a number out of this system needs this section. It is not
boilerplate modesty; some of these figures are exact and meaningless at the same
time, and both halves matter.

## 5.1 Real, and would work unchanged on production data

The schema; the feature computation layer; the entity resolution graph;
point-in-time reading; condition evaluation and the degraded-evidence policy;
scoring, consolidation and precedence; alert deduplication, suppression and
priority; action issuance; the authorization and ingest doors; the scheduler;
rule validation, publishing, shadow and promotion; all three simulations; all
nine contracts; the API; the console; and the entire explanation surface.
**None of these ever look at a synthetic label.** Point them at real transaction
rows and they run.

## 5.2 Simulated, and clearly marked as such

Only **three** places in the non-test code read the planted fraud label:

| Site | Powers | On real data it becomes |
|---|---|---|
| [v_kpi_decisions.sql](db/views/v_kpi_decisions.sql) | the false-negative tile | a confirmed-fraud join; recall stops being measurable without a sampled audit |
| [v_condition_performance.sql](db/views/v_condition_performance.sql) | condition precision | falls back to case-disposition precision, already in the view |
| [outcomes.py](src/glassbox/engine/outcomes.py) | settling challenges | a real step-up integration |

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

**And one thing that is not simulated but is not real either.** The bearer-token
map in [auth.py](src/glassbox/api/auth.py) is two hardcoded credentials over
plain HTTP with no expiry or rotation. It exists so the audit columns record a
true actor and the console knows which surfaces to render. Replacing it changes
that one file, because everything downstream consumes an actor *string* — but
until it is replaced, nothing here should stand in front of real money.

## 5.3 Numbers derived from this dataset

The **method** transfers; the **numbers** do not. The `+12` condition price, the
`75 / 40` band cutoffs, the 30-second cycle period and every rule threshold were
derived from this population by tools that recompute against whatever data they
are pointed at.

## 5.4 What this proves, and what it does not

**Proves:** scores are additive and fully decomposable, verified in SQL; four
structurally different fraud shapes are representable in the same model;
mitigating evidence can hold a transaction below the line and cap the action; a
charge can be **stopped at the door** rather than regretted afterwards; a rule can
be authored, simulated against real history, published into shadow and promoted
without a release; new patterns arrive by `INSERT`; and every analytics figure is
computed from stored rows rather than asserted.

**Does not prove:** throughput or real latency (the millisecond budget is a design
target, not a measurement); detection quality (synthetic fraud is fraud we already
knew how to describe); resilience (nothing has failed because nothing real has
run); that a stopped charge can be released, since nothing answers a step-up; or
that analysts trust it.

Stating the second list is not modesty. A prototype that overstates what it proves
is worse than no prototype, because it moves decisions forward on evidence that
does not exist.

---

## Where to go next

- [README.md](README.md) — the shorter tour, and the current known gaps.
- [HANDOFF.md](HANDOFF.md) — week-by-week account of what was built and every
  judgment call, newest first. Read §W4.5 and §W5.1 for decisions the plans did
  not specify.
- [WEEK5-PLAN.md](WEEK5-PLAN.md) — the session-by-session record of the write
  path, with forty-three decisions and twelve defects found while taking them.
- [console/README.md](console/README.md) — what the console holds itself to, and
  what running it in a container decides.
- [architecture.md](architecture.md) — the original design document, kept
  unedited as a planning artifact. Where it and the code disagree, `HANDOFF.md`
  records why.
