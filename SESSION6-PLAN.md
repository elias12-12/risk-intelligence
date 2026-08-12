# Session 6 — demo readiness

**Purpose.** Everything in this file came out of a walkthrough of the system with
the person who has to present it. Two items are defects found by querying the
live database; the rest are places where the surface is correct and unpresentable
— copy written for a maintainer, controls that only work if you already know the
answer, and a demo that hides the thing it is demonstrating.

**Read this first if you are picking the work up:** §0 is the constraints that
will fail your build if you ignore them. §1–§13 are the work items. §14 is four
questions the author has to answer before two of the items can be finished.

Nothing in this file has been implemented. The repository is unchanged.

---

## §0 Constraints that bite

These are enforced by tests, not by convention. Violating one produces a red
build, not a code review comment.

| Constraint | Enforced by | What it means here |
|---|---|---|
| `alert.v1` is byte-frozen, SHA-256 pinned | `test_contract.py` | Never add a field to it. New fields go on a sibling contract. Touching `models.py` breaks the digest |
| Every contract is byte-compared against its committed schema | `test_contract.py` | Change a Pydantic model → regenerate with `scripts/export_contract_schema.py` and commit the result, in the same commit |
| Console types are generated from the service's OpenAPI | `test_openapi.py` | Change a route's response model → regenerate `console/src/api/schema.d.ts`. Never hand-write a payload shape; `types.ts` is the only file allowed one, and a test pins that |
| One score bar, one `/authorize` call site, no hardcoded liveness | `console.test.ts` | §13 adds scenarios to the authorize screen — keep them in `Authorize.tsx`. A second file calling `api.authorize` fails the build |
| Forbidden console copy | `console.test.ts:102` | Four regexes ban "feeds the next model retrain", "vs. last week", "compared to the previous period", "AI/LLM/GPT-powered". §6's rewrite must not reintroduce them |
| `db/` is an append-only ledger | `migrate.py`, `test_migrations.py` | Never edit an applied migration or seed. Add the next number. §2's fix is a new seed file |
| No fixture id under `db/` | `test_clusters.py` | Nothing in schema or seeds may name `RING-1187`, `TXN-48291`, `CUST-MENSAH` |
| Walkthrough citations are line-checked | `test_walkthrough.py` | Moving a cited function breaks 150 parametrised tests. Update `WALKTHROUGH.md` in the same commit |

Run both suites before handing back:

```bash
pytest                                                       # 604 tests
docker compose --profile console run --rm console npm test   # 40 tests
```

---

## §1 — Re-score from the console *(blocker; do this first)*

### The problem

Two rules were authored, simulated, published and promoted through the console
(`RF-401` refund abuse, `C-301` card-testing). **No alerts appeared and no KPI
moved.** The rules are correct; verified in the database as `status = 'active'`
with the right conditions.

Two independent causes, both confirmed:

**A. `GET /cycle` does not run a cycle.** It is a status read —
[`cycle_state()`](src/glassbox/api/routes_ingest.py#L144) returns
`ingest_watermark` state and scheduler liveness, and writes nothing. The console
calls it to render the status strip. `POST /cycle`
([`run_one_cycle()`](src/glassbox/api/routes_ingest.py#L125)) is the one that
runs. Reloading the page will never evaluate anything.

**B. `POST /cycle` would also have done nothing.** From the live database:

```
watermark async        2026-01-15 15:00:00
watermark inline_sync  2026-01-15 15:00:00
frontier (newest txn)  2026-01-15 14:45:00
```

[cycle.py:96](src/glassbox/ingest/cycle.py#L96):

```python
behind = [s for s, at in marks.items() if at is None or at < as_of]
if not behind and not force:
    return CycleResult(ran=False, reason="nothing has arrived since the last cycle")
```

Every watermark is at or past the frontier, so the cycle correctly reports there
is nothing to consume. **The cycle reacts to arriving data, not to a new rule** —
which is right, and is exactly why a new rule needs a different verb.

`force=True` does not help either: the lane loop still computes
`affected_subjects(since, as_of)` over an empty window and evaluates nothing.

Confirming evidence: zero decisions have ever been written for `RF-401` or
`C-301`, and the decision population has never contained a `customer` or
`merchant` subject (`transaction 9844, account 78, network 1`). Both new rules
introduced subject types the engine has never scored.

### What to build

`python scripts/run_cycle.py --lane async` is the correct operation — it calls
`run_lane` with `subject_ids=None`, a **full population pass** that ignores
watermarks. There is no API equivalent. Add one, and a button.

**Backend.** A new endpoint, admin-only:

```
POST /cycle/rescore?lane=async     ingest.v1     admin
```

- Call `run_lane(conn, lane, reference_now(), run_id=..., ctx=EngineContext.load(conn))`
  with `subject_ids=None`, then `watermark.advance(conn, lane, as_of)`, then commit.
  Same shape as `scripts/run_cycle.py:31-40`.
- Reuse `CycleReport` if the fields fit; add a sibling model rather than
  widening `ingest.v1`'s existing one if they do not.
- **Do not fold this into `POST /cycle`.** They answer different questions —
  "consume what arrived" vs "re-score everything against current rules" — and the
  project's own precedent (`/authorize` vs `/simulate/transaction`, §2.6 of the
  walkthrough) is that two meanings get two endpoints rather than one with a flag.

**Frontend.** A button on the rules screen, near the promote control:

> **Re-score the population** — runs every active rule over every subject again.
> A newly promoted rule has never been applied to existing data; this applies it.

Disable for non-admins. Show the returned counts (`evaluations`, `decisions`,
`alerts`) rather than a toast — the numbers are the proof it did something.

**Expect it to be slow.** The async lane covers ~2,400 in-window subjects and the
full population is ~9,900. See §14 Q1.

### Acceptance

From a clean bootstrap: author RF-401 via the console → publish → click re-score
→ `alert_routing = 'no_authority'` on its decisions (shadow) → promote → click
re-score → an alert on `CUST-REFUND` appears in the queue.

---

## §2 — Disposition verdict drift *(defect)*

### The problem

The author noticed that marking a case a false positive did not move the
percentages. Verified: **it moves some tiles and not others**, because four views
disagree about what a case's verdict is.

`case_outcomes` is append-only, latest-wins. Week 5 changed `v_kpi_cases` to
latest-wins so an analyst could correct the synthetic settler. **Three sibling
views were not changed.**

| View | Line | Ordering | Wins |
|---|---|---|---|
| `v_kpi_cases` | [:50](db/views/v_kpi_cases.sql#L50) | `ORDER BY decided_at DESC, outcome_id DESC` | **latest** |
| `v_kpi_rule_attribution` | [:42](db/views/v_kpi_rule_attribution.sql#L42) | `ORDER BY decided_at, outcome_id` | first |
| `v_kpi_executions` | [:32](db/views/v_kpi_executions.sql#L32) | `ORDER BY decided_at, outcome_id` | first |
| `v_condition_performance` | [:52](db/views/v_condition_performance.sql#L52) | `ORDER BY decided_at, outcome_id` | first |

Live proof — three cases carry an analyst correction on top of a synthetic
verdict, and the two views disagree on every one:

| alert | `v_kpi_cases` | `v_kpi_rule_attribution` |
|---|---|---|
| 4 | `confirmed_legit` (analyst) | `confirmed_fraud` (script) |
| 5 | `confirmed_fraud` (analyst) | `false_positive` (script) |
| 7 | `false_positive` (analyst) | `confirmed_fraud` (script) |

Consequence in the console: **false-positive rate, validation outcomes and median
triage respond to a correction; per-rule precision, prevention FP/TP and
condition precision are frozen on the script's original verdict forever.**

The irony is documented in the file that broke.
[v_kpi_cases.sql:9-13](db/views/v_kpi_cases.sql#L9-L13) says the CTE is *"kept
identical on purpose so the two views cannot drift into disagreeing about what a
case's verdict is."* Duplicating the CTE to avoid a view dependency is what made
the drift possible.

### The fix

A **new seed file**, `db/seeds/0033_align_verdict_ordering.sql` — not an edit to
the view files if that would break their standalone re-appliability; check how
`migrate.py` applies `db/views/` before choosing. Either way the change is one
clause in three places:

```sql
(array_agg(disposition ORDER BY decided_at DESC, outcome_id DESC))[1]
```

**Do not** change `min(decided_at)` anywhere. The triage clock deliberately runs
to the *first* disposition — "how long until this was worked" and "what did we
conclude" are different questions, and `v_kpi_cases` mixes them on purpose and
says so.

Write the reason into the seed's comment header, in the house style: the finding,
the evidence (the three-row disagreement above), and what it moves.

### Also add a regression test

`tests/test_dispositions.py` — write two dispositions on one alert with opposite
verdicts, then assert **all four views** report the later one. This defect is a
duplicated CTE drifting; the test is the only thing that stops it drifting again.

### Acceptance

Disposition a case in the console. Per-rule precision changes on the next load.

---

## §3 — One command to run everything

### Goal

`docker compose up` (or one script) brings up database, backend and console, with
data loaded, ready to demo. Today it needs: Docker for the database, a host
virtualenv for `bootstrap.ps1`, `python -m glassbox serve` in one terminal, and a
`docker compose --profile console` for the UI.

### What to build

**A backend image.** New `Dockerfile` at the repo root (or `docker/api.Dockerfile`):
Python 3.11+, `pip install -r requirements.txt`, source at `/app/src`, entrypoint
`python -m glassbox serve`.

**A new `api` service** in [docker-compose.yml](docker-compose.yml):

```yaml
api:
  build: { context: ., dockerfile: Dockerfile }
  container_name: glassbox_api
  depends_on:
    db: { condition: service_healthy }
  ports: ["8000:8000"]
  environment:
    GLASSBOX_DSN: postgresql://glassbox:glassbox@db:5432/glassbox
    GLASSBOX_HOST: 0.0.0.0            # required, see below
    GLASSBOX_NOW: 2026-01-15T15:00:00+00:00
    GLASSBOX_CYCLE_SECONDS: 0         # see §14 Q3
```

Three things that will go wrong if not handled:

- **`GLASSBOX_HOST` must be `0.0.0.0`.** The default is loopback and a container
  binding loopback is unreachable from outside it. The failure looks exactly like
  the service being down. This is already documented for the host case in
  [config.py:48-58](src/glassbox/config.py#L48-L58); it is now mandatory.
- **The DSN host changes from `localhost:55432` to `db:5432`** inside the compose
  network. Keep the published `55432` on the db service so host tooling and
  `pytest` keep working unchanged.
- **The console's `GLASSBOX_API` becomes `http://api:8000`**, not
  `host.docker.internal:8000`. Keep the `host.docker.internal` default for anyone
  running the API on the host; override it in the compose service.

**Bootstrap-on-first-run.** The API container needs the database migrated,
seeded, loaded and feature-computed before it serves anything useful. Options:

- an `init` one-shot service that runs `scripts/reset_db.py` then exits, with
  `api` depending on its completion — cleanest, and idempotent if `reset_db.py`
  is safe to re-run;
- or an entrypoint that checks for a sentinel (does `rule_definitions` have
  rows?) and bootstraps only if empty.

See §14 Q2 — whether a second `up` should reset or preserve data is the author's
call, and it matters: a demo that wipes the rules you authored last night is
worse than one that needs a flag.

**Drop the `console` profile** so a bare `docker compose up` starts all three,
or keep it and add a top-level convenience script. Note the profile exists
because `bootstrap.ps1` promises a fast database-only path — if the profile goes,
check that script still works.

### Acceptance

On a machine with only Docker: `git clone` → `docker compose up` → console at
:5173 with the queue populated, API at :8000, no Python or Node on the host.

Update `README.md`, `console/README.md` and `WALKTHROUGH.md §4.1–4.2`.

---

## §4 — Rename the demo users

`nadia.analyst` → **`jane.analyst`**, `omar.admin` → **`joe.admin`**.

**Source of truth:** [auth.py:37](src/glassbox/api/auth.py#L37).

```python
DEFAULT_TOKENS = "analyst-token:jane.analyst:analyst,admin-token:joe.admin:admin"
```

**Also update** (all references verified, `.venv` excluded):

| File | What |
|---|---|
| `tests/test_api_auth.py` | lines 64, 65, 74, 76, 115, 140 |
| `tests/test_dispositions.py` | line 30, `ANALYST` |
| `tests/test_publish.py` | line 47, `ADMIN_ACTOR` |
| `console/src/components/SignIn.tsx` | any name in the copy |
| `README.md`, `console/README.md`, `WALKTHROUGH.md`, `HANDOFF.md`, `WEEK5-PLAN.md` | prose mentions |
| `scripts/bootstrap.ps1` | token references |

The bearer tokens themselves (`analyst-token`, `admin-token`) do **not** change —
only the actor strings.

**Do not rewrite existing audit rows.** `case_outcomes.analyst_id`,
`rule_definitions.created_by` and `rule_versions.published_by` record who
actually acted. Rewriting them would be falsifying an audit trail in a system
whose entire argument is that the audit trail is trustworthy. New rows get the
new names; old rows keep theirs. A fresh bootstrap regenerates everything anyway.
See §14 Q4 if the author disagrees.

---

## §5 — KPI window and date controls

`GET /kpis` already accepts both parameters
([routes_kpis.py:25](src/glassbox/api/routes_kpis.py#L25)) — `window_days`
(1–365, default 7) and `as_of` (default `reference_now()`). **No backend change
is needed.** The console simply never sends them.

Add to [Kpis.tsx](console/src/screens/Kpis.tsx) (39 lines today):

- a date/time input for `as_of`, pre-filled with the value the payload returns
- a window input or select for `window_days` (suggest 1 / 7 / 14 / 30 / 90)
- **defaults unchanged** — an untouched screen must produce exactly today's payload

Two things to surface, because they are the most confusing behaviour in the
system and the controls make them visible rather than mysterious:

- **The window is half-open, `(start, end]`.** A transaction dated after
  `window_end` is excluded. This is why an alert raised on 16 January does not
  appear in a window ending 15 January — not caching, not a stale view.
- **Deltas vanish above ~15 days.** The dataset spans 30, so any window over half
  of that has no preceding equal-length baseline, `baseline_available` goes
  false, and every delta is correctly null. `baseline_absent_reason` already
  explains why on the wire — **render it.** Today it is dropped, and a screen
  full of blank deltas with no explanation reads as broken.

Setting the window to 30 and watching the deltas correctly disappear, with the
reason printed, is a good demo beat. Make sure it works.

---

## §6 — Rewrite the KPI tile copy

### The problem

Every tile carries `basis` and `requires` strings written for a maintainer:

> *"cases raised or restated, over every decision evaluated. A folded evaluation
> is the same case seen again and is not counted twice."*
> *"§9 dedup + §10 population scoring; the denominator is decisions.alert_routing,
> which did not exist before 0023"*

`requires` is archaeology — which internal milestone made the tile possible. It
means nothing to an audience.

### What to do

- **Stop rendering `requires`.** Leave the field on the wire (it is part of
  `kpis.v1`; removing it is a contract change) — just do not display it.
- **Replace the `basis` line with two short lines:** what the tile *means*, and
  how the number was *computed*.
- **Keep `caveat` and the `synthetic` flag, visibly**, but simplify the wording.
  These are the honesty machinery and the strongest thing in the demo. Do not
  bury them.

### Suggested copy

Whether these live in the console or replace the server-side `basis` strings is
§14 Q5 — but if they go server-side, `kpis.v1` bytes change and the schema must
be regenerated and committed.

| Tile | Definition | How it was computed |
|---|---|---|
| Alert volume | How many cases came out of how much traffic | Cases raised or restated ÷ every decision evaluated in the window. A repeat of the same case is not counted twice |
| Score distribution | The shape of the population's risk | Decisions grouped by subject type and band. The headline is how many scored above zero |
| False-positive rate | How often we alerted on something a human said was fine | (false positive + confirmed legitimate) ÷ cases with any verdict. "Inconclusive" is excluded from both sides |
| False-negative rate | How much known fraud we missed | Labelled fraud that never became a case ÷ all labelled fraud |
| Validation outcomes | What humans concluded about our alerts | One verdict per case, counted by verdict |
| Median triage time | How long a case waits before someone works it | From when the event happened to the first verdict on it |
| Block / challenge / fail-open | How often we acted on a customer, and how it went | Preventive actions ÷ decisions evaluated. Counted per action issued, not per decision — one step-up per customer, not one per re-check |
| Per-rule precision | Which rules are worth their alerts | Cases a rule cited evidence on that were confirmed fraud ÷ cases it cited evidence on with any verdict |
| Emerging trends | Which fraud patterns are moving | Distinct reason codes cited by cases in the window, each compared with the previous window |

### Suggested caveats

| Current | Replacement |
|---|---|
| `SYNTHETIC_GROUND_TRUTH` | **We planted this fraud, so we can measure whether we caught it. Real data has no answer key — there, recall needs a sampled audit of traffic we never alerted on.** |
| `SYNTHETIC_OUTCOMES` | **No customer answered these step-ups; a script settled them. Every such row is marked synthetic.** |
| `FAIL_MODE_IS_POLICY` | **This is the lane's policy, not an observed failure. Nothing has failed, because nothing real has run.** |
| `_provenance_note` | **N of M verdicts here were written by a script rather than by an analyst.** *(keep it derived from `case_outcomes.source` — do not hardcode)* |

**Two traps.** The four forbidden regexes in `console.test.ts:102` still apply.
And `_provenance_note` is *derived* for a reason recorded in
[0029](db/migrations/0029_case_outcome_provenance.sql): it used to be a hardcoded
sentence that became false the moment a real analyst wrote a verdict. Simplify
the words, keep the derivation.

---

## §7 — Describe each emerging trend

The tile lists bare reason codes: `VELOCITY_SPIKE`, `NEW_MCC`, `PASS_THROUGH`,
`VETO_APPLIED`. Add a plain-language gloss beside each.

`ref_reason_code` already has a `description` column — **check whether it is
populated before writing a lookup in the console.** If it is, publish it through
`kpis.v1`'s parts or the existing `/reference` endpoint (`catalog.v1`) and render
it. Adding it to `kpis.v1` changes that schema; using `/reference` does not.

Live codes and suggested wording:

| Code | Description |
|---|---|
| `VELOCITY_SPIKE` | Unusual burst of charges in a short window |
| `NEW_DEVICE` | A device fingerprint nobody has seen before |
| `GEO_ANOMALY` | Location inconsistent with recent activity |
| `NEW_MCC` | First time this customer used this kind of merchant |
| `DEVICE_FANOUT` | One device behind several accounts |
| `STRUCTURING` | Amounts kept just under a reporting threshold |
| `PASS_THROUGH` | Money leaves almost as fast as it arrives |
| `CREDENTIAL_EVENT` | A password reset or similar just before the movement |
| `PAYEE_DRAIN` | A new payee paid most of the balance |
| `DATACENTER_IP` | Session came from a datacenter, not a home connection |
| `SPEND_NORMAL` | Amount matches this customer's usual spending *(mitigating)* |
| `CARD_PRESENT` | Physical card used with chip-and-PIN *(mitigating)* |
| `TRAVEL_EXPLAINED` | A travel booking explains the location *(mitigating)* |
| `VETO_APPLIED` | Evidence for the customer capped the action |

Mark the mitigating ones visually. An audience that sees only aggravating codes
misses that the system argues both ways.

---

## §8 — Simulation screen: usable controls

[Simulate.tsx](console/src/screens/Simulate.tsx)

**Subject type → dropdown.** It is a free-text input today. The seven valid
values are already published by `GET /reference` (`catalog.v1`,
`subject_types`) — read them, do not hardcode.

**Lane and subject type are coupled, and this is the main failure mode.** Only
subject types some rule targets *in that lane* can be planned; anything else
returns `SubjectNotEvaluable`. `network` on `inline_sync` fails. Either filter
the lane options by the chosen subject type, or show the constraint in the error.

**Offer known-good subjects.** The author cannot guess valid ids. Either
pre-populate a datalist from `GET /queue`, or offer these five signed-off
examples as one-click presets:

| Subject | Id | Lane | Expect |
|---|---|---|---|
| transaction | `TXN-48291` | inline_sync | 87 · high · challenge |
| transaction | `TXN-48300` | inline_sync | 68 · elevated · monitor *(veto capped it)* |
| transaction | `TXN-48251` | inline_sync | 0 · low · allow *(mitigators consumed it)* |
| network | `RING-1187` | async | 64 · elevated · hold |
| account | `ACC-2201` | async | 58 · elevated · hold |

**Replay ceiling — remove it from the default view.** The author reports that
pasting the placeholder text verbatim does not work, which is itself the
argument: a control whose valid input is not guessable does not belong on a demo
screen. Verify the parsing bug separately (the placeholder is
`2026-01-15T14:07:11Z`; check what the client actually sends and whether the
route's `datetime` coercion accepts it) — but either way, move it behind an
"advanced" toggle or drop it from the UI. **Keep the backend parameter.** It is
the audit capability; it is just not a live-demo control.

---

## §9 — Simplify the simulation copy

Replace:

> *"Set this to a stored decision's `decided_at` and the answer is what that
> decision saw rather than what we would say today. That is the audit question
> the bitemporal feature store exists to make answerable."*

If the control survives §8, use:

> **Leave blank for today's answer.** Set a past date to see what the system
> actually knew at that moment — useful when data has been corrected since.

If it does not survive, delete the paragraph.

Simplify the panel headings too:

- *"Re-derive a stored subject"* → **"Re-score something we already decided"**,
  with one line: *Runs the same engine over existing data. Nothing is saved.*
- *"A charge that never happened"* → keep the title; it is good.

---

## §10 — Explain the inline-only limit on fabricated charges

On the "a charge that never happened" panel, add:

> **Only fast-lane transaction rules can fire here.** A single made-up charge is
> judged the way a card terminal would judge it — on its own, in milliseconds.
> Patterns that need several transactions to become visible (mule rings, account
> takeover) run in the slower lane and cannot be triggered by one hypothetical
> charge.

Correct, and it pre-empts *"why didn't the mule-ring rule fire?"*

Consider also warning when the fabricated charge would not reach a threshold —
notably that `card_cnp_count` reads **1**, not 5, for a lone charge with no burst
behind it, because the scoped feature pass window is one microsecond wide
([simulate.py:424](src/glassbox/engine/simulate.py#L424)). This is the single
most likely "the engine looks broken" moment on stage.

---

## §11 — Rules screen: feature descriptions, fewer columns

[Rules.tsx:99-121](console/src/screens/Rules.tsx#L99-L121)

**Remove the `Spec` and `Absent` columns.** Keep Feature, Entity, Kind, Window,
Inline.

**Add a plain description per feature.** `feature_catalog.description` is already
populated and already published through `catalog.v1` — check whether the existing
text is usable before writing new copy. Suggested wording if not:

| Feature | Description |
|---|---|
| `card_cnp_count` | Card-not-present charges on this card in the last 90 seconds |
| `card_cnp_pace_ratio` | That rate compared with the card's normal 30-day pace |
| `device_first_seen_min` | Minutes since this device was first seen anywhere |
| `session_geo_jump_km` | Distance from the last different location on this card |
| `mcc_is_new_for_customer` | Whether this customer has ever used this kind of merchant |
| `accounts_per_device` | How many accounts were opened on this device this week |
| `structuring_flag` | Incoming amounts sit just under the reporting threshold |
| `pass_through_ratio` | How much of the money in went straight back out |
| `passthrough_time_min` | Minutes between money arriving and leaving |
| `activity_is_passthrough_only` | No salary, card or bill activity — the account only forwards money |
| `ring_cohesion` | How tightly linked the accounts in this group are |
| `min_since_password_reset` | Minutes from the last password reset to this transfer |
| `new_payee_then_drain` | A newly added payee was paid most of the balance |
| `ip_is_datacenter` | The session came from a datacenter, not a home connection |
| `amount_over_avail_balance_pct` | The transfer as a percentage of available balance |
| `country_is_new_for_customer` | First transaction this customer has made in this country |
| `recent_travel_purchase` | A flight or travel booking on this card in the last week |
| `amount_vs_baseline_z` | How far the amount sits from this customer's usual spend |
| `entry_mode_chip_pin` | The physical card was used with chip-and-PIN |
| `card_txn_count_24h` | All transactions on this card in 24 hours |
| `merchant_decline_burst` | Declined attempts at this merchant in 10 minutes |
| `customer_refund_count_30d` | Refunds to this customer in 30 days |
| `customer_refund_amount_30d` | Total value refunded to this customer in 30 days |
| `card_challenge_fails_30d` | Failed identity checks on this card in 30 days |

The last one is worth a note in the UI: it is produced by the system's own
actions — the engine challenges, the outcome settles, and it becomes evidence
the next rule can read.

**Do not lose the "no default — observable as absent" distinction entirely.** It
is the load-bearing detail behind the missing-mitigator policy. Move it to the
rule-authoring screen where it is actionable, rather than the catalog table where
it is noise.

---

## §12 — Rule detail: copy and column help

[RuleDetail.tsx:222-237](console/src/screens/RuleDetail.tsx#L222-L237)

**Replace this:**

> *"v_condition_performance over decision_conditions: every condition of every
> applicable rule, fired or not. precision_pct is direction-aware — the fraud
> rate for an aggravator, the legitimate rate for a mitigator — measured against
> transactions.synthetic_label, which is exact on this dataset and meaningless
> outside it. points_per_precision_point is what the catalog charges per unit of
> measured precision, and HIGH IS BAD."*

**With this:**

> **How each condition is performing.** Every condition of every rule that
> applied, whether it fired or not.
>
> A condition that adds points is right when it fires on fraud. A condition that
> *subtracts* points is right when it fires on legitimate activity — that is its
> job — so it is scored the other way round. Higher precision is better for both.
>
> ⚠️ Measured against fraud we planted in this sample data. Exact here,
> meaningless anywhere else.

**Add per-column help** (tooltip or a definitions block under the table):

| Column | Help text |
|---|---|
| Evaluated | How many decisions looked at this condition |
| Fired | How many times it was true |
| Fire rate | Fired ÷ Evaluated — how much of the population it touches |
| Fired on fraud | Of the times it fired, how many were on known fraud. *(Reads "Fired on legitimate" for a mitigating condition — the header flips, because that is what a mitigator should do)* |
| Precision | How often it was right. Adds points: % of firings on fraud. Subtracts points: % on legitimate. Higher is better either way |
| Pts / precision pt | What this condition charges per unit of accuracy. **High is bad** — the points are not being earned |
| Degraded | How many times the evidence was missing or too old to use |

**Worth surfacing on screen, because it is the best story in the module:** the
`Pts / precision pt` column is what found a real defect. `country_is_new_for_customer`
was priced at +50 and scored 7.4 — 4.4× the next aggravator. Investigation found
the +50 had been reverse-engineered from a screenshot to make a total add up. It
was repriced to +12 in
[0026](db/seeds/0026_reprice_country_novelty.sql), with the evidence in the
comment. A one-line "this column found a mispriced rule" link to that seed makes
the point better than any explanation.

**Also add a low-sample warning.** A row reading `Evaluated 1 · Fired 1 · Precision
100%` is not a measurement. Grey out or flag rows below ~30 evaluations. Seed
0026 makes exactly this argument when it refuses to anchor on
`device_first_seen_min` (36 firings, all on planted fixtures).

---

## §13 — Rebuild the "Send a charge" screen

### The problem

[Authorize.tsx](console/src/screens/Authorize.tsx) sends five charges and shows
outcomes, but the charges themselves are invisible — the audience sees a button
and then a verdict. The details are the argument: five charges, twenty seconds
apart, same card, a device nobody has seen, an IP 1,412 km from home. Without
them the decline looks arbitrary.

And it is one scenario. Three would show the range.

### 13a — Show the charges

Before sending, render the five charges as a table: sequence, time, amount, card,
merchant, MCC, channel, entry mode, country, device, IP. Highlight what changes
between them (the clock) and what does not (everything else).

As each is sent, show the running score and the decision inline, so the audience
watches 0 → 0 → 0 → 0 → **87 declined** rather than being told about it.

Add a short "why this is not staged" note — the reasoning already exists verbatim
in [demo_burst.py:14-33](scripts/demo_burst.py#L14-L33):

> Twenty seconds apart, so all five fall inside the 90-second window. A device
> nobody has seen, so the first charge registers it. Dated at the sample data's
> reference instant, because a charge dated today would look at a card with no
> history at all.

### 13b — Add two more scenarios

Scenario picker, three options. All must be `inline_sync` transaction subjects —
that is the only thing `POST /authorize` can judge (see §10).

**Scenario 1 — Card-testing burst (existing).** Five CNP charges, `CARD-4417` /
`CUST-OKAFOR` / `MER-GIFT`, mcc 5815, 20s apart, unseen device, `40.71/-90.78`.
→ four approved, fifth scores **87** and is **declined** with `step_up_required`.
*Shows: prevention.*

**Scenario 2 — The veto.** One chip-and-PIN charge abroad on a customer with a
travel booking on file. Verified as available in the data: `CUST-MENSAH` /
`CARD-9954`, country `PT`, `entry_mode` `chip_pin`, ~86.20 — the shape of
`TXN-48251`, and `recent_travel_purchase` is true for this customer at the
reference instant.
→ new-country evidence appears, three mitigators consume it, score lands at
**0** (a net-negative pool is dropped whole), action `allow`, **approved**.
*Shows: the system arguing for the customer. This is the most under-sold capability
in the demo.*

**Scenario 3 — Ordinary traffic.** One unremarkable chip-and-PIN charge at a
normal merchant, home country, known device.
→ score **0**, no signals, approved, no alert.
*Shows: the system is quiet on normal traffic — the control case that makes the
other two mean something.*

**Verify scenarios 2 and 3 against a fresh bootstrap before committing.** Feature
values depend on the generator, and `CUST-MENSAH`'s travel flag is what makes
scenario 2 work. Assert it rather than assume it.

**Constraint:** `console.test.ts:62` asserts exactly one file calls
`POST /authorize`. Three scenarios, one call site.

**Note:** these commit. Each run writes real rows with a fresh id prefix. That is
deliberate and defensible — the response carries `persisted: true` and the case is
in the queue a click away. But a demo run three times leaves three sets of rows.
Consider a visible "clean up" control mirroring `demo_burst.py --clean`.

---

## §14 — Questions for the author

**Q1 — Re-score endpoint: blocking or background?**
A full async-lane pass covers ~9,900 subjects. If it takes more than a few
seconds, a synchronous HTTP call will look hung. Options: (a) synchronous with a
spinner, simplest, fine if it is genuinely fast — **measure it first**; (b)
background with a job id the console polls. Recommend (a) unless measurement says
otherwise. §1 assumes (a).

**Q2 — Should `docker compose up` reset the database?**
Reset-every-time gives a guaranteed-identical demo but destroys rules authored in
a previous session. Preserve-if-present keeps your work but can drift. Recommend
**preserve if the database already has rules, with an explicit
`docker compose run reset` to wipe** — and say which you want.

**Q3 — Scheduler on or off in the packaged compose?**
Worth revisiting: with the watermark at the frontier and no data arriving, the
scheduler ticks every 30s and does nothing visible. It only produces movement
when something arrives through a door (`/authorize`, `/ingest/*`, `demo_burst`).
So `GLASSBOX_CYCLE_SECONDS=0` costs almost nothing and makes every change
traceable to a click. Recommend `0` in the compose default, documented, easy to
flip. Confirm.

**Q4 — Rename historical rows?**
§4 recommends leaving existing `omar.admin` / `nadia.analyst` audit rows alone
and only using the new names going forward, on the grounds that rewriting an
audit trail is exactly what this system argues against. If you want a clean demo
with no old names visible anywhere, the answer is a fresh bootstrap rather than
an UPDATE — confirm which.

**Q5 — KPI copy: console-side or server-side?**
Console-side is free. Server-side (replacing the `basis` strings) means the copy
travels with the payload to any client, but changes `kpis.v1`'s bytes — schema
regenerated and committed, `test_contract.py` re-run. Recommend **console-side**
for §6, keeping the server strings as the technical record. Confirm.

---

## §15 — Suggested order

1. **§2** disposition drift — a defect, small, self-contained, and every metric
   screen is wrong until it lands
2. **§1** re-score endpoint — unblocks demoing rule authoring at all
3. **§3** one-command run — everything after this is easier to test
4. **§4** rename — trivial, do it while the container work is settling
5. **§5–§7** KPI screen
6. **§8–§10** simulation screen
7. **§11–§12** rules screens
8. **§13** authorize screen — largest, most visible, do it last with the most time

Update `WALKTHROUGH.md` and `HANDOFF.md` as you go, not at the end;
`test_walkthrough.py` fails the build on a stale citation, and it is 150 of the
604 tests.

---

## §16 — Notes carried from the walkthrough

Things confirmed against the live database during the session that produced this
document, worth knowing before you touch anything.

- **`WALKTHROUGH.md` says T-021's aggravator is +50. It is +12.**
  [0026](db/seeds/0026_reprice_country_novelty.sql) repriced it and the doc was
  not updated. Fix while you are in there.
- **The tiles are windowed; the queue is not.** 7 alerts exist, 5 fall inside the
  default 7-day window. This accounts for most "why don't these numbers match"
  questions and is worth stating in the UI.
- **`v_kpi_rule_attribution` counts (rule, case) pairs, not cases.** 5 cases
  produce 7 rows because two rules asserted evidence on cases a third carried.
- **The 95% false-negative rate is a coverage number, not an engine weakness.**
  Four rules describe four fraud shapes; the data has more. Measured on the live
  data: a single condition `card_cnp_count >= 3` fires on 66 of 149 fraud
  transactions and **1 of 9,683 legitimate ones** — recall would go from ~5% to
  ~31% for two INSERT statements. That reframing is the strongest argument the
  demo has, and §13's screens should leave room for it.
- **`RF-401` and `C-301` already exist in the dev database** as `active`, authored
  through the console. They have never been evaluated (§1). A fresh bootstrap
  will not contain them — they are runtime rules, not seeds.
