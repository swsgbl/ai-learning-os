# M14-234: Restore preflight optional search-profile service (searxng)

## Scope

Narrow contract fix to `tools/ops/production_restore_preflight.py` (M14-231):
the live production rehearsal project now runs **seven** healthy containers —
the six required `--profile local` services plus the optional `--profile
search` service `searxng` (same compose project label). The M14-231 five-way
stack classification counted *every* project-labeled container against
`EXPECTED_SERVICES`, so the all-seven-healthy machine was misclassified
`stack-partial` → verdict `restore-required`, blocking honest production
acceptance. This slice makes the required verdict depend **only** on the
existing recovery six-service anchor set, reports known optional
compose-profile services separately, and keeps every fail-closed protection.

No change to `production_recovery.py`; no Docker mutation, no public-routing
change, no frpc/WSL/Harmony/Android/CC-Switch touch, no weakening of any
blocker.

## Contract delta (before → after)

| Input shape | M14-231 | M14-234 |
| --- | --- | --- |
| six required healthy + searxng healthy | `stack-partial` → `restore-required` (**misclassification**) | `stack-healthy` → `healthy` |
| six required healthy + searxng stopped/unhealthy | `stack-partial` → `restore-required` | `stack-healthy` → `healthy` + note `optional-service-not-healthy:searxng` |
| six required healthy + **unknown** extra service | `stack-partial` | `stack-partial` (fail-closed, unchanged) |
| required service missing / unhealthy | `stack-partial` / `stack-degraded` | identical (fail-closed, unchanged) |
| six required stopped (+ optional running) | `stack-partial` | `stack-stopped` (recovery path can bring up the required six; optional untouched by `--profile local` recovery) |
| only searxng present, required absent | `stack-partial` | `stack-partial` (unchanged — optional never masks an absent required stack) |

Mechanics:

- `classify_stack(service_states, expected, optional=frozenset())` — the
  five-way classification is computed **only** over the required anchor set
  `expected` (= `recovery.EXPECTED_STACK_SERVICES`, cross-locked as before).
  Known optional services (`OPTIONAL_PROFILE_SERVICES = frozenset({"searxng"})`,
  cross-locked against the compose `profiles: ["search"]` declaration) are
  excluded from the required-set judgment in any state. Services in neither
  set remain fail-closed `stack-partial`. With `optional` empty the function
  is branch-for-branch equivalent to the M14-231 judgment (unit-locked).
- Report: `containers.optional_services` (state per known optional service)
  and `containers.unknown_services` (extras that forced `stack-partial`) are
  new fields; log line now reads e.g.
  `containers: stack-healthy（必需在场 6/6；可选 searxng=running-healthy）`.
- A known optional service that is present but not healthy adds **note**
  `optional-service-not-healthy:<name>` (never a blocker, never a verdict
  flip).
- `SCHEMA` bumped `aios-production-restore-preflight/1` → `/2` (containers
  block gains `optional_services`/`unknown_services`; classification
  semantics narrowed to the required set).
- Read-only surface, blocker set, `--apply` guard chain, env/compose/image/
  volume/listener protections: all unchanged (source-contract tests still
  lock: no stop/rm/kill/down/restart/reset/pull/build literals; volume face
  is `ls`-only).

## Real Execution (read-only; no production action performed)

```
python tools/ops/production_restore_preflight.py \
  --env-file ../../ai-learning-os/infra/env.production-recovery \
  --output .verify/m14-234/machine-preflight.json
```

Run from the M14-234 worktree; the canonical recovery env pin file was
referenced **in place via a relative path** (never copied, values never
displayed). Authoritative result:

- **exit 0 / `verdict=healthy`**, `blockers=[]`, `notes=[]`;
- `containers.classification=stack-healthy`, seven service states all
  `running-healthy`, **required six 6/6**,
  `optional_services={'searxng': 'running-healthy'}`, `unknown_services=[]`;
- env pin present, 0 missing / 0 placeholder / 0 dev-default keys (values
  never displayed); compose config OK with zero service drift;
  images: 0 local / 0 registry missing; volumes: 0 persistent missing,
  0 cache missing, 0 classification unknown;
  listeners: `api 127.0.0.1:8000 open (/health=200)`, `web 127.0.0.1:3011 open`;
  public edge stays `uncertain` (out of local scope, as designed).
- Post-fix expectation confirmed exactly: healthy, no blockers, required six
  healthy, optional searxng reported.
- Report JSON asserted to contain no secrets and no absolute paths
  (`.verify/m14-234/check_report.py`, all contract assertions pass).
- **Nothing was mutated**: pre- and post-run read-only `docker ps -a` show
  the same seven containers with continuously increasing uptime
  (38 → 39 min, no restart); `docker volume ls` unchanged (all three named
  volumes present). The preflight only issued `docker version` /
  `docker volume ls` / `docker ps -a` / `docker image inspect` and client-side
  `docker compose config` renders plus loopback TCP/HTTP GET probes.
  `--apply` was not used; frpc and unrelated projects untouched.

## Verification

Interpreter: canonical repo venv `../../ai-learning-os/.venv`
(Python 3.11.15), the documented worktree pattern.

```
python -m pytest services/api/tests/test_production_restore_preflight.py -q
  → 47 passed (40 M14-231 + 7 M14-234 optional-service round)

python -m pytest services/api/tests/test_production_restore_preflight.py \
    services/api/tests/test_production_recovery.py \
    services/api/tests/test_production_preflight.py \
    services/api/tests/test_production_web_gateway.py \
    services/api/tests/test_public_edge_preflight.py \
    services/api/tests/test_searxng_egress_recovery.py \
    services/api/tests/test_ops_snapshot.py \
    services/api/tests/test_production_monitor.py \
    services/api/tests/test_rc_smoke_rehearsal.py \
    services/api/tests/test_soak_window_gate.py -q
  → 719 passed, 1 skipped
```

- `ruff check` both touched files — "All checks passed"; `compileall` clean;
  `git diff --check` clean; added-line scan (170 lines): zero secrets, zero
  private absolute paths, zero U+FFFD.
- New focused tests (M14-234 round): all-seven-healthy ⇒ healthy (the live
  misclassification shape); optional stopped/unhealthy/health-starting ⇒
  still healthy with explicit note; unknown extra service ⇒ still
  fail-closed partial; missing required and unhealthy required (with searxng
  present) ⇒ still partial/degraded; pure-function edges (searxng-only ⇒
  partial; six stopped + optional running ⇒ stopped; empty-optional
  equivalence with the M14-231 judgment); source contract locking
  `OPTIONAL_PROFILE_SERVICES={"searxng"}` to the compose
  `profiles: ["search"]` declaration (never `local`).
- Raw machine outputs kept in gitignored `.verify/m14-234/`
  (`machine-preflight.json`, `machine-preflight.log`, `check_report.py`);
  not committed.

## Honesty boundaries

- This slice fixes a classification contract; it does not perform or approve
  production acceptance by itself. Verdict `healthy` here means the **local
  restore preflight** contract is satisfied (required six healthy, listeners
  up, volumes/images/env in shape); the public edge remains `uncertain`
  until §9 `public_edge_preflight.py` + frpc status acceptance run
  independently.
- `--apply` semantics unchanged: on `healthy` it refuses with zero actions.
- Optional-service exemption is name-anchored and profile-cross-locked, not
  a blanket ignore of extras: any other project-labeled container still
  fail-closes as `stack-partial`.
