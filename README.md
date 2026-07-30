# GlassBox

An additive, explainable risk decisioning engine. Every score is the sum of the
signals shown next to it, every action names the rule that chose it, and both
properties are enforced in three independent layers.

Detection logic lives in **rows, not code**: a rule is a `rule_definitions` row
plus `rule_conditions` rows, and a feature is a `feature_catalog` row carrying
its own computation spec. The Python in `src/glassbox/` interprets those rows —
it does not encode any particular pattern.

---

## Quick start

```powershell
.\scripts\bootstrap.ps1
```

That starts Docker Desktop if needed, brings up PostgreSQL 16, installs
dependencies, generates fixtures, migrates and seeds, computes the feature
layer, runs both decisioning lanes, exports the published contract and runs the
acceptance suite. Roughly three minutes cold.

Step by step:

```bash
docker compose up -d                        # PostgreSQL 16 on :55432
cp .env.example .env
python -m pip install -r requirements.txt
python -m pip install -e .                  # so `python -m glassbox` resolves

python scripts/generate_synthetic.py        # fixtures/synthetic_data.sql
python scripts/reset_db.py                  # migrate + seed + load + features
python scripts/run_cycle.py --lane inline_sync
python scripts/run_cycle.py --lane async

psql "$GLASSBOX_DSN" -f db/acceptance/verify_scores.sql   # 87 / 68 / 64 / 58 / 31
pytest                                                    # 104 tests
python -m glassbox serve                                  # read API on :8000
```

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
| `TXN-48251` | T-021 | 31 | low | `allow` | Mitigating evidence keeps it out of the queue |

Alongside them sits a scored population: ~9,800 transactions, ~300 declines,
~120 refunds, a 22-decline card-testing burst, a refund-abuse customer and 22
labelled fraud clusters deliberately sized so most fall *below* R-114's line.

---

## How it fits together

```
db/migrations/  0001-0008  Week 1 schema, unchanged
                0011       feature computation specs + the resolution graph
                0012       decision detail: evaluation, veto, prevention, versions
                0013       version stores, action executions, clusters
                0014       bitemporal feature_values  (the one non-additive migration)
db/seeds/       0009-0010  the catalog and the four rules
                0015-0021  21 computable specs, 11 resolution edges, rule policy,
                           score bands, novelty baselines, driver filters
db/views/       v_alert_invariants.sql
db/acceptance/  verify_scores.sql          read-only; no hardcoded subject ids

src/glassbox/
  features/   predicate.py  the injection boundary
              aggregations.py  17 named reducers
              compiler.py   spec -> parameterised query
              runner.py     append-only, point-in-time-correct writes
  graph/      builder.py    clusters from the link layer
  engine/     resolver.py   subject -> entity, over a stored graph
              pit.py        the point-in-time read
              conditions.py fire / degrade
              scoring.py    per-rule score, before dedup
              consolidate.py one signal per (feature, direction)
              precedence.py veto -> authority -> severity -> prevention -> cap
              persist.py    one decision; 0-or-1 alerts
              evaluation.py the order, which is the design
  contract/   models.py     the frozen read contract
  api/        two read endpoints
```

The pipeline order, in one line each:

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
11. **Persist** — one decision per (subject, lane, evaluation); an alert iff a rule had authority.

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

**The read contract is frozen.** `contract/alert.v1.schema.json` is generated
from `src/glassbox/contract/models.py` and committed; a test regenerates it in
memory and asserts byte-equality. Never edit it by hand — a breaking change
becomes `alert.v2.schema.json` and v1 keeps being served.

---

## Extending it: what costs rows, and what costs code

`tests/test_extension_cardtesting.py` adds a complete card-testing detector with
two INSERTs and detects the planted burst end to end. A psycopg hook records
every statement executed in the test body and fails if any of them is DDL, so
that claim is checked rather than asserted.

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
pytest                       # 104 tests, ~35s including a full rebuild
pytest tests/test_degraded.py -v
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
| `test_contract.py`, `test_api.py` | §12 — the freeze, the invariants, the endpoints |
| `test_predicate_safety.py` | §3.1 — six injection shapes rejected, values always bound |
| `test_feature_runner.py` | §3.1 — every value the deleted generator code derived |
| `test_extension_cardtesting.py` | §14 — INSERT-only extension, DDL hook |
| `test_migrations.py` | the five-column key, no UPSERTs, ledger idempotence |

---

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `GLASSBOX_DSN` | `postgresql://glassbox:glassbox@localhost:55432/glassbox` | Dev database |
| `GLASSBOX_TEST_DSN` | …`/glassbox_test` | Dropped and recreated by the test session |
| `GLASSBOX_NOW` | `2026-01-15T15:00:00+00:00` | The fixtures' reference instant |

Port 55432 rather than 5432 so a locally-installed PostgreSQL does not collide.

---

## Known gaps

- `new_payee_then_drain` is `source_kind='sequence'`; the sequence runner is
  Week 3. It is the single value the generator still hand-seeds, in a labelled
  `HAND_SEEDED_FEATURES` block. S-077's other three features are computed for
  real.
- `ratio` is named by §3.1 but unused by any catalogued feature, so it is
  deliberately not implemented — a spec asking for it fails loudly at compile
  time rather than returning a number nobody defined.
- `action_executions` is a table with no machinery behind it; issuing and
  resolving actions is Week 3.
- Acceptance is checked against fixtures, not the population. §4 and §5 are
  *demonstrated*, not stress-tested; the card-testing pass is the partial
  substitute.
- `week1-data-model.md` is **superseded by the seed files** where the two
  disagree — catalog size, three `entity_type` values, and the price of
  `country_is_new_for_customer`. It is kept as a Week-1 artifact rather than
  quietly edited.
