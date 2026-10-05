# M14-236: Current production preflight evidence (read-only post-merge snapshot)

## Scope

Docs-only evidence slice. It records a **read-only** production restore
preflight snapshot of the rehearsal machine, taken by the supervisor's
fresh rerun of `tools/ops/production_restore_preflight.py`
(no `--apply`, `apply=False`) in the canonical checkout **after PR #322
merged into main**, and anchors that snapshot as evidence of the
**current post-merge machine state — but only as of its `generated_at`
(2026-10-05T04:15:49Z)**. It is the machine-state baseline the
supervisor's post-merge review needed; it **does not authorize
recovery, does not declare production readiness, and does not perform
any step of recovery**. It keeps M14-235
(`docs/evidence/m14-235-production-state-drift/README.md`) intact as
the distinct **pre-merge** drift snapshot; older evidence is never
rewritten, weakened, or superseded — each observation is true at its
own observation time.

Nothing in this slice performed or triggered: any Docker mutation, a
preflight rerun, `docker pull`/`docker build`, `docker compose up` (or
any compose mutation), any volume create/delete, any production
action, any frpc/WSL/public-edge touch, or any edit to tracked
evidence of older tasks. The change surface is exactly four Markdown
files (this README plus the three ledgers
`docs/PROJECT_STATUS.md` / `docs/ROADMAP.md` / `docs/CHANGELOG.md`);
zero code, tests, workflows, infra, Docker files, or `.gitignore`
changes.

## Timeline (anchored, UTC)

| Event | Time (UTC) | Source |
| --- | --- | --- |
| PR #322 merged — `M14-235: record production state drift evidence (#322)`, merge commit `7ab13940bede10a3971ee07a3ca2b878d5b5249c` (parents `eac1e09e22cb1e4c14fd54deb6c4f1a7e812f8bb` + `72c424d0a893e9df3af39dc1a75d131850ce7e2a`) | 2026-10-05T04:03:20Z | authoritative GitHub `merged_at` (supervisor); local merge-commit committer time 2026-10-05T04:03:19Z cross-checked in git |
| **This preflight run** (`generated_at`) | 2026-10-05T04:15:49Z | `machine-preflight.json` |

The run therefore postdates the PR #322 merge by ≈12m29s. It is
**current post-merge evidence only as of `generated_at`
(2026-10-05T04:15:49Z)**: it proves the machine was in the recorded
blocked state at that instant, and nothing about any later instant —
machine state at any later time must be re-established by a fresh
read-only preflight, never inferred from this slice.

## Source artifacts (read-only, gitignored, not copied into the repo)

Produced by the supervisor's read-only rerun (`apply=False`) of
`tools/ops/production_restore_preflight.py` in the canonical checkout;
kept read-only under gitignored
`.verify/m14-236-current-machine-preflight/` (`.gitignore` covers
`.verify/`; nothing under it is tracked or copied into the repo):

| Artifact | SHA-256 | Bytes |
| --- | --- | ---: |
| `machine-preflight.json` | `4f3f4be1c39750c1e44b31c0d525e04638e7d94da9b5c9bf368c516eccd15113` | 3142 |
| `machine-preflight.log` | `96b5dec1d08704020be0d70ff4f898ba73aa936f5fae8f8eab2aa555ce1e9b85` | 1607 |

Both hashes and byte sizes were independently recomputed at slice time
and matched the artifacts; every fact quoted below was cross-checked
against the source JSON and the source log.

## Preflight facts (sanitized; verified against both artifacts)

- Identity: `generated_at=2026-10-05T04:15:49+00:00`, `schema=
  aios-production-restore-preflight/2`, `tool=production_restore_preflight`,
  run read-only (`apply=False`); inputs project
  `aios-m14-03-production-rehearsal`, profile `local`, compose file
  `docker-compose.yml`, env pin `env.production-recovery` (key-shape
  checks only — values never displayed anywhere).
- Docker engine: available, server version **29.8.1**.
- Compose config: `config_ok=true`, zero service drift (declared set
  equals the expected six: api/livekit/minio/postgres/redis/web).
- Containers: classification **`stack-absent`** — required in-place
  **0/6**; `optional_services` **empty** (`{}`); `unknown_services`
  empty.
- Listeners: api `127.0.0.1:8000` **closed** (`tcp_open=false`, health
  not probed — `/health=None`); web `127.0.0.1:3011` **closed**;
  loopback-only bind.
- Images: local missing `aios/minio:RELEASE.2025-10-15T17-29-55Z`,
  `aios/api:m14-211-production`, `aios/web:m14-193-production`; registry
  anchors `postgres:17-alpine` / `redis:7-alpine` /
  `livekit/livekit-server:latest` also absent (note
  `registry-pull-required`, not a blocker).
- Volumes: query OK; persistent missing **`postgres-data`** and
  **`minio-data`** (both declared in compose); cache volume
  `searxng-cache` missing (note `cache-volume-missing`).
- env pin: present; 0 missing / 0 placeholder / 0 dev-default keys.
- Public edge: `public-edge-uncertain` (fixed boundary — the local
  restore preflight never validates the public `https://…/aios` chain;
  that stays with §9 `public_edge_preflight.py` + frpc status
  acceptance).
- Verdict: **`blocked`** with blockers `local-image-missing`,
  `persistent-volume-missing:postgres-data`,
  `persistent-volume-missing:minio-data`.

## Comparison to M14-235 (pre-merge drift snapshot)

| Dimension | M14-235 (pre-merge, distinct snapshot — unchanged) | M14-236 (this slice, post-merge) |
| --- | --- | --- |
| observation time (`generated_at`) | 2026-10-04T17:17:45Z (inside the PR #321 review window, before that merge) | 2026-10-05T04:15:49Z (≈12m29s after the PR #322 merge) |
| run identity | same tool, same schema `/2`, same inputs, read-only `apply=False` | same |
| verdict | `blocked`, same 3 blockers | `blocked`, same 3 blockers |
| containers / listeners / images / volumes | stack-absent 0/6; 8000 + 3011 closed; 3 local images missing; postgres-data + minio-data missing; searxng-cache missing | identical on every dimension |

The two snapshots were diffed field-by-field at slice time (read-only;
the M14-235 artifacts were only re-hashed, never modified): the log
artifacts are **byte-identical** (same SHA-256
`96b5dec1d08704020be0d70ff4f898ba73aa936f5fae8f8eab2aa555ce1e9b85`,
1607 bytes), and the JSON artifacts (both 3142 bytes; M14-235 archive
SHA-256 `5d59a343683a2bb24b95441746aa41881ccc35ca5333eca9868358bf14a6e91e`
vs this slice's `4f3f4be1c39750c1e44b31c0d525e04638e7d94da9b5c9bf368c516eccd15113`)
differ in exactly **one** field:
`generated_at`. In other words, every observed machine fact is
unchanged across the ≈11h window that spans both the PR #321 and
PR #322 merges: the blocked Docker data-plane state **persisted**
through both merges. Consistency is expected — neither merge touched
the Docker data plane — but persistence is still only an observation
about the interval up to 2026-10-05T04:15:49Z, not a guarantee about
any later time. M14-235 remains the distinct pre-merge snapshot; this
slice does not rewrite, weaken, or supersede it.

## What did NOT happen (this slice)

- No Docker mutation of any kind: no container start/stop/rm/kill/
  restart, no pull, no build, no `compose up`/`down`, no volume
  create/delete/prune.
- No preflight rerun: the source artifacts were only read and hashed
  (plus the read-only field diff and hash re-check of the M14-235
  archives described above; no file there was modified).
- No production action; no frpc / WSL / public-edge touch; `--apply`
  never used.
- No edit to tracked evidence of older tasks; M14-234 healthy
  evidence and M14-235 pre-merge snapshot evidence are untouched.
- No code, test, workflow, infra, Docker file, or `.gitignore` change.

## Boundary and next-task guidance

This slice changes no executable behavior; `production_ready=false` is
unchanged. The evidence proves exactly one thing: the machine was in
the recorded **blocked** state at 2026-10-05T04:15:49Z. It does **not**
authorize recovery, does not constitute recovery progress, and does
not establish the machine state at any later time. Production
restoration stays **blocked** until:

1. the external Docker data-plane recovery finishes — restore
   `postgres-data`/`minio-data` from validated backups (or complete an
   explicit fresh-install decision with evidence; never a silent
   `compose up` that would create empty volumes), and rebuild the
   pinned local images in an approved window (the preflight and the
   recovery path never pull/build), **and**
2. a fresh read-only preflight reports `healthy`.

Until both hold, no `compose up`, no production restore, and no
acceptance claim may proceed; machine state at any later time must be
re-established by a new read-only run, not read off this snapshot.

## Slice validation (docs-only)

- SHA-256 and byte size of both source artifacts independently
  recomputed and recorded above; all quoted facts cross-checked
  against the source JSON and log.
- Timeline anchors cross-checked in local git (merge commit
  `7ab1394` parents and committer time) against the authoritative
  PR #322 `merged_at` (2026-10-05T04:03:20Z) and the artifact
  `generated_at` (2026-10-05T04:15:49Z).
- M14-235 comparison grounded in a read-only re-hash (both archived
  artifacts matched their recorded digests) and a field-by-field JSON
  diff (single differing field: `generated_at`).
- `git diff --check` clean; added-line scan: zero secrets/tokens/
  passwords, zero absolute machine paths, zero U+FFFD.
- No pytest required (docs-only); only harmless read-only commands
  were run.
