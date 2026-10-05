# M14-238: public mobile evidence plan (plan-only gap report) evidence

## Scope

This slice adds the plan-only orchestrator companion
`tools/android_release/public_mobile_evidence_plan.py` plus focused offline
contract tests. It reads one explicit JSON manifest (v1, role paths relative
to the manifest directory) and emits a sanitized JSON+Markdown **gap report**
over file-level facts for the six M14-237 evidence roles, together with the
exact quoted M14-237 replay command. Raw demo artifacts stay in the
gitignored `.verify/m14-238-demo/` directory and are not committed.

Contract highlights (all pinned by tests):

- **Plan-only, no child semantics**: role files are read as bytes only —
  never `json.loads`-ed, never checked for schema/tool/status. Garbage or
  binary content still reports present/bytes/SHA-256 with zero blockers
  (`test_garbage_non_json_role_content_stays_clean`).
- **No subprocess / network / device / Docker / env / secret**: pinned by an
  ast source guard plus the zero-side-effect output discipline (single
  explicit report directory, atomic write + read-back verify, zero residue
  on refusal).
- **Manifest v1 validation (invalid → exit 2, zero report writes)**: schema
  equality, unknown keys, duplicate JSON keys (via `object_pairs_hook`),
  missing/null required roles, absolute or traversal or backslash or colon
  or control-char or over-long paths, duplicate role targets, and
  `freshness_hours` (bool rejected; bounds [1, 720], default 24 — mirroring
  M14-237's replay-valid range).
- **File-level gap blockers (exit 1, report written)**: `missing-file`,
  `symlink-file`, `reparse-file`, `directory-not-file`, `empty-file`,
  `unreadable-file`. The optional `cloudflare_preflight` role absent or
  `null` is **not** a blocker (mirrors M14-237's optional-input semantics).
- **Sanitized output**: report carries basename/bytes/SHA-256/role names and
  fixed-vocabulary codes only — never absolute local paths, never file
  content (asserted by `test_report_is_sanitized_no_absolute_local_paths`).
- **Exact quoted replay**: the M14-237 command is reconstructed from the
  manifest's own relative POSIX paths, `shlex.quote`-d (space-safe), with
  `--freshness-hours` from the manifest (default 24) and an honest
  `--output <output-dir>` placeholder; the cloudflare flag appears only
  when the role is provided.
- **Injectable clock and Store**: `generated_at` comes from an injected
  `now` (CLI: real current UTC; tests: fixed
  `2026-10-05T12:00:00Z`); all filesystem access goes through the shared
  `Store` protocol so symlink/unreadable/write-failure shapes are tested
  structurally without Windows symlink privileges.

## Real CLI demo (synthetic data, gitignored `.verify/m14-238-demo/`)

From the manifest directory, against a synthetic six-role manifest
(`cloudflare_preflight: null`, `freshness_hours: 24`):

- Complete run: exit `0`, `[public-mobile-evidence-plan] COMPLETE:
  complete=True blockers=0`, JSON+Markdown written to `out-complete/`.
- Gap run (attestation file removed): exit `1`, blocker
  `missing-file:attestation`, report written to `out-gap/` including the
  exact replay command:
  `python tools/android_release/public_mobile_release_gate.py
  --restore-preflight evidence/restore/report.json ... --attestation
  evidence/attestation.json --output <output-dir> --freshness-hours 24`
  (no cloudflare flag — role not provided).

Console transcript: `.verify/m14-238-demo/console.log` (both exits
captured).

## Report anchors

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| `.verify/m14-238-demo/manifest.json` | 335 | `c6dc1011e6141ca501044d7b483f9e58236d43e22c8c93d02f692d160c46f870` |
| `.verify/m14-238-demo/out-complete/public-mobile-evidence-plan.json` | 2417 | `af3fc7bd92f586df8313017e3194c753e310ed8e2c6b6138aec6e7831b97c60d` |
| `.verify/m14-238-demo/out-complete/public-mobile-evidence-plan.md` | 1707 | `9889fe92eac70a6e5be17548b8f9f715038b58545fbf8a252e37019abe8d0304` |
| `.verify/m14-238-demo/out-gap/public-mobile-evidence-plan.json` | 2435 | `2ca4f8c5f81650adc549b8f541f87976fe0617bd5afa5e8b5ed6b2ae86132faf` |
| `.verify/m14-238-demo/out-gap/public-mobile-evidence-plan.md` | 1692 | `a0ee0be34ba4e2bab6b0004f89f0a1252026caea5a4b40abb9a50f92db9b4259` |
| `.verify/m14-238-demo/console.log` | 311 | `7d42257bdd24c101278a0fbd3ecf911ba7e45182892d939d58930808e716aff2` |

Synthetic role files (demo only, no real report semantics): restore 14 B /
edge 16 B / smoke 17 B / release-evidence 19 B, plus the removed
attestation; hashes are pinned in the generated reports themselves.

## Offline verification

- `python -m pytest tests/android_release/test_public_mobile_evidence_plan.py -q`:
  **56 passed** (fixed-time + injected-Store contract tests, ast read-only
  guard, zero-residue refusal paths).
- `python -m pytest tests/android_release -q`: **354 passed, 4 skipped**
  (zero regression in the neighboring android_release suites).
- `python -m ruff check` on both new files: passed.
- `python -m compileall tools/android_release/public_mobile_evidence_plan.py`:
  passed.

## Slice verification

- Final `git diff --check`: passed.
- Changed-line scan for secret values, credential assignments, local
  absolute paths, and U+FFFD: no findings.

## Limitations

- The planner deliberately asserts no readiness of any kind (`complete`
  only means the six role files exist as regular, non-link, non-empty
  files at bytes level; M14-237 remains the sole readiness authority).
- The replay command's `--output` is a placeholder by design — the gate
  output directory is the operator's explicit choice at replay time.
- No production service, container, secret, token, proxy process, release
  gate, device, or public deployment is touched by this slice.
