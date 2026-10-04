# M14-231: Production restore preflight and guarded execution plan

## Scope

Slice adds a read-only, fail-closed **restore preflight** plus a guarded
**--apply** entry point (`tools/ops/production_restore_preflight.py`, M14-231)
so an operator can restore the existing local production stack
(compose project `aios-m14-03-production-rehearsal`, `--profile local`, six
services) safely. Tool + offline tests only. No public-routing redesign; no
Docker volume/container mutation, no factory reset, no frpc restart, no
touching of unrelated projects (uniterm-mysql etc.) was performed by this
slice. The tool reuses `production_recovery.py` constants/Runner/redaction
patterns via importlib — no new framework.

## Machine facts at slice time (read-only probes only)

Task context suspected "AIOS compose stack stopped" as the primary issue.
Read-only verification (docker ps -a / docker compose ls / docker image
inspect / netstat / curl) refined that:

| Fact | Value |
| --- | --- |
| `docker ps -a` | only `uniterm-mysql` (unrelated, untouched) — **no AIOS containers exist at all** (not merely stopped) |
| `docker compose ls` | no compose projects |
| AIOS images | **all absent**: `aios/api:*`, `aios/web:*`, `aios/minio:*`, postgres/redis/livekit registry images |
| `127.0.0.1:8000` / `:3012` | no listener (curl 000) |
| `infra/env.production-recovery` | present in canonical checkout (gitignored), **not** in this worktree |
| Docker engine | working |

So the real restore blockers are: stack containers absent **and** the pinned
self-built images (`aios/api:<env tag>`, `aios/web:<env tag>`,
`aios/minio:<RELEASE>`) absent — the documented recovery path
(`production_recovery.py`, `up -d --no-build`) fail-closes on exactly this
(`minio-local-image-missing`), and rebuilding images is a supervisor-approved
window action this tool correctly refuses to do itself.

## Tool contract (fail-closed)

Read-only preflight classifies, without ever printing env values / secrets /
raw credentials / local absolute paths:

- **env pin shape** — presence, the nine `PIN_KEYS` (shared with
  `production_recovery`), template placeholders, compose dev-default secret
  fallback values. Key names only.
- **compose validity** — `config --quiet` + rendered `--services` vs the
  profile-local six-service anchor (drift = blocker). stderr excerpt is
  defensively redacted against env secret values.
- **images** — env-tag-derived `aios/api` / `aios/web` + `aios/minio` local
  anchors missing ⇒ `local-image-missing` blocker (tool never pulls/builds);
  registry anchors (postgres/redis/livekit) missing ⇒ note only (compose up
  can pull).
- **container state** — `docker ps -a` filtered by compose project label ⇒
  `stack-absent` / `stack-stopped` / `stack-partial` / `stack-degraded` /
  `stack-healthy`.
- **API/Web local listeners** — TCP probe + loopback `GET /health`: running
  container with closed port ⇒ `listener-missing:<svc>` (web action points at
  `production_web_gateway.py`, the M14-157 stale-mapping form); non-running
  service with an open port ⇒ `port-conflict:<svc>` (human adjudication; the
  tool never kills processes).
- **public edge** — always `uncertain`, never a local blocker: local restore
  cannot verify the public `https://…/aios` chain; action points at §9
  `public_edge_preflight.py` + `frpc_windows_controller.py status`. The tool
  never restarts or touches frpc.

Verdicts: `blocked` (exit 1) / `restore-required` (exit 0) / `healthy`
(exit 0).

`--apply` is guarded delegation to the existing documented recovery path only:
requires verdict `restore-required` **and** the exact phrase
`--confirm-phrase "APPLY PRODUCTION RESTORE"`; executes
`production_recovery.py --dry-run` first (non-zero aborts before enforce),
then enforce. The tool itself constructs no docker up/stop/rm/kill/down/
restart/pull/build argv (source-contract tested).

## Real Execution (read-only; no production action performed)

```
python tools/ops/production_restore_preflight.py \
  --env-file <canonical>/infra/env.production-recovery \
  --output .verify/m14-231/machine-preflight.json
```

- Authoritative result: **exit 1 / `verdict=blocked`**, blockers exactly
  `[local-image-missing]`; env pin present with 0 missing / 0 placeholder /
  0 dev-default keys (values never displayed); containers
  `stack-absent (0/6)`; listeners `api 127.0.0.1:8000 closed`,
  `web 127.0.0.1:3011 closed` (web port correctly derived from env pin);
  public edge `uncertain`.
- Missing local anchors reported: `aios/minio:RELEASE.2025-10-15T17-29-55Z`,
  `aios/api:<env-tag>`, `aios/web:<env-tag>`; registry anchors also absent
  (note `registry-pull-required`).
- Second run with worktree-default env path (file absent) correctly blocked
  on `env-missing` — exit 1.
- **`--apply` was NOT executed on the machine** (preflight is blocked on
  missing images; guarded mode would refuse with zero actions). No container
  was started, stopped, created, or removed; no volume touched; frpc and
  uniterm-mysql untouched.
- Report JSON contains no secrets and no absolute paths (asserted).

## Operator next steps (documented, not auto-run)

1. Rebuild the three self-built images in a supervisor-approved window
   (docs/PUBLIC_EDGE_DEPLOYMENT.md §8; `docker compose … up -d --build` shape
   or equivalent explicit builds) — this tool and the recovery path never
   build/pull on their own.
2. Re-run this preflight until `verdict=restore-required`.
3. `python tools/ops/production_recovery.py --dry-run`, then enforce (or the
   guarded `--apply` with the exact phrase).
4. Public edge stays unverified until §9 `public_edge_preflight.py` passes
   and frpc status is checked — local restore alone never proves public
   readiness (`public_ready` unchanged).

## Verification evidence

Interpreter: canonical repo venv `..\..\.venv` (Python 3.11.15), the
documented worktree pattern; focused suite also green on Python 3.12.13.

```
python -m pytest services/api/tests/test_production_restore_preflight.py -q
  → 31 passed

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
  → 703 passed, 1 skipped
```

- `python -m compileall -q tools/ops/production_restore_preflight.py
  services/api/tests/test_production_restore_preflight.py` — clean.
- `python -m ruff check` both files — "All checks passed".
- Test coverage highlights: five stack classifications; env missing/keys/
  placeholder/dev-default; compose invalid (redacted stderr) + services
  drift; local vs registry image split; port-conflict vs listener-missing
  cross-check; public-edge always-uncertain; secret markers never in logs/
  stdout/report; no absolute paths in report; apply confirm-gate / blocked /
  healthy refusals / dry-run-gate abort / argv forwarding; source-contract
  (no destructive subcommand literals) and constant cross-lock against
  `production_recovery.py` + `infra/docker-compose.yml`.
- Raw machine outputs kept in gitignored `.verify/m14-231/`
  (`machine-preflight.json`, `machine-preflight.log`); not committed.

## Honesty boundaries

- This slice ships a preflight/plan capability; it does **not** restore the
  stack (images are absent — rebuild is a supervisor-approved action), does
  not redesign public routing, and does not claim public readiness. Public
  edge remains `uncertain` until §9 acceptance + manual checklist pass.
- The guarded `--apply` is only as safe as the documented recovery path it
  delegates to; that path's own fail-closed semantics (pin check, image
  preflight, never stop/rm/down/restart/pull/build) remain the authority.
