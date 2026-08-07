# GlassBox console

The analyst and admin surfaces, in a browser. It binds to the published
contracts and **asserts nothing they do not say**.

```bash
docker compose --profile console up -d console          # :5173, proxying /api -> :8000
docker compose --profile console run --rm console npm test    # 40 tests
docker compose --profile console run --rm console npm run build   # -> dist/, served at /console
```

Node is not installed on the host, and nothing here asks you to install it. The
packages `package-lock.json` pins live in the image and in a named volume; the
only thing on your disk is this directory and the `dist/` a build writes into
it. Run the same commands without Docker (`npm install && npm run dev`) if you
would rather — everything below holds either way.

You need a service to talk to, and **which interface it binds to depends on
where the console is**:

```bash
python -m glassbox serve                        # :8000 on loopback — console on the host
GLASSBOX_HOST=0.0.0.0 python -m glassbox serve  # console in the container
```

The second is not optional for the containerised console and it is the one
thing that will not announce itself: the container reaches this process over the
Docker bridge as `host.docker.internal`, and a socket bound to `127.0.0.1`
refuses that connection — so every request fails exactly as if the service were
down. `.env.example` carries the pairing (`GLASSBOX_HOST` and `GLASSBOX_API`,
which are meaningless apart). Loopback stays the default because this process
commits to `GLASSBOX_DSN` and `POST /authorize` can decline a charge.

Sign in with `analyst-token` or `admin-token`. Reads are open, so the queue, a
case, and the KPI tiles all render before you do.

---

## What the container arrangement decides, and why

**`node_modules` is a named volume, never a host bind.** The source is
bind-mounted so an edit is the thing Vite serves and so `npm run build` writes
`dist/` where `_mount_console` looks for it — on the host, at
`:8000/console`. `node_modules` is carved back out of that mount, because
installing Linux binaries into a Windows working tree is the failure the whole
exercise exists to avoid.

**The entrypoint reconciles that volume against the lockfile.** Docker seeds a
named volume from the image the first time it is used and never again, so a
rebuilt image whose `package-lock.json` had moved would be silently shadowed by
the previous dependencies — and the first symptom would be a test failing for a
reason that is not in the diff. `docker-entrypoint.sh` compares an md5 stamp
written at build time and runs `npm ci` when the two disagree.

**The service sits behind a compose profile.** `bootstrap.ps1` runs a bare
`docker compose up -d` and promises a demoable database in three minutes; a
console service that joined that command would spend the first of those minutes
building a Node image the script does not need. Nothing starts unless it is
named.

**`VITE_POLL=1` in the compose environment.** A Windows bind mount does not
deliver inotify events into a Linux container. The watcher registers, nothing
ever fires, and hot reload stops being hot without one error to say so. Polling
is the only thing that sees those edits and it costs idle CPU, so it is opt-in
rather than baked into `vite.config.ts`.

---

## Set `GLASSBOX_CYCLE_SECONDS` deliberately

`glassbox serve` runs the background cycle every 30 seconds by default and
commits to whatever `GLASSBOX_DSN` points at. That is correct for a demo and
confusing while building a queue screen, because rows appear that no click
caused.

```bash
GLASSBOX_CYCLE_SECONDS=0 python -m glassbox serve    # nothing ticks
GLASSBOX_CYCLE_SECONDS=30 python -m glassbox serve   # the demo
```

Either is fine. Inheriting one is not — and the console will tell you which you
picked, because the system strip reads it off `GET /cycle` rather than assuming.

---

## Where the types come from

```
src/glassbox/api/app.py
  └─ scripts/export_openapi.py  ->  console/src/api/openapi.json   (committed)
       └─ npm run types         ->  console/src/api/schema.d.ts    (committed)
            └─ src/api/types.ts      aliases, and nothing else
```

Both generated files are committed and `tests/test_openapi.py` fails if the
document is stale, so a route whose `response_model` changed cannot leave the
console typechecking green against a shape the service no longer serves.

**This is D6's answer.** The nine files under `contract/` are the frozen,
published artifacts and they contain dangling `$ref`s — the exporter's
`ref_template` is document-root-absolute while Pydantic nests `$defs` per model.
Generating a client from them is the moment that stops being free. Rather than
move `alert.v1`'s pinned digest to fix a `$ref` template, or spend a version
number on `alert.v2`, the client is generated from the OpenAPI document, which
FastAPI builds with every model hoisted into `components.schemas` and every
reference resolvable. `scripts/export_openapi.py` carries the full reasoning and
`test_openapi.py` asserts the resolvability rather than assuming it.

D6 stays **open**: nothing here fixes the published schemas, it routes around
them.

---

## The properties this console holds itself to

Each one is a test in `src/console.test.ts` or beside the component, because the
project's habit is to enforce a claim mechanically rather than assert it in prose.

**One score bar, three payloads.** `alert.v1`, `simulation.v1` and `ingest.v1`
carry `Signal`, `Action` and `Evidence` unmodified and deliberately, so one
component renders a stored alert, a what-if and a live authorization. A second
bar implementation is the failure mode `persist.ranked_signals` was made public
to prevent, moved up a layer — so a test asserts `bar-seg` appears in exactly one
file.

**The bar adds up exactly.** Pydantic sends `Decimal` as a *string*, which is
what lets the server check `sum(signals) == score` with `==` and no tolerance.
Parsing those into floats to add them would hand that back, so `format.ts` sums
them as scaled integers. When a payload does not add up the bar says so instead
of rendering it quietly — unreachable through the API, and rendered anyway,
because the one surface worth restating an invariant on is the one whose whole
proposition is that it holds.

**`persisted` decides the frame.** A simulated decision gets a dashed, hatched
frame and a header saying nothing was written; a stopped charge gets a frame of
its own again. The frame comes from the payload's own field, not from which
screen is rendering — `/simulate/*` publishes `persisted: false` on the wire
precisely so a caller does not infer it from the URL. A rolled-back decision
that *would* have declined a charge never reads as stopped, because `persisted`
wins in the direction that claims less.

**No tile copy is written in the UI.** Every label, basis, caveat, numerator,
denominator and window comes off `kpis.v1`. A delta renders only when
`delta_pct` is non-null, and it names the baseline window that produced it; when
there is no baseline the payload's own `baseline_absent_reason` is printed. §11
flags two console strings that outran the system — neither was ever written
here, and a source scan keeps it that way.

**Nothing asserts liveness.** Whether the engine is running comes from
`GET /cycle` and nowhere else. There are three answers and the third is
load-bearing: running, not running, and *we could not ask* — `/cycle` needs a
token, so a signed-out console genuinely does not know and says so rather than
defaulting to either guess. A test asserts no file hardcodes a running scheduler
and that only the strip renders a status dot.

**The queue does not move under the pointer.** The queue is ordered by
`score × exposure × recency`, so an arriving case does not append — it *inserts*,
and every row below it shifts. The console polls `/cycle`, and when the watermark
moves it fetches the new queue into a holding area and offers a button. Nothing
on screen changes until it is clicked, because reordering a list under a pointer
is how somebody dispositions the case they were not reading, and a disposition is
append-only.

**The endpoint that commits has one call site.** `POST /authorize` writes to raw
capture and can decline a charge. `POST /simulate/transaction` answers a
hypothetical and rolls back. They are separate endpoints so that a typo cannot
turn one into the other, and a test asserts `api.authorize(` appears in exactly
one screen — the one that says what it is going to do before it does it.

**Payload shapes are never redeclared.** Every export in `src/api/types.ts` is an
alias into the generated schema. Exactly one shape is hand-written —
`CycleState`, because `GET /cycle` has no `response_model` and OpenAPI can only
type it as an open object — and a test pins that it stays the only one.

---

## What this does not do

- **No error boundary.** A thrown render fails to the browser console. Adding
  one that swallowed a `ContractViolation` would hide the single failure this
  system most wants to be loud.
- **No pagination.** `GET /queue` caps at 50 and the console shows what it is
  given. On seven alerts that is not a limitation; on real volume it is.
- **No optimistic writes.** A disposition is posted and the verdict re-read.
  Rendering the write before the server accepted it would mean the screen and
  the record disagree for as long as the request takes, and the record is the
  product.
- **Nothing answers a step-up.** The demo shows a charge being stopped and never
  one being released, because the service has no endpoint for "the customer
  passed" — `resolve_actions.py` settles challenges against planted ground truth.
- **It is not responsive below ~700px.** Tables scroll horizontally inside their
  own containers; the layout is built for a desk.
