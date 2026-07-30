# Week 2 handoff — GlassBox

**Branch:** `week2-contract-engine` · **Base:** `main` @ `3c640f6`
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
degraded evidence.

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

- **`new_payee_then_drain`** is `source_kind='sequence'`; the runner raises
  `UnsupportedSourceKind` and the generator hand-seeds the one value. Week 3.
- **`action_executions`** is a table with no machinery. §8 is Week 3.
- **Batch/incremental consistency testing** is Week 3 (§17).
  `run_population(as_of=<historical>)` already gives the batch behaviour, so
  there is no second implementation to build.
- **Alert dedup folding** — `dedup_key` is computed and stored; the folding
  behaviour it enables is Week 3 (§9).
- **Acceptance is checked against fixtures, not the population.** §4 and §5 are
  *demonstrated*, not stress-tested. On the labelled cohort the four rules act on
  4 of 158 fraud-labelled transactions with 1 false positive — a deliberately
  unflattering number, since the cohort was sized so most clusters fall below
  R-114's line. A cohort the rules catch entirely would make §11's
  false-negative tile read 0% and prove nothing.

---

## 6. Where to start on Week 3

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

`db/migrations/0011`–`0014`, `db/seeds/0015`–`0022`, `db/views/`,
`db/acceptance/`, `src/glassbox/` (23 modules), `scripts/` (8), `tests/` (11
modules, 104 tests). ~7,400 lines. Week 1's `0001`–`0010` are `git mv`'d to
canonical names with their contents untouched — and they **do** apply cleanly,
which the plan flagged as unverified. `score_and_verify.sql` is deleted;
`week1-data-model.md` is marked superseded rather than quietly edited.
