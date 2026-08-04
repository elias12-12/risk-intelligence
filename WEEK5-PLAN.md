# Week 5 — Plan: roles, dispositions, simulation, rule authoring, console

**Status:** sessions 1–4 of 5 complete — 393 tests green, `alert.v1`'s digest
unmoved. Session 5 remains. Each ends with the suite green and this file
updated.

This is a **living document**, unlike `architecture.md` (a planning artifact kept
unedited) and unlike each week's section of `HANDOFF.md` (left as it was
written). Sessions are expected to change it. When Week 5 completes, its account
moves to `HANDOFF.md` as §W5 and this file becomes the artifact that stops being
edited.

---

## How to use this document

**Status marks**, one per deliverable:

| Mark | Meaning |
|---|---|
| **TODO** | not started |
| **WIP** | in progress; the session that owns it says so in the progress log |
| **DONE** | built, tested, and the acceptance criteria below actually pass |
| **CUT** | deliberately dropped — with the reason, in place, never deleted |

**Update protocol**, at the end of every session:

1. Flip the marks you earned. Do not flip a mark on an untested deliverable.
2. Append one entry to the **Progress log** at the bottom: date, session, what
   landed, what did not, and anything the next session needs to know.
3. If you took a decision this plan did not specify, add a row to **Decisions
   taken along the way** with its reasoning. That table is the reason this
   project's history is readable; a decision whose justification lives in a diff
   is a decision nobody can explain later.
4. If you found a defect, record it in **Defects found along the way** whether or
   not you fixed it.

---

## Where this starts

Week 4 is complete: every numbered item of `architecture.md` Part I is DONE, five
contracts are published and `alert.v1` is frozen and digest-pinned. The read API
serves seven GET endpoints. Nothing writes.

Week 5 adds the first write path in the project's history, and that is the whole
reason it is sequenced the way it is: **the control plane is writable but not
publishable**, and an admin write endpoint shipped before the publish step means
a human can put a live `challenge` on a customer with no record of what changed.

### Four defects this work must close

Found by reading the code, none of them owned by `README.md` or `HANDOFF.md`.
They are not side quests: three of the five sessions exist partly to close them.

| # | Defect | Evidence | Closes in |
|---|---|---|---|
| ~~D1~~ | ~~**No version counter is ever bumped.**~~ **CLOSED, session 3.** `publish_rule_version` / `publish_feature_version` (migration `0030`) snapshot the current definition and bump the counter *only when the definition actually moved*; `rules/publish.py` is the one caller on the write path and seed `0031` is the other, backfilling the four rules and 23 features that predate the publish step. A stored `rule_version_set` now resolves to the definition it was made under, and the case report checks rather than claims | was: `db/seeds/0026_reprice_country_novelty.sql:65`; no `SET version` anywhere | Session 3 |
| ~~D2~~ | ~~**Shadow mode is inert.**~~ **CLOSED, session 3.** `catalog.ACTING_RULE_STATUSES` is the gate: a shadow rule is excluded from consolidation and from precedence — no signal, no authority, no severity, no veto, no alert, no execution — and is still fully evaluated, its conditions landing in `decision_conditions` flagged `is_shadow` and its would-be action on `decisions.shadow_action`. `catalog.v1`'s `takes_action` publishes `false` for it, and `test_shadow.py` asserts both halves | was: `src/glassbox/catalog.py:85`; `src/glassbox/engine/precedence.py` | Session 3 |
| ~~D3~~ | ~~**Dispositions have no provenance column.**~~ **CLOSED, session 1.** `0029` adds `case_outcomes.source`; `v_kpi_cases` carries it as `disposition_source`; three tiles derive their caveat from it and one of them stops carrying a caveat at all when no synthetic verdict is in the window. `test_dispositions.py` asserts the flip in both directions | was: `db/migrations/0008_feedback.sql:9-17`, `engine/outcomes.py:205`, `contract/kpis.py:386` vs `:403` | Session 1 |
| ~~D4~~ | ~~**Admin-authored rules have no validation to fail against.**~~ **CLOSED, session 2.** `rules/validate.py` rejects twenty-two distinct shapes, every one of which previously produced a rule that did *nothing* rather than a rule that failed. The operator list it checks against is now a tuple in `engine/conditions.py`, imported by both the interpreter and the validator, so there is no second definition to drift. `test_rule_validation.py` asserts a 422 for each, and asserts that every rule this repo ships still validates clean | was: `db/migrations/0006_rules.sql`; `src/glassbox/engine/conditions.py:76` | Session 2 |

### One more, flagged and not scheduled

**There is no CI.** No `.github/` at all. The digest freeze, the DDL hook, the
`ON CONFLICT` grep test, the mitigator validator — every enforcement mechanism
the project's claims rest on runs only when a human runs it, and there is already
a merge commit from a second contributor. `requirements.txt` pins nothing and
there is no lockfile, so a three-minute reproducible bootstrap is one upstream
release away from a red suite that has nothing to do with the code.

A Postgres service container plus `pytest` is roughly twenty lines. **Not in
scope until asked** — recorded here so it is not rediscovered.

---

## Decisions already taken

Settled before session 1 opened. Each is a decision, not a default.

| # | Decision | Why |
|---|---|---|
| 1 | **Two demo users, static `token → (actor, role)` map in config. No users table** | The scope is a demo with one analyst and one admin. A users table implies user management, and nothing here manages users. The map is one config block and is trivially replaced by real identity later, because everything downstream consumes an actor *string* |
| 2 | **The admin promotes `shadow` → `active` themselves; `published_by` records who** | With two users there is no real separation of duties, so a second-approver gate would be theatre. Recording the actor is the part that survives into a real deployment |
| 3 | **`alerts.status` stays engine-owned. `case_outcomes` is the disposition record** | The analyst marks a case; the engine owns the lifecycle. Two writers on one column is how a status becomes unreadable |
| 4 | **Dispositions are append-only; latest wins** | An analyst changing their mind is a second row. The first judgement stays in the record — the same argument as the append-only migration ledger and `feature_values` |
| 5 | **The queue filters on "has no outcome yet", not `status = 'open'`** | Consequence of 3: nothing ever leaves `open`, so the old predicate would never empty the queue |
| 6 | **Simulation and publish are separate endpoints sharing one validator** | `POST /simulate/rule` rolls back by construction; `POST /rules` writes. Same body, same validation, different guarantee — the test path can never accidentally become the write path |
| 7 | **"Delete a rule" is `status = 'inactive'`** | `decisions.action_source_rule`, `decisions.vetoed_by` and `alert_signals.source_rule_id` all reference `rule_definitions` with no `ON DELETE` clause, so Postgres already refuses to delete a rule that ever acted. That is the schema defending the audit trail. A never-published draft may be hard-deleted; `rule_conditions` cascades |
| 8 | **`simulation.v1` and `catalog.v1` are siblings. `alert.v1` is never reopened** | Same pattern as `queue.v1` and `executions.v1`. `Signal`, `Subject`, `Action` and `Evidence` live inside `alert.v1`'s `$defs` closure, so they may be **reused read-only** — adding one field to any of them for a new surface's benefit breaks the frozen digest |
| 9 | **React with a Vite dev proxy (`/api` → `:8000`), not a CORS allowlist** | The bundle is served same-origin from FastAPI in the end, so CORS never becomes a production surface. If the proxy is rejected, the fallback is an explicit dev-origin allowlist — never `*` |

---

## Open decisions

Answer these when you reach them; record the answer in the table above.

| # | Decision | Note |
|---|---|---|
| ~~O1~~ | ~~**The population sample cap for `/simulate/rule`**~~ | **ANSWERED, session 2: 2,000 by default, overridable per request, and the cap published as the denominator.** `population.subjects_available`, `subjects_evaluated`, `sample_cap` and `truncated` all ride on the payload with a `basis` naming the sampling rule. The sample is the **most recent** N by trigger time, not the planner's own prefix — see decision 18. A 2,000-subject what-if on the transaction lane takes ~1.6s |
| ~~O2~~ | ~~**Does a restated case reappear in the queue after disposition?**~~ | **ANSWERED, session 1: no — a case a person worked stays out, and `include_worked=true` is how you look at it.** The stronger option (return it when new evidence arrives after the verdict) was rejected for this dataset, not in principle: the synthetic settler derives `decided_at` from `first_event_at`, so "new evidence after the verdict" fires on an essentially arbitrary subset of the fixtures and would read as noise in a demo. Revisit when a case is folded onto after a human has judged it, which nothing here does yet |
| ~~O3~~ | ~~**What a `shadow` rule writes**~~ | **ANSWERED, session 3: it evaluates, records everything, and takes nothing.** Three columns on `decisions` (`shadow_score`, `shadow_action`, `shadow_rules`) and one flag on `decision_conditions` (`is_shadow`, with `contributed` forced to 0 by a rewritten CHECK). The recorded answer is what the WHOLE decision would have been with the shadow rules active — live and shadow rules consolidated together — not what the shadow rule would say alone: promoting a rule does not move it into an empty room, a live veto still caps it and §6 still deduplicates it. "Evaluate and record nothing" was rejected because it makes promotion a leap of faith and reduces `shadow` to a spelling of `inactive`. No separate `shadow_decisions` table: a shadow evaluation is not a second decision, and a row of its own would land in the denominator of alert volume and every rate §11 publishes |
| ~~O4~~ | ~~**Whether `/simulate/transaction` is reachable by the analyst role or admin only**~~ | **ANSWERED, session 4: admin only.** It is the one simulation that fabricates an EVENT, and an event is what this system's entire record is made of — a payload showing a score against a transaction id is one screenshot away from being read as something that occurred. Every other defence is on the payload (`persisted: false`, the row echoed as inserted, four named limits) and every one of them depends on somebody reading it; the role is the defence that does not. The cost is real and accepted: an analyst working a case cannot ask *"what if this charge had been $4,000?"* without an admin. Revisit if the console makes that a common question |

---

# Session 1 — Foundation, the analyst write path, simulation (a)

**Goal:** an authenticated analyst can mark a case false-positive, and anyone can
ask the engine to re-derive a subject's decision without persisting it.

Closes **D3**.

### Deliverables

| | Item | Notes |
|---|---|---|
| **DONE** | `src/glassbox/api/auth.py` | `token → (actor, role)`, overridable by `GLASSBOX_API_TOKENS`. `require_role` dependencies, roles ORDERED so `admin` implies `analyst`. Reads left open, deliberately, and the module says why |
| **DONE** | `db/migrations/0029_case_outcome_provenance.sql` | `case_outcomes.source`, backfilled to `'synthetic'`. Also adds the **disposition vocabulary CHECK** 0008 only ever had as a comment — see decision 12 |
| **DONE** | `engine/outcomes.py` writes `source='synthetic'` explicitly | |
| **DONE** | `POST /alerts/{alert_id}/outcome` + `GET` | Actor from the principal, never the body — `analyst_id` is not a field on the request model, so an attempt to set it is a 422 |
| **DONE** | Queue predicate → **"no verdict from a person"** | Not "no outcome" — see decision 11, which is the difference between a working queue and an empty one |
| **DONE** | The disposition caveats derived, not hardcoded | Three tiles, not one: validation outcomes, median triage time, **and false-positive rate**, which never claimed to be synthetic and always was |
| **DONE** | `contract/simulation.py` — `simulation.v1` | Reuses `Subject`, `Signal`, `Action`, `Evidence` unmodified. Adds `RuleTrace` — which rules looked and declined, which fired without authority |
| **DONE** | `engine/simulate.py` — `simulation_scope()` | Refuses an autocommit connection outright; `force_rollback`, savepoint-nested inside a caller's transaction |
| **DONE** | `EvaluationResult` → `simulation.v1` serializer | Signals come from `persist.ranked_signals`, now public — decision 13 |
| **DONE** | `POST /simulate/subject` | Plans through `plan_evaluations`, so it sees the same trigger row a real cycle would |
| **DONE** | Exporter emits `simulation.v1` **and `dispositions.v1`** | Both byte-checked and closed-field-checked by the existing parametrised tests, with no edit to `test_contract.py` |
| **DONE** | *(not planned)* `contract/dispositions.py` — `dispositions.v1` | The write surface publishes a contract like every read surface does |
| **DONE** | *(not planned)* `GET /me` | The console cannot decide whether to render the admin surfaces without it |
| **DONE** | *(not planned)* `v_kpi_cases` verdict is latest-wins, carries `disposition_source` | Decision 10 |
| **DONE** | *(not planned)* `scripts/migrate.py` re-applies views every run | **D5** — a view edit had silently no effect on an existing database |

### Acceptance

- An analyst token dispositions a case; an admin token also can; no token → 401.
- A second disposition on the same alert is a **second row**; the API reports the
  latest and can return the history.
- `alerts.status` is unchanged by a disposition — asserted, not assumed.
- A test writes one human disposition and asserts the validation-outcomes tile's
  caveat **changes**. That is D3 closed, demonstrated rather than described.
- `POST /simulate/subject` reproduces **87 / 68 / 64 / 58 / 0** for the five
  fixtures, including TXN-48300's veto signal and TXN-48251's empty pool.
- `replay_as_of` against a stored decision reproduces that decision's score and
  action.
- **A simulate call writes nothing:** row counts on `decisions`, `alerts`,
  `alert_signals`, `feature_values` and `action_executions` unchanged.
- `alert.v1`'s digest has not moved.
- Suite green.

### Traps

- `run_lane(..., persist=False)` returns counters and **discards the results**
  (`engine/evaluation.py:284-295`). Session 1 wants `evaluate()`
  (`evaluation.py:298`), which returns the result directly.
- The simulate path must not reach `persist.write_batch` at all — not with a flag,
  not with a branch. Different call site, enforced by the scope manager.
- Adding one field to `Signal` for simulation's benefit breaks the frozen digest.
  Reuse it as-is or define a new model.

---

# Session 2 — Admin read surface, the validator, simulation (b)

**Goal:** the admin can see every rule and every catalog feature, draft a
candidate rule, and test it against history before anything is written.

Closes **D4**.

### Deliverables

| | Item | Notes |
|---|---|---|
| **DONE** | `contract/catalog.py` — `catalog.v1` | `RuleSummary`, `RuleDetail`, `FeatureView`, `ReferenceVocabulary`. A sibling; `alert.v1`'s digest unmoved |
| **DONE** | `GET /rules` | Including `inactive` ones — this is the control plane, not the engine's view of it |
| **DONE** | `GET /rules/{rule_id}` | Conditions, versions (empty, and it says so), and per-condition performance from `v_condition_performance`. A condition never evaluated publishes `performance: null` plus the reason — see decision 17 |
| **DONE** | `GET /features` | The catalog with its computation spec. `has_default` carries the §5 distinction JSON cannot |
| **DONE** | `GET /reference` | From the `ref_*` rows, except operators and reducers, which come from the interpreter — decision 16 |
| **DONE** | `rules/validate.py` — the validation layer | All nine of the listed rejections, plus four the list did not name: an operator with nothing to compare against, an unknown `reason_code`, a veto rule with no mitigating condition, and a subject type the planner cannot reach |
| **DONE** | Collecting evaluation path | `evaluate_population()` — a generator, consumed by `run_lane` as well, so the persisting and collecting paths cannot batch differently |
| **DONE** | `POST /simulate/rule` — **admin only** | Apply draft → load `EngineContext` (after) → evaluate → roll back. Also handles the EDIT case: an existing `rule_id` is replaced inside the sandbox (decision 19) |
| **DONE** | Aggregate response | `RuleSimulation` on `simulation.v1`: population with the cap as denominator, would-fire / would-authorise / would-carry-action / would-alert, band distribution, ground-truth precision, per-condition performance, worked examples with their bars, and the diff |
| **DONE** | *(not planned)* The lifecycle and operator vocabularies became single definitions | `catalog.RULE_STATUSES` and `conditions.SUPPORTED_OPERATORS`, imported by the loader, the validator and `/reference` |

### Acceptance

- A draft of `C-301` (card testing, merchant subject, `merchant_decline_burst`)
  fires on the planted burst and reports it, having written nothing.
- Every rejection in the validator list has a test asserting a **422** — in
  particular a typo'd operator, which today produces a rule that silently never
  fires.
- An analyst token on `/simulate/rule` → **403**.
- Row counts unchanged across a simulate-rule call, including `rule_definitions`
  and `rule_conditions`.
- `GET /rules/{id}` returns condition performance for a rule that has fired, and
  degrades honestly for one that has not.
- Suite green.

### Traps

- **Build `EngineContext` *after* inserting the candidate rule.** It snapshots
  rules at load time (`EngineContext.load`), so a context built first will not see
  the draft and the simulation will report that the rule does nothing.
- `db.connect()` defaults to `autocommit=False` — keep it that way on this path.
  An autocommit connection turns the rollback into a no-op and the "test" writes
  the rule.
- `_load_policies` reads per call by design; do not add caching to make this
  faster without reading `persist.py:123-127` first.

---

# Session 3 — The publish path

**Goal:** an admin can save a rule, and saving it produces an audit trail rather
than an overwrite.

Closes **D1** and **D2**. This is the session that pays for the whole feature.

### Deliverables

| | Item | Notes |
|---|---|---|
| **DONE** | `POST /rules`, `PUT /rules/{rule_id}` — **admin only** | `api/routes_rules.py`. Same body, same `ensure_valid`, no validation of its own. A create that names an existing rule is a 409; a PUT whose path and body disagree is a 422 |
| **DONE** | `rules/publish.py` — the publish step | Definition → version → snapshot, in one call that commits or does neither. The bump is **conditional** — see decision 21 |
| **DONE** | `POST /rules/{rule_id}/promote` — **admin only** | `shadow` → `active`, publishing a version whose `published_by` is the promoter and whose `status` is `active` |
| **DONE** | The shadow gate | `catalog.ACTING_RULE_STATUSES`, applied in `evaluate_batch`: shadow rules never reach `consolidate` or `precedence.decide`. Excluding them from the **veto pass** matters as much as from authority — decision 22 |
| **DONE** | `DELETE /rules/{rule_id}` → `status='inactive'` | `?purge=true` hard-deletes a draft that never published and refuses everything else; the FKs refuse the rest independently |
| **DONE** | Feature-catalog publish, same shape | `POST /features/{key}/publish`, on the same conditional-bump rule. There is no feature *authoring* endpoint, deliberately — decision 23 |
| **DONE** | Amend `test_explain.py:239` | Now two tests: the report says its versions resolve **and has checked**, and a version whose definition is deleted is still named as a gap |
| **DONE** | Backfill seed for existing definitions | `0031`, through the same SQL functions the write path calls. It also states what it does NOT do about `0026`'s unversioned reprice, because that is a claim about history |
| **DONE** | *(not planned)* `tests/test_shadow.py` | Nine tests. The gate had no test module to live in, and D2's two halves — records everything, takes nothing — are different claims |

### Acceptance

All met.

- Editing a rule bumps its version and leaves the previous definition retrievable
  from `rule_versions`. ✔ `test_editing_a_rule_bumps_it_and_keeps_the_previous_definition`
- A decision stored before an edit resolves its `rule_version_set` to the
  definition **as it was**, not as it now is. ✔ `test_a_stored_decision_resolves_to_the_definition_it_was_made_under`,
  which reprices `session_geo_jump_km` on the live R-114 and checks the stored
  decision still names v1 and v1 still says 18.
- A rule at `status='shadow'` produces no alert, no preventive action, and no
  execution row. ✔ `test_shadow.py` — and it produces no *signal* either, which
  turned out to be the sharper form: shadowing R-114 takes `TXN-48291` from 87
  to 0 with an empty bar, not to a smaller number.
- Promotion moves it to `active` and it then acts. ✔ over HTTP, and a second
  promotion is a 409.
- The case report stops saying its version numbers resolve to nothing. ✔ and it
  asks the version stores rather than assuming — deleting a snapshot inside a
  test makes it print the gap sentence again.
- Suite green: **369** (was 334).

### Traps

- The version bump belongs to **publish**, not to save-the-row. `decisions`
  stores `rule_version_set` at evaluation time; a counter that moves on every
  keystroke makes the set meaningless in the other direction.
- The shadow gate and the write endpoints must land **together**. A rule that
  saves as `shadow` while nothing reads `status` is a rule that starts
  challenging customers the moment it is saved.
- Seed `0026` changed a price without a bump. Decide whether the backfill
  retroactively represents that as version 2 — and if it does, say so in the seed
  comment, because it is a statement about history.

---

# Session 4 — Simulation (c), the hypothetical transaction

**Goal:** type a charge that never happened and see what the engine would say.

### Deliverables

| | Item | Notes |
|---|---|---|
| **DONE** | `POST /simulate/transaction` — **admin only** | `engine/simulate.simulate_transaction`: prepare (outside the scope), then insert → scoped feature pass → plan → evaluate → roll back, all inside one `simulation_scope` |
| **DONE** | Scoped feature pass | Built on the INCREMENTAL runner rather than `run_for_entity` — see decision 27. `runner.driven_by('transactions')` answers "which features does an arriving charge drive", derived from each compiled spec's **driver** relation |
| **DONE** | Request model | `TransactionDraft` on `simulation.v1`: the writable subset of `transactions`, closed. References are checked before the sandbox opens, so an unknown card is a 422 naming it rather than an `IntegrityError` |
| **DONE** | Documented limits in the response payload | Four, and a fifth when a spec is uncomputable. Derived from what the pass did, not written out — see decision 29 |
| **DONE** | *(not planned)* `synthetic_label` is refused, not merely omitted | It is planted ground truth. A fabricated charge that labelled itself would answer the question it was asked — decision 28 |
| **DONE** | *(not planned)* `tests/test_simulate_transaction.py` | 24 tests. The scope is watched writing and watched being gone, on the real path |

### Acceptance

All met.

- A fabricated CNP burst on a real card trips R-114 and returns its signals. ✔ a
  sixth CNP charge on `CARD-4417` scores **87 / high / challenge** with all four
  signals — and `card_cnp_count` reads **6** where the stored value at that
  instant is 5, which is the assertion that proves the pass ran rather than the
  read finding the burst that already happened.
- **After the call, `transactions` and `feature_values` row counts are
  unchanged.** ✔ across eleven tables, in-process and over HTTP — and separately
  `test_the_feature_pass_writes_inside_the_scope_and_is_gone_after_it`, which
  watches `feature_values` grow inside the scope and be gone after it.
- The response names the limits rather than implying a full evaluation. ✔ four
  codes, each carrying the feature keys it applies to.
- Suite green: **393** (was 369).

### Traps

- Never run this path on an autocommit connection.
- The feature pass writes `feature_values` inside the transaction. That is fine
  rolled back and catastrophic committed. The scope manager is the only thing
  standing between those two outcomes — test it directly, not incidentally.

---

# Session 5 — React console and the documentation

**Goal:** the analyst and admin surfaces exist in a browser, and the project's
own record explains what was built and why.

### Deliverables

| | Item | Notes |
|---|---|---|
| **TODO** | Vite + React scaffold, dev proxy `/api` → `:8000` (decision 9) | Client types generated from the committed JSON Schemas or `/openapi.json` |
| **TODO** | Queue and alert detail | Bound to `queue.v1` and `alert.v1`. The queue renders the **published priority factors**, since the ordering explains itself |
| **TODO** | Disposition control | The analyst's false-positive mark, against session 1's endpoint |
| **TODO** | KPI tiles | Render each tile's `basis`, `caveat`, `numerator`/`denominator` **from the payload**. Do not write tile copy in the UI |
| **TODO** | Rule list, rule detail, rule authoring, simulate, publish, promote | The admin surface, against sessions 2 and 3 |
| **TODO** | §11's "console copy that outruns the system" | `architecture.md` flags two strings — an escalation toast promising a model retrain, and deltas implying a measured prior period. Neither is true. This is the moment they were deferred to |
| **TODO** | `HANDOFF.md` §W5, `README.md` updates | Newest week first. Every decision from this file's tables carries into the handoff |

### Acceptance

- Both roles' flows work end to end in a browser against a bootstrapped database.
- No tile in the console asserts anything the payload does not say.
- No number is rendered without the denominator or caveat the contract publishes
  alongside it.
- `HANDOFF.md` §W5 exists and this file is marked superseded.

---

## Cross-cutting invariants that must not break

Check these at the end of every session, not only at the end of Week 5.

1. **`sum(signals) == score`** — `v_alert_invariants`, the Pydantic validator, and
   the suite. Three layers, and simulation payloads are now a fourth surface where
   it must hold.
2. **`alert.v1`'s digest has not moved.** New surfaces are siblings.
3. **Never `ON CONFLICT … DO UPDATE` on `feature_values`.** A grep test enforces
   it; session 4 wrote to that table from a new call site and did it through the
   incremental runner rather than around it (decision 27), so the one INSERT the
   grep test guards is still the only one.
4. **Simulation writes nothing.** Row-count assertions, on every simulation path
   — and on the transaction path also a direct test that watches the scoped
   feature pass write inside the scope and be gone after it.
5. **The explanation surface reads six relations and no more.**
   `explain/evidence.ALLOWED_RELATIONS`, proved by a cursor hook. Nothing in this
   plan should widen it; if a console screen needs a fact from outside that set,
   the fact belongs on the alert.
6. **Every number that reaches an explanation passes through `Quoter.q`.**

---

## Decisions taken along the way

Append here. One row per decision the plan did not specify, with its reasoning.

| # | Session | Decision | Why |
|---|---|---|---|
| 10 | 1 | **`v_kpi_cases` publishes the LATEST disposition as the verdict, not the first — but keeps the clock on the first** | First-wins was right while the only writer was a script that wrote each case once. With a human writer it would publish the verdict most likely to be wrong while storing the right one. The clock stays on `min(decided_at)` because "how long until this was worked" and "what did we conclude" are different questions, and a correction hours later does not mean triage took hours longer. No number moved on the shipped fixtures — every case has exactly one disposition — which is what made the change safe to take now rather than later |
| 11 | 1 | **The queue filters on `source = 'analyst'`, not on "has any disposition"** | `resolve_actions.py` dispositions every open case in one synthetic pass, so a queue keyed on any disposition is EMPTY after a normal bootstrap — the demo would open on an empty queue with nothing to work. A fixture script closing a case is not an analyst having worked it. This is the second use for 0029's column and, on this dataset, the more load-bearing one |
| 12 | 1 | **0029 also adds the disposition-vocabulary CHECK that 0008 only ever had as a comment** | A fifth disposition value would not raise anywhere: `v_kpi_cases` classifies on four literals, so it would land in neither `is_true_positive` nor `is_false_positive` and quietly deflate every rate over it. The Pydantic `Literal` guards the endpoint; this guards every other writer. Same layered-enforcement argument §1 makes for the sum invariant |
| 13 | 1 | **`persist._ranked_vetoes` became public `persist.ranked_signals`, returning the whole ordered bar** | `simulation.v1` renders a bar for an evaluation that was never stored. A second implementation of "what goes on the bar, in what order" would be invisible until the simulated bar and the alert bar disagreed about the same subject — the failure mode §3.1 argues about for features and W3.6 #3 for the fire verdict |
| 14 | 1 | **`queue.v1` gained one field (`worked_by_analyst`); `alert.v1` did not move** | A queue that hides worked cases but cannot say which are worked forces a second request per row. queue.v1 is a sibling, not frozen, no console is bound to it yet, and the schema diff is exactly one property. `alert.v1`'s pinned digest is unchanged, which the suite proves |
| 15 | 1 | **`decided_at` on a human disposition is real wall-clock time** | A person decided when they decided. The consequence is real and stated in `contract/dispositions.py`: on fixtures pinned to January, a verdict written today gives a first-time case a triage time of months. It does not distort the tile, because the clock takes the FIRST disposition and every fixture case already carries a synthetic one |
| 16 | 2 | **The vocabularies are validated against ROWS, not against a Pydantic `Literal` — but operators and reducers are validated against the INTERPRETER** | `ref_action`, `ref_subject_type`, `ref_execution_mode` and `ref_reason_code` exist so a new value is an INSERT rather than a migration; a `Literal` on the draft model would be a second copy that goes stale the moment somebody uses the seam as designed. Operators are the opposite case: they are not data, they are what `conditions.fires()` implements, so the tuple lives beside the implementation and the validator, the loader and `/reference` all import it. Decision 12 took the layered approach for dispositions because that vocabulary is a fixed four; this one is deliberately open |
| 17 | 2 | **A condition that has never been evaluated publishes `performance: null` and a sentence, not zeroes** | `fire_rate: 0%, precision: 0%` on a newly authored rule reads as a measurement of a bad condition rather than as an absence of evidence — and this surface exists precisely so an admin can price a condition against what it earns. The same argument §11 makes for a KPI delta with no baseline window: a number with no denominator behind it is worse than no number |
| 18 | 2 | **The sample cap takes the MOST RECENT N subjects by trigger time, not the planner's own prefix** | The transaction planner orders by `occurred_at` and the dimension planners order by id, so a prefix or suffix of the planner's order means something different for every subject type — and on the transaction lane it would take the OLDEST 2,000, excluding every planted fixture and the burst a card-testing draft exists to find. Most-recent is also what an admin means: a rule is authored against what is happening now. Deterministic, so a second run reproduces the first |
| 19 | 2 | **`/simulate/rule` also accepts an EDIT, not only a new draft** | The plan's table said "insert draft". Restricting it to that would have made the "decisions whose score or action would change" diff able to show only subjects a NEW rule newly touches — and the change an admin most needs to see before making is a REPRICE, which is what Week 4's `0026` was. Repricing `session_geo_jump_km` from 18 to 5 in the sandbox reports `TXN-48291` moving 87 → 74 and `challenge` → `alert`, before the seed is written. It is also the shape session 3's `PUT /rules/{id}` needs anyway. Replacement mode UPDATEs the definition and DELETEs its conditions, which cascades to `decision_conditions` — all rolled back, and `test_simulate_rule.py` asserts the conditions come back byte-identical |
| 20 | 2 | **`RuleSimulation` is published on `simulation.v1`, not on `catalog.v1`** | The guarantee it carries is `persisted: false`, and that guarantee is what the contract is for. `catalog.v1` is what the control plane IS; `simulation.v1` is what the engine WOULD say. Adding a model to a sibling changes that sibling's bytes — a reviewed diff, which `test_contract.py` forces — and `alert.v1` is untouched |
| 21 | 3 | **The version bump is CONDITIONAL: publishing a definition that has not moved changes nothing** | The plan's own trap says a counter that moves on every keystroke makes `rule_version_set` meaningless in the other direction — a hundred versions, ninety-nine identical, and no way to see which change mattered. So `publish_rule_version` compares the current definition (and its conditions, with `condition_id` stripped — it is a surrogate key recreated on every edit) against the snapshot at the current version: absent → store it where it is, different → bump then store, identical → nothing. The backfill falls out of the same rule rather than needing a mode of its own: four rules stable since Week 1 do not become version 2 for being written down |
| 22 | 3 | **The shadow gate excludes a shadow rule from the VETO pass, not only from authority** | A veto is the one rule shape that changes an outcome without scoring, so a gate applied only at authority would leave a shadow rule able to *soften* a live decision — capping a `challenge` to `monitor` while appearing to do nothing. Same class of accident as a shadow rule blocking someone, in the direction that is harder to notice. `test_shadow.py::test_a_shadow_veto_cannot_cap_a_live_action` pins it on TXN-48300, where T-021 is exactly that rule |
| 23 | 3 | **There is no feature-AUTHORING endpoint, only a feature-PUBLISH one** | Writing a computation spec is a data-engineering act, not an admin one — the README's extension-cost table says so, and `predicate.ALLOWED_RELATIONS` and `aggregations.REDUCERS` are deliberate allow-lists a form cannot widen. What D1 needs on the feature side is not authoring: it is that after a seed edits a catalog row, *something* records that the row moved, or `feature_version_set` names a spec version with no definition behind it. `POST /features/{key}/publish` is that something, and it is idempotent |
| 24 | 3 | **A candidate rule is simulated as `active`, and the payload says so** | `RuleDraft.status` defaults to `shadow` and the publish path forces new rules there, so after the gate a what-if run "as authored" would report — accurately and uselessly — that the rule does nothing. `simulate_rule` applies the draft as active inside the sandbox and publishes `evaluated_as: "active"` plus a `shadow_note`, rather than leaving a reader to infer which question was answered. `simulation.v1` gains two fields and `alert.v1` does not move |
| 25 | 3 | **An edit may not change a rule's status, and a new rule may not be published `active`** | Both are refused with a 409 naming the endpoint that does mean to do it. The alternative — accepting the field and quietly landing the rule in shadow — leaves an author believing they published a live rule when they did not, which is the worst of the three states. The mirror case matters more: an edit that could also flip shadow → active is an edit that starts acting on customers as a side effect of fixing a typo |
| 26 | 3 | **`explain/evidence.ALLOWED_RELATIONS` gains the two version stores** | A deliberate widening of a boundary this project treats as load-bearing, so it is recorded rather than done quietly. §13's constraint 4 requires the report to cite the rule version set; now that those numbers resolve, the honest thing to print is which definitions are actually retrievable, and that cannot be known without looking. The lookup is keyed on `(rule_id, version)` pairs already ON the alert — a pointer being resolved, with no degrees of freedom in the `WHERE` clause, not free-text recall. `transactions`, `feature_values`, `case_outcomes` and every other alert are still outside the boundary and the cursor hook still proves it |
| 27 | 4 | **The scoped feature pass is built on the INCREMENTAL runner, not on `run_for_entity`** | The plan pointed at `run_for_entity`, which computes a value and does not store it — and the engine reads `feature_values`, so a value that is never written is a value the point-in-time read cannot find. Storing its return by hand would have been a second writer to a bitemporal append-only table, with its own idea of `as_of`, `computed_at` and `spec_version`; that is the train/serve divergence §3.1 argues about, aimed at the one table where a divergence is unrecoverable. `run_feature(as_of=occurred_at, since=occurred_at − 1µs)` gets the same narrowing out of the runner's own watermark: the work list is the fabricated row and anything real sharing its instant, so the pass writes at most two values per feature and every one of them goes through the same INSERT a real cycle uses |
| 28 | 4 | **`synthetic_label` is REFUSED by the engine, not merely absent from the request model** | It is planted ground truth — the denominator of §11's false-negative tile and of every precision number `/simulate/rule` publishes. Omitting it from `TransactionDraft` stops the HTTP caller; refusing it in `prepare` stops every caller, including a test and a future console-side batch path. Same layered-enforcement argument decision 12 made for the disposition vocabulary, and it is what turns "nothing labels it, so it sits outside every ground-truth join" from a published caveat into an enforced one |
| 29 | 4 | **The two ways of not being recomputed are kept APART in the runner, not classified afterwards** | `driven_by` returns a `DriverSplit` — driven, driven-by-another-relation, uncomputable — because they are different claims about the answer: `accounts_per_device` was read at a stored value that is correct and current, while `new_payee_then_drain` has no computed value at all. The first draft merged them into one `skipped` map and the contract layer recovered the distinction by looking for `"driven by"` in the reason string, which is a second definition of the split living in a substring |
| 30 | 4 | **The sandbox writer stayed in `engine/simulate.py` rather than moving beside `rules/publish.apply_definition`** | Session 3's handoff suggested the opposite, and the reasoning behind it does not carry: `apply_definition` is shared because the rule what-if and the publish path write the SAME rows, and the only difference is the COMMIT. A fabricated transaction has no publish path and never will — there is nothing on the other side to share with, and `rules/` is the control plane. `insert_arrival` and `scoped_feature_pass` are public rather than private for the reason the plan's own trap gives: the guarantee has to be testable directly, not inferred from a row-count total |

---

## Defects found along the way

Append here, fixed or not.

| # | Session | Defect | Status |
|---|---|---|---|
| D5 | 1 | **Editing a view had no effect on an existing database.** `scripts/migrate.py` honoured the migration ledger for `db/views/*.sql`, but a view is edited in place — there is no `v_kpi_cases_2.sql` — so the ledger entry from its first run meant every later edit applied to a fresh database and silently skipped an existing one. Unnoticed because no view had ever been changed; 0029's `disposition_source` is the first, and `kpi_report.py` would have failed against any dev database with "column does not exist" | **FIXED.** `migrate.is_view()`; views re-apply every run (they open with `DROP VIEW IF EXISTS` and are idempotent by construction), and `--status` prints `always` for them |
| D7 | 2 | **A subject type can be added by INSERT but only reached by a code change, and nothing noticed the gap.** `ref_subject_type` is a vocabulary table — "new values are INSERTs, never migrations" (`0001_reference.sql`) — but the planner reaches a subject type only if `engine/evaluation._SUBJECT_SQL` has an entry for it. A rule on a subject type that exists in the vocabulary and not in the planner is LOADED by the engine, never planned, and never evaluated: no error, no firing, nothing in the ledger. The two sets happen to be equal today (seven each), which is why nobody had hit it | **GUARDED, not fixed.** `PLANNABLE_SUBJECT_TYPES` is now derived from the planner and `rules/validate.py` rejects a draft naming anything outside it, with a message saying a new subject type is a code change. The README already says so in its extension-cost table; now the validator does. Actually reaching an eighth subject type is still a code change, by design |
| D8 | 3 | **Editing a rule destroys the condition ledger behind its old conditions.** `rule_conditions.condition_id` is referenced by `decision_conditions` with `ON DELETE CASCADE` (0023), and an edit replaces a rule's conditions wholesale — so repricing a condition deletes every historical row that recorded it firing. The evidence §10 used to *find* the misprice is deleted by the act of fixing it, and `v_condition_performance` then reports the new price as though it had always been in force | **OPEN, and named in `publish.apply_definition`'s docstring.** The definition survives — that is what `rule_versions.conditions` is for — but the per-firing history does not. Not fixed here because the fix is a schema change with a real cost either way: `ON DELETE SET NULL` orphans ledger rows from the condition they measured, and versioning `rule_conditions` properly means the ledger points at `(condition_id, version)`, which is a migration on the largest table in the database (79,068 rows per cycle). Worth doing before any real repricing history matters; harmless on a dataset rebuilt from scratch |
| D9 | 3 | **`v_condition_performance` does not separate shadow firings from live ones.** The view groups by `condition_id` and now sees `is_shadow` rows too. Every rate in it (fire rate, direction-aware precision) is still correct — a firing is a firing — but `mean_contribution` is diluted across a promotion boundary, because a shadow firing contributes 0 by construction | **OPEN, deliberately, and unreachable today.** No rule ships in shadow, so the view is byte-identical on this dataset. Separating them is one `is_shadow` column in the `GROUP BY` and a decision about what `GET /rules/{id}` should show for a rule that has been both — which is a question worth answering with a real shadow rule in front of you rather than in advance |
| D6 | 1 | **Published schemas contain dangling `$ref`s.** The exporter uses `ref_template="#/$defs/{model}"`, which is document-root-absolute, but Pydantic nests the referenced definitions under each model's own `$defs` — so `#/$defs/Signal` in `alert.v1` resolves to nothing at the document root. Pre-existing since Week 2 and true of every contract, not introduced here | **OPEN, and deliberately not fixed.** Correcting the template changes `alert.v1`'s bytes, which is exactly the deliberate act the pinned digest exists to force. It costs nothing until session 5 generates a TypeScript client from these files, which is when it must be decided — as `alert.v2`, or by hoisting `$defs` in the exporter and re-pinning |

---

## Progress log

Append one entry per session. Newest last.

| Date | Session | Landed | Did not land | For the next session |
|---|---|---|---|---|
| 2026-08-03 | — | This plan | — | Start session 1 |
| 2026-08-03 | 1 | Everything in the session-1 table, plus four items it did not plan: `dispositions.v1`, `GET /me`, the `v_kpi_cases` verdict change, and the migrate view fix (**D5**). **262 tests** (was 215), all green. **D3 closed.** `alert.v1`'s digest unmoved; `queue.v1` gained one field as a reviewed diff | Nothing from the session-1 list was cut | **Session 2.** Three things it should know: (1) the validation layer it builds is shared with session 3's publish path, so put it in `rules/validate.py` and not inside a route; (2) `simulation_scope` is built and tested, including the autocommit refusal — session 2's rule what-if goes through it rather than managing its own transaction; (3) **`run_lane(persist=False)` still discards its results** (`evaluation.py:284-295`) — that is session 2's first piece of work, and `evaluate()` is not a substitute because it evaluates one request at a time |
| 2026-08-03 | 2 | Everything in the session-2 table, plus the two vocabulary consolidations it did not plan. **334 tests** (was 262), all green. **D4 closed**, **O1 answered**, **D7 found and guarded**. `catalog.v1` published; `simulation.v1` gained `RuleSimulation` as a reviewed diff; `alert.v1`'s digest unmoved | Nothing from the session-2 list was cut. `/simulate/rule` reports `would_alert` as an upper bound rather than a case count, because §9's hygiene runs at persist time and nothing is persisted — stated on the payload, not fixed | **Session 3, and it is the one that pays for the feature.** Four things it should know: (1) `rules/validate.py` is built and shared — `POST /rules` calls `ensure_valid` and needs no validation of its own, and `normalised()` is the only thing that rewrites a draft; (2) the publish path can lift `_apply_draft` out of `engine/simulate.py` — it is already the exact INSERT/UPDATE pair, minus the rollback; (3) **the shadow gate has a published field waiting for it**: `RuleSummary.takes_action` returns `True` for a shadow rule today and `test_catalog_api.py` asserts that, with a comment saying to flip both when the gate lands — that test is the tripwire, not a contradiction; (4) O3 is still open and session 3 must answer it — what a shadow rule WRITES |
| 2026-08-04 | 3 | Everything in the session-3 table, plus `tests/test_shadow.py`, which it did not plan. **369 tests** (was 334), all green. **D1 and D2 closed**, **O3 answered**, **D8 and D9 found** and both left open with reasons. Migration `0030` (three shadow columns, one ledger flag, two publish functions), seed `0031` (the backfill, and what it deliberately does not claim about `0026`), `rules/publish.py`, `api/routes_rules.py`. `simulation.v1` and `catalog.v1` changed as reviewed diffs; `alert.v1`'s digest unmoved | Nothing from the session-3 list was cut. Two things are *deliberately* not done: `v_condition_performance` still pools shadow firings with live ones (**D9**, unreachable on this dataset), and an edit still cascades away its own condition ledger (**D8**) | **Session 4** — `POST /simulate/transaction`. Four things it should know: (1) `simulation_scope` is unchanged and still the only thing standing between a fabricated `feature_values` row and a corrupted audit store — test it directly, not incidentally; (2) `rules/publish.apply_definition` is now shared by the publish path and the rule what-if, so anything session 4 adds to the sandbox belongs beside it rather than inside `engine/simulate.py`; (3) a fabricated transaction will be evaluated against **live rules only** — a shadow rule will produce `shadow_action` on the simulated result exactly as it does on a stored decision, and `SimulatedDecision` already has the three fields for it; (4) the import order is now `contract/simulation.py` -> `engine/simulate.py` -> `rules/publish.py` -> `contract/catalog.py`, so anything session 4 adds to `rules/publish.py` may import `contract.catalog` and must not import `contract.simulation` |
| 2026-08-04 | 4 | Everything in the session-4 table, plus two items it did not plan: the engine REFUSES a fabricated `synthetic_label` rather than merely omitting it, and `tests/test_simulate_transaction.py` (24 tests). **393 tests** (was 369), all green. **O4 answered: admin only.** `simulation.v1` gained `TransactionSimulation` as a reviewed diff; `alert.v1`'s digest unmoved. No migration, no seed — this session is entirely code, which is itself worth noting: fabricating an event needed nothing the schema did not already have | Nothing from the session-4 list was cut, and no defect was found. Two things are *deliberately* not done: the fabricated charge is evaluated as a `transaction` subject in one lane only — the card, account and customer it references are not re-evaluated, so S-077 and L-203 cannot appear in the answer — and graph features are read at their stored value rather than rebuilt. Both are named on the payload's `limits` rather than left to be discovered | **Session 5** — the React console and the documentation, and it is the only session left. Five things it should know: (1) **`contract/simulation.v1.schema.json` moved this session and `alert.v1` did not** — the digest test is the tripwire, and a TypeScript client generated from these files hits **D6** (dangling `$ref`s, open since Week 2 and true of every contract), which session 5 must decide about rather than discover; (2) there are now **three** simulation endpoints and two of the three are admin-only — `/simulate/subject` is the analyst's, `/simulate/rule` and `/simulate/transaction` are not, so `GET /me` is what the console renders off; (3) `TransactionDraft.columns()` drops unstated fields deliberately — an omitted column is not a null one, and a form that posts `null` for every empty input will get a different answer than one that omits them; (4) the limits on a transaction simulation are **rows with codes**, so render them from the payload exactly as §11's tile caveats are rendered — writing that copy in the UI is the same mistake in a new place; (5) the two console strings `architecture.md` §11 flags (the retrain toast, the deltas implying a measured prior period) are still unwritten and unfixed, and session 5 is where they were deferred to |
