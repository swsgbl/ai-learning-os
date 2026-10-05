# M14-235: Production state drift evidence (read-only pre-merge snapshot)

## Scope

Docs-only evidence slice. It records a **read-only** drift snapshot of the
production rehearsal machine, taken by the supervisor's rerun of
`tools/ops/production_restore_preflight.py` (no `--apply`, `apply=False`)
on the M14-234 feature worktree — **after the M14-234 feature commit
`f5eea42` and before PR #321 merged** — which found the machine drifted
back to a **blocked** Docker data-plane state, and anchors that snapshot as
a distinct evidence slice. It is **not** proof of the current post-merge
machine state: machine state at or after the merge is established only by a
fresh read-only preflight, never inferred from this snapshot. It does
**not** rewrite, weaken, or supersede the historical M14-234 healthy
evidence — both observations are true at their respective observation
times.

Nothing in this slice performed or triggered: any Docker mutation, a preflight
rerun, `docker pull`/`docker build`, `docker compose up` (or any compose
mutation), any volume create/delete, any production action, or any edit to
tracked evidence of older tasks. The change surface is exactly four Markdown
files (this README plus the three ledgers
`docs/PROJECT_STATUS.md` / `docs/ROADMAP.md` / `docs/CHANGELOG.md`); zero
code, tests, workflows, infra, Docker files, or `.gitignore` changes.

## Timeline (anchored, UTC)

| Event | Time (UTC) | Source |
| --- | --- | --- |
| M14-234 feature commit `f5eea421648d909aed010bfca6dd9d1283fc5905` | 2026-10-04T12:19:18Z | local git (committer date 2026-10-04 20:19:18 +08:00) |
| **This drift snapshot** (`generated_at`) | 2026-10-04T17:17:45Z | `machine-preflight.json` |
| PR #321 merged (`eac1e09`, parents `2ad62a4` + `f5eea42`) | 2026-10-05T03:29:01Z | authoritative GitHub `merged_at` (supervisor) |

The snapshot was therefore taken ≈4h58m after the feature commit and
≈10h11m before the PR merge — inside the PR review window, **before** the
merge. It says nothing about the machine state at or after merge time.

## Source artifacts (read-only, gitignored, not copied into the repo)

Produced by the supervisor's read-only rerun (`apply=False`) of
`tools/ops/production_restore_preflight.py` during the PR #321 review
window on the M14-234 feature worktree; kept read-only under
`.verify/m14-234-supervisor/` inside the
`ai-learning-os-worktrees/m14-234-restore-preflight-optional-search`
worktree:

| Artifact | SHA-256 | Bytes |
| --- | --- | ---: |
| `machine-preflight.json` | `5d59a343683a2bb24b95441746aa41881ccc35ca5333eca9868358bf14a6e91e` | 3142 |
| `machine-preflight.log` | `96b5dec1d08704020be0d70ff4f898ba73aa936f5fae8f8eab2aa555ce1e9b85` | 1607 |

Both hashes and byte sizes were recomputed at slice time and matched the
archived artifacts; every fact quoted below was verified against the source
JSON and the source log.

## Snapshot facts (sanitized; verified against both artifacts)

- Identity: `generated_at=2026-10-04T17:17:45+00:00`, `schema=
  aios-production-restore-preflight/2`, `tool=production_restore_preflight`,
  run read-only (`apply=False`); inputs project
  `aios-m14-03-production-rehearsal`, profile `local`, compose file
  `docker-compose.yml`, env pin `env.production-recovery` (key-shape checks
  only — values never displayed anywhere).
- Docker engine: available, server version **29.8.1**.
- Compose config: `config_ok=true`, zero service drift (declared set equals
  the expected six: api/livekit/minio/postgres/redis/web).
- Containers: classification **`stack-absent`** — required in-place **0/6**;
  `optional_services` **empty** (`{}`); `unknown_services` empty.
- Listeners: api `127.0.0.1:8000` **closed** (`tcp_open=false`, health not
  probed — `/health=None`); web `127.0.0.1:3011` **closed**; loopback-only
  bind.
- Images: local missing `aios/minio:RELEASE.2025-10-15T17-29-55Z`,
  `aios/api:m14-211-production`, `aios/web:m14-193-production`; registry
  anchors `postgres:17-alpine` / `redis:7-alpine` /
  `livekit/livekit-server:latest` also absent (note
  `registry-pull-required`, not a blocker).
- Volumes: query OK; persistent missing **`postgres-data`** and
  **`minio-data`** (both declared in compose); cache volume
  `searxng-cache` missing (note `cache-volume-missing`).
- env pin: present; 0 missing / 0 placeholder / 0 dev-default keys.
- Public edge: `public-edge-uncertain` (fixed boundary — the local restore
  preflight never validates the public `https://…/aios` chain; that stays
  with §9 `public_edge_preflight.py` + frpc status acceptance).
- Verdict: **`blocked`** with blockers `local-image-missing`,
  `persistent-volume-missing:postgres-data`,
  `persistent-volume-missing:minio-data`.

## Drift snapshot vs historical M14-234 healthy evidence

| Dimension | M14-234 healthy rerun (historical, unchanged) | M14-235 drift snapshot (pre-merge) |
| --- | --- | --- |
| verdict | `healthy`, blockers `[]` | `blocked`, 3 blockers |
| containers | required 6/6 running-healthy (+ optional searxng running-healthy) | `stack-absent`, required 0/6, optional empty |
| api 8000 / web 3011 | open (`/health=200`) / open | closed / closed |
| local images | all present | 3 missing (minio / api / web anchors) |
| persistent volumes | present | `postgres-data` + `minio-data` missing |

M14-234's evidence
(`docs/evidence/m14-234-restore-preflight-optional-search/README.md`, raw
outputs in gitignored `.verify/m14-234/`) remains valid for its observation
time: it verified the *classification contract fix* during M14-234 slice
verification, and its healthy verdict was true then — that evidence was
committed with feature commit `f5eea42` (≤ 2026-10-04T12:19:18Z). This
drift snapshot postdates `f5eea42` and predates the PR merge, and shows
the *external machine* had changed in between: the stack was gone, the
pinned local images were gone, and the persistent data volumes were gone.
This is Docker/data-plane state outside the repository, **not a code
regression** of M14-234 or of any merged slice: the same preflight tool,
run read-only, honestly reports the state it finds. And because the
snapshot predates the merge, it equally is **not** evidence about the
post-merge machine — whether the data plane is still in this blocked shape
at any later time must be established by a fresh read-only preflight.

## What did NOT happen (this slice)

- No Docker mutation of any kind: no container start/stop/rm/kill/restart,
  no pull, no build, no `compose up`/`down`, no volume create/delete/prune.
- No preflight rerun: the source artifacts were only read and hashed.
- No production action; no frpc / WSL / public-edge touch; `--apply` never
  used.
- No edit to tracked evidence of older tasks; the M14-234 healthy evidence
  and its raw artifacts are untouched.
- No code, test, workflow, infra, Docker file, or `.gitignore` change.

## Boundary and next-task guidance

This slice changes no executable behavior; `production_ready=false` is
unchanged. The snapshot is a **pre-merge** observation: it makes no claim
about the current post-merge machine state, and none may be inferred from
it. Production restoration stays **blocked** until:

1. the external Docker data-plane recovery finishes — restore
   `postgres-data`/`minio-data` from validated backups (or complete an
   explicit fresh-install decision with evidence; never a silent
   `compose up` that would create empty volumes), and rebuild the pinned
   local images in an approved window (the preflight and the recovery path
   never pull/build), **and**
2. a fresh read-only preflight reports `healthy`.

Until both hold, no `compose up`, no production restore, and no acceptance
claim may proceed; machine state at any later time must be re-established
by a new read-only run, not read off this snapshot.

## Slice validation (docs-only)

- SHA-256 and byte size of both source artifacts recomputed and recorded
  above; all quoted facts cross-checked against the source JSON and log.
- Timeline anchors cross-checked in local git (`f5eea42` committer date,
  `eac1e09` parents) against the authoritative PR #321 `merged_at`
  (2026-10-05T03:29:01Z) and the artifact `generated_at`
  (2026-10-04T17:17:45Z).
- `git diff --check` clean; added-line scan: zero secrets/tokens/passwords,
  zero absolute machine paths, zero U+FFFD.
- No pytest required (docs-only); only harmless read-only commands were run.
