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
| **AIOS volumes** (read-only `docker volume ls`) | **all absent**: `aios-m14-03-production-rehearsal_postgres-data`, `aios-m14-03-production-rehearsal_minio-data`, `aios-m14-03-production-rehearsal_searxng-cache` — machine has only an unrelated anonymous volume and `freellmapi_freellmapi-data` |
| `127.0.0.1:8000` / `:3012` | no listener (curl 000) |
| `infra/env.production-recovery` | present in canonical checkout (gitignored), **not** in this worktree |
| Docker engine | working |

So the real restore blockers are: stack containers absent, the pinned
self-built images (`aios/api:<env tag>`, `aios/web:<env tag>`,
`aios/minio:<RELEASE>`) absent, **and the persistent production data volumes
absent** — a naive `compose up` after merely rebuilding images would
**silently create empty `postgres-data`/`minio-data` volumes and present an
empty database as "restored production"**. The documented recovery path
(`production_recovery.py`) fail-closes on the missing images but has **no
volume guard**; the M14-231 preflight closes that gap (Codex review
correction round).

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
- **persistent data volumes** (Codex review correction round; read-only
  `docker volume ls`, never inferred from container state) — compose-declared
  named volumes classified as persistent (`postgres-data`, `minio-data`) vs
  recreatable cache (`searxng-cache`); full names follow the compose
  `<project>_<key>` underscore convention cross-locked against M14-41
  `minio_volume_adoption.VOLUME_NAME` and the M14-46 evidence volume
  `aios-m14-03-production-rehearsal_postgres-data`. Missing persistent volume
  ⇒ `persistent-volume-missing:<key>` blocker whose action explicitly says:
  **do not run compose up/recovery** (it would silently create an empty data
  volume); first restore data volumes from a verified backup or complete an
  explicit fresh-install decision with evidence. Missing cache volume ⇒ note.
  Volume query failure (`docker volume ls` or `compose config --volumes`)
  ⇒ `volume-query-failed` fail-closed (no volume conclusion at all); a
  compose-declared but unclassified volume, or a persistent key not declared
  by the profile render, ⇒ `volume-classification-unknown:<key>`
  fail-closed. Cache volume non-declaration under `--profile local` is
  expected (searxng is `--profile search`), not drift.
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

- Authoritative result (final correction-round run): **exit 1 /
  `verdict=blocked`**, blockers exactly `[local-image-missing,
  persistent-volume-missing:postgres-data, persistent-volume-missing:minio-data]`;
  env pin present with 0 missing / 0 placeholder / 0 dev-default keys (values
  never displayed); volumes read-only census: persistent missing
  `[postgres-data, minio-data]`, cache missing `[searxng-cache]` (note),
  classification unknown `[]`; containers `stack-absent (0/6)`; listeners
  `api 127.0.0.1:8000 closed`, `web 127.0.0.1:3011 closed` (web port
  correctly derived from env pin); public edge `uncertain`.
- Missing local anchors reported: `aios/minio:RELEASE.2025-10-15T17-29-55Z`,
  `aios/api:<env-tag>`, `aios/web:<env-tag>`; registry anchors also absent
  (note `registry-pull-required`).
- Second run with worktree-default env path (file absent) correctly blocked
  on `env-missing` — exit 1.
- **`--apply` was NOT executed on the machine** (preflight is blocked on
  missing images **and missing persistent volumes**; guarded mode would
  refuse with zero actions). No container was started, stopped, created, or
  removed; no volume created/deleted; frpc and uniterm-mysql untouched.
- Report JSON contains no secrets and no absolute paths (asserted).

## Codex review correction round

Production-safety gap found in review: the first M14-231 round reported only
`local-image-missing`; after an image rebuild it would have allowed
`restore-required` while `postgres-data`/`minio-data` were absent —
`compose up` would then silently create empty production data volumes.
Correction applied (same single M14-231 commit, amended):

1. Read-only volume census via `docker volume ls` (never inferred from
   container state) + `compose config --volumes` declaration check, with the
   repo-established `<project>_<key>` naming verified against M14-41
   (`minio_volume_adoption.VOLUME_NAME`) and M14-46 evidence.
2. Missing persistent volumes block (`persistent-volume-missing:<key>`); the
   recovery-command action is suppressed whenever any blocker is present, and
   the volume action explicitly forbids compose up/recovery until data
   volumes are restored from a verified backup or an explicit fresh-install
   decision is recorded. Cache-only absence stays a note.
3. Query failures fail closed (`volume-query-failed`, no volume conclusion);
   unclassified or profile-undeclared persistent volumes fail closed
   (`volume-classification-unknown:<key>`).
4. New focused offline tests (9): persistent-absent blocking + recovery-hint
   suppression, cache-only absence, volume-ls failure, declared-render
   failure, existing-volumes green path, unclassified/undeclared fail-closed
   pair, cache-under-local-profile expected, naming convention, source
   contract (only `docker volume ls`; never rm/prune/create), compose volume
   cross-lock.

## Operator next steps (documented, not auto-run)

1. **Data volumes first**: `postgres-data`/`minio-data` are absent — either
   restore them from a verified backup (existing Postgres/MinIO backup
   drill artifacts) or make an explicit fresh-install decision with evidence.
   Do **not** run compose up/recovery before that decision: it would create
   empty production data volumes.
2. Rebuild the three self-built images in a supervisor-approved window
   (docs/PUBLIC_EDGE_DEPLOYMENT.md §8) — this tool and the recovery path
   never build/pull on their own.
3. Re-run this preflight until `verdict=restore-required`.
4. `python tools/ops/production_recovery.py --dry-run`, then enforce (or the
   guarded `--apply` with the exact phrase).
5. Public edge stays unverified until §9 `public_edge_preflight.py` passes
   and frpc status is checked — local restore alone never proves public
   readiness (`public_ready` unchanged).

## Verification evidence

Interpreter: canonical repo venv `..\..\.venv` (Python 3.11.15), the
documented worktree pattern; focused suite also green on Python 3.12.13.

```
python -m pytest services/api/tests/test_production_restore_preflight.py -q
  → 40 passed (31 first round + 9 volume-correction round)

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
  → 712 passed, 1 skipped
```

- `python -m compileall -q tools/ops/production_restore_preflight.py
  services/api/tests/test_production_restore_preflight.py` — clean.
- `python -m ruff check` both files — "All checks passed".
- Test coverage highlights: five stack classifications; env missing/keys/
  placeholder/dev-default; compose invalid (redacted stderr) + services
  drift; local vs registry image split; **persistent vs cache volume
  classification, fail-closed volume queries, recovery-hint suppression on
  volume blockers, volume naming/cross-lock, no destructive volume
  commands**; port-conflict vs listener-missing cross-check; public-edge
  always-uncertain; secret markers never in logs/stdout/report; no absolute
  paths in report; apply confirm-gate / blocked / healthy refusals /
  dry-run-gate abort / argv forwarding; source-contract (no destructive
  subcommand literals; volume face is `ls`-only) and constant cross-lock
  against `production_recovery.py` + `infra/docker-compose.yml` +
  `minio_volume_adoption.py` + M14-46 evidence.
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
