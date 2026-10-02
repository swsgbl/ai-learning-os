# M14-213H: Harmony current-main regression evidence

## Scope

This is a docs-only evidence slice based on `main` commit
`070b1f1fe0d268cbd48599a970b418fa69a01663`. It records the completed
HMHarness verification in the gitignored evidence directory
`.verify/m14-213-harmony-current-main-regression/`; that directory is the
audit source and is not committed.

The verification ran in worktree
`ai-learning-os-worktrees/m14-211-provider-smoke-container-path` at commit
`b1f1a72b55513716d9281e22d6597b1c580742bf`. The Harmony code tree is
byte-identical between that commit and current main:
`git diff --name-only b1f1a72..070b1f1 -- apps/harmony` returned no files.
Current main additionally changes only `tools/voice/smoke_local_voice.py`,
`services/api/tests/test_provider_smoke_container.py`, and
`docs/CHANGELOG.md`. Therefore this is an **Harmony-tree-equivalent
current-main regression**, not a claim that the full backend/tooling stack was
retested at `070b1f1`.

## Anchors

- Verification worktree HEAD: `b1f1a72b55513716d9281e22d6597b1c580742bf`.
- Evidence base/current main: `070b1f1fe0d268cbd48599a970b418fa69a01663`.
- Target: Harmony emulator `127.0.0.1:5555`, `emulator_only=true`.
- Tracked production files were unchanged by the verification.
- The task-owned loopback auth backend was stopped by its launcher in
  `finally`; no shared service or emulator was stopped or restarted.

## Results

| Gate | Result |
| --- | --- |
| Worktree HEAD and tracked-clean check | PASS |
| Authoritative Harmony release suite | PASS, 748/748; result reused from the preceding interrupted session and not rerun in the completing session |
| Unsigned HAP build | PASS, 573148 bytes |
| Unsigned preflight contract | PASS, exit 0 with `unsigned_boundary=true` |
| Signing-material fail-closed gate | PASS by expected exit 2: `blocked_by_external_materials` |
| Simulator/loopback auth smoke | PASS, launcher exit 0, `status=executed`, no failure reasons |
| Emulator health after smoke | PASS, `127.0.0.1:5555` remained available |

The auth smoke summary was 11 `ok`, one expected `not_run`
(`auth_phase_skip` because `--expect-auth on` skips the auth-off phase), and
zero failures. It covered the host auth contract, install, start, settings UI,
anonymous 401, wrong password, login and refresh, cold restart, logout,
background return, and uninstall. All seven host auth contract checks matched:
auth status, gated privacy 401, wrong password 401, login 200 with token,
`me` 200 with token, gated privacy 200 with token, and gated privacy rejection
with a wrong token.

## Launcher Environment Finding

The first auth-smoke continuation failed before product testing because the
host interpreter used by the launcher lacked `aiosqlite`. The auth smoke child
backend resolves its interpreter from `AIOS_AUTH_SMOKE_PYTHON`; pointing that
variable to the repository virtual environment allowed the real backend to
start. This was an environment launcher issue, not a product regression.

The successful launcher invocation ran the repository auth-smoke launcher
against the built unsigned HAP and target `127.0.0.1:5555`, with mutation
explicitly confirmed. Its stderr artifact is empty and its stdout ends with
`EXIT: 0`.

## Artifact Hashes

- Unsigned HAP: 573148 bytes, SHA-256
  `2636d5321b8f0449da769daca6c34a473b7029ef1b3009a573ce74bea3f5721d`.
- `auth_smoke_launcher.json`: SHA-256
  `2168f330331d884022f9eceecb03615bce2952f4ffaa10e2b37215be82159fb5`.
- `auth_smoke_on.json`: SHA-256
  `737568e19c01ecbace0f0072c52ddd627628afaac815ff68708316ac91840f1a`.
- `auth_smoke_launcher_stdout.json`: SHA-256
  `8e1ec749f9e086d44028f2465b5cde4e3bc0c538bfde837ec39d08f35b6fe362`.
- `preflight_expect_unsigned.json`: SHA-256
  `5fffbe40da63c4a1b57a15930dbdef32c18419cac384ebb1f39ea9173b2b6c5d`.
- `preflight_require_materials.json`: SHA-256
  `3aade09377b2d06f41a53a80940b37ab896f4a32eb59f16846dce11ac147c5fd`.
- `release_build.json`: SHA-256
  `f9e74a7319ffd356735298b97ce16d6a4eed0f3c6a56ea4ad8956c66d3a1d25d`.

The completing session also recaptured the signing-material stdout/stderr
gates. Their expected exit remains 2 because `.cer`, `.p12`, and `.p7b`
materials are intentionally absent in the unsigned boundary.

## Limitations

- The HAP is unsigned; `signedness_verified=false`.
- No AGC release signing, signed HAP, or public distribution was performed.
- Validation was simulator and loopback only; no Harmony physical device was
  used.
- The 748-test suite result was reused from the preceding session and was not
  rerun after the launcher environment repair.
- The result does not retest the current-main local-voice probe/test changes.
- It does not authorize production rollout, change any release gate, or make
  `production_ready=true`.

## Verification For This Docs Slice

- Confirmed `apps/harmony` has no diff between the verification commit and the
  evidence base.
- Parsed the referenced JSON artifacts and confirmed their statuses and exit
  codes.
- Recomputed the listed SHA-256 values.
- Ran the repository documentation/version guard tests selected for this slice.
- Ran `git diff --check`.
- Scanned added lines for secret-like values, local absolute paths, and U+FFFD.
