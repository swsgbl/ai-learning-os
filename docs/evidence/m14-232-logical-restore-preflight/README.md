# M14-232: Logical restore preflight (read-only reconstruction decision)

## Scope

Slice adds one narrowly scoped, read-only CLI
(`tools/ops/logical_restore_preflight.py`, M14-232) that decides whether the
missing AIOS persistent volumes can be **safely reconstructed** from the
verified M14-193 logical backup, and outputs a staged runbook it never
executes. Tool + offline tests only. **No `--execute` mode exists; no Docker
mutation was performed by this slice** — no container started/stopped/created/
removed, no volume created/deleted, no image pulled/built, frpc and unrelated
projects untouched. The tool reuses M14-231 `production_restore_preflight.py`
constants/probes via importlib (which itself reuses
`production_recovery.py`) — no new framework, no third-party dependency.

## Backup under test (M14-193 pre-cutover backup)

`artifacts/m14-193-production-cutover/pre-cutover-backup` in the canonical
main checkout (gitignored `artifacts/` location; contains production
env/config — **no values, raw bodies, or credential material from it ever
enter this README, the tool output, logs, or the commit**):

| Fact | Value |
| --- | --- |
| schema | `aios-backup-v1` (exact match enforced) |
| tables | 30 (exact set anchored in the tool) |
| rows | 41 declared = 41 actual (per-table reconciliation) |
| files | 4 declared = 4 actual, per-file SHA256 all match |
| manifest SHA256 anchor | `381C49875C23F4CF35F49CED864522A200D6B46614C72D42B52DFB34EB0612EC` — recomputed, matches (cross-locked with the M14-193 evidence literal) |

## Tool contract (fail-closed)

**Backup validation** — any drift blocks with `backup-invalid` plus detail
codes: approved-location check (only gitignored `artifacts/`/`temp/` of this
checkout **and** the canonical main checkout — canonical root derived at
runtime from the worktree `.git` gitdir pointer, zero hardcoded absolute
paths in source; unapproved locations fail fast without even walking the
tree); symlink/junction (reparse-point) rejection for the backup root, its
ancestors within the tree, and every nested entry; exact schema
`aios-backup-v1`; exact manifest SHA256 anchor; exact 30-table set/count
(declared and in `database.json`); exact 4-file set/count (undeclared extra
files and missing declared files both block); declared-vs-actual per-table
row counts and the 41-row total; per-file SHA256 recomputed byte-wise.
env/compose backup files are **hash-only** — never decoded, so values cannot
leak; reports contain only table names, file names, counts, sizes, and
SHA256 hashes.

**Read-only Docker semantics** (three read-only commands only —
`docker version`, `docker volume ls`, `docker image inspect`; engine/volume
probes delegated to the M14-231 probe functions):

- Docker unavailable or **any** query failure (including image-inspect
  transport errors) ⇒ `docker-state-unreadable`, no volume/image conclusions
  at all;
- either persistent volume present
  (`aios-m14-03-production-rehearsal_postgres-data` /
  `…_minio-data`, `<project>_<key>` naming cross-locked with M14-231/
  M14-41/M14-46) ⇒ `existing-data-must-not-be-overwritten`;
  `searxng-cache` is recreatable — absence is a note only;
- required images = the exact `aios/minio:RELEASE.2025-10-15T17-29-55Z`
  self-built anchor (from `production_recovery.LOCAL_BUILD_IMAGE_REFS`) plus
  `postgres:17-alpine` (cross-locked with M14-231 anchors and
  `infra/docker-compose.yml`); missing ⇒ `required-image-missing`; the tool
  never pulls/builds. API/Web full-stack readiness stays delegated to
  M14-231;
- only **valid backup + readable Docker + both persistent volumes absent +
  required images present** ⇒ `ready-to-reconstruct` (exit 0); everything
  else ⇒ `blocked` (exit 1).

**Staged runbook** (output only, never executed; no secrets, no absolute
paths): ① re-verify backup via this preflight → ② approved-window image
build/adoption (`minio_image_adoption.py`; `postgres:17-alpine` pull or
compose-time) → ③ immediately re-check both data volumes still absent →
④ explicitly create **only** the two named data volumes → ⑤ start only
postgres/minio (`up -d --no-build postgres minio`) → ⑥ alembic schema
migration → ⑦⑧ restore DB + object data via the documented
`python -m app.ops.cli restore` path (its manifest integrity check runs
first, hash mismatch leaves the target untouched) → ⑨ verify rows (30/41),
files (4), manifest anchor, and object hashes (optionally
`backup-restore-evidence`) → ⑩ full stack via the existing recovery path
(`production_recovery.py --dry-run` → enforce, or M14-231 guarded `--apply`)
→ ⑪ M14-231 preflight re-check + local/public/voice acceptance.

Secret suppression and private-path rejection are enforced end-to-end:
env/config values never leave memory (hash-only), all log lines and the JSON
report pass a secret-redaction pass plus a drive-letter/UNC scrubber
(`D:\…`/UNC → `<path>`; URLs like `https://…` are not mangled).

## Real Execution (read-only; no Docker/production action performed)

```
python tools/ops/logical_restore_preflight.py \
  --output .verify/m14-232/machine-preflight.json
```

- Authoritative result: **exit 1 / `verdict=blocked`**, blockers exactly
  `[existing-data-must-not-be-overwritten]`.
- Backup side: **valid** — 30 tables / 41 rows / 4 files, manifest SHA256
  anchor match, all four per-file hashes ok, location approved
  (`m14-193-production-cutover/pre-cutover-backup` shown relative, never as
  an absolute path).
- Docker side: engine readable (`29.8.1`); **both persistent volumes are
  present again on this machine** (`…_postgres-data`, `…_minio-data`;
  `…_searxng-cache` also present, so no cache note), required images all
  present (`aios/minio:RELEASE.2025-10-15T17-29-55Z`,
  `postgres:17-alpine`) — hence no image blocker.
- The dispatch anticipated "backup-valid but Docker unavailable → blocked
  **if live state still says so**". Live state no longer does: Docker is
  readable and the stack's volumes have reappeared since the M14-231
  evidence. The honest fail-closed answer for reconstruction over existing
  data is **do not** — `existing-data-must-not-be-overwritten`. **No Docker
  state was modified to make the result `ready-to-reconstruct`** (creating,
  deleting, or renaming volumes to flip the verdict would be exactly the
  kind of mutation this tool forbids).
- Report JSON and log contain no secrets and no absolute paths (asserted).
  Raw machine outputs kept in gitignored `.verify/m14-232/`
  (`machine-preflight.json`, `machine-preflight.log`); not committed.

## Verification evidence

Interpreter: canonical repo venv `..\..\.venv` → canonical
`ai-learning-os\.venv` (Python 3.11.15), the documented worktree pattern;
focused suite also green on Python 3.12.13.

```
python -m pytest services/api/tests/test_logical_restore_preflight.py -q
  → 41 passed (0 warnings)

python -m pytest services/api/tests/test_logical_restore_preflight.py \
    services/api/tests/test_production_restore_preflight.py \
    services/api/tests/test_production_recovery.py \
    services/api/tests/test_production_preflight.py \
    services/api/tests/test_production_web_gateway.py \
    services/api/tests/test_public_edge_preflight.py \
    services/api/tests/test_searxng_egress_recovery.py \
    services/api/tests/test_ops_snapshot.py \
    services/api/tests/test_production_monitor.py \
    services/api/tests/test_rc_smoke_rehearsal.py \
    services/api/tests/test_soak_window_gate.py \
    services/api/tests/test_minio_image_adoption.py \
    services/api/tests/test_minio_volume_adoption.py \
    services/api/tests/test_minio_selfbuild.py -q
  → 1010 passed, 1 skipped
```

- `python -m compileall -q` both files — clean; `python -m ruff check` both
  files — "All checks passed!"; `git diff --check` clean.
- Added-line scans on the diff: no secret patterns, no private absolute
  paths (scrubber unit test uses a synthetic `Z:`/`Q:` drive sample), no
  U+FFFD.
- Test coverage highlights: ready-path happy path; cache-absent note;
  approved-base boundary (base itself rejected); unapproved location
  fail-fast without reading; missing backup; junction backup root + nested
  junction + nested file symlink rejected (real `mklink /J` on Windows,
  POSIX symlink fallback, skip when platform cannot create links); schema /
  anchor / table-set (missing & extra, by name) / row-count (per-table and
  total-only drift) / file-set (extra & missing & count) / file-hash /
  manifest-not-json block matrix; Docker unavailable skips further probes;
  engine/volume/image transport errors → unreadable with no conclusions;
  volume-query failure draws no volume/image conclusions; both/single
  existing persistent volume blocks with both volume names in the action;
  required-image missing (minio self-built and postgres anchors);
  deterministic blocker order; runbook stage coverage (exactly two
  `docker volume create`, `up -d --no-build postgres minio`, alembic,
  `app.ops.cli restore`, hash reconciliation, existing recovery path,
  M14-231 preflight + public/voice acceptance); marker secret (env file +
  database rows) never in logs/stdout/report; no absolute paths in report;
  scrubber unit semantics incl. URL non-mangling; atomic report write
  scrubs paths; parser defaults + derived default backup dir lands inside
  an approved base; exit codes 0/1; source-contract (docker argv tokens
  ⊆ version/volume/image with ls/inspect sub-allowlists incl. the delegated
  M14-231 face, no destructive subcommand token literals, no direct
  subprocess, no own execute/apply/confirm-phrase options); constants
  cross-locked with M14-231 / `production_recovery` / compose / the
  M14-193 anchor literal; expected facts 30/41/4 pinned.

## Honesty boundaries

- This slice ships a **decision + staged plan** capability. It did not
  reconstruct anything: on the live machine the persistent volumes exist
  again, so the correct verdict is "do not reconstruct over existing data",
  and no action was taken against those volumes.
- The staged runbook's later stages (volume create, service start, restore)
  reference existing documented mechanisms (`minio_image_adoption.py`,
  `app.ops.cli restore` / `backup-restore-evidence`,
  `production_recovery.py`, M14-231 `--apply`); executing them remains a
  supervisor-approved, separately evidenced human action.
- Reconstruction readiness (`ready-to-reconstruct`) has **not** been
  demonstrated live in this slice — it is covered by offline tests only,
  because manufacturing that state on this machine would require deleting
  existing data volumes, which is precisely the mutation this tool forbids.
