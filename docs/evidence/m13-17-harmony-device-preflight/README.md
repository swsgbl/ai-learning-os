# M13-17 — Harmony real-device read-only preflight gate

Fail-closed, strictly read-only preflight gate for **one explicit real
HarmonyOS device target**, executed only after an existing signature
verification report has been consumed and validated.

- Tool: `tools/harmony_release/device_preflight.py`
- Tests: `tests/harmony_release/test_device_preflight.py`
- Status: **mock-only** — see [Boundaries](#boundaries) below.

## What it does

Two modes (subcommands):

### `plan`

A pure plan — **zero file reads**. It never resolves the hdc tool, never
validates that the target exists, never reads the signature report, never
spawns a process, and never resolves the bundle name from
`AppScope/app.json5`: without an explicit `--bundle` the bundle-query step
stays `not_run` with the deterministic reason `bundle_not_supplied`
(`bundle_resolver(root, None)` — which would open `AppScope/app.json5` — is
never invoked in plan mode). With an explicit `--bundle` the name is used
verbatim, still without a single file read. Every step is recorded `not_run`
(`plan_mode`, or `bundle_not_supplied` for the bundle query).

```text
python tools/harmony_release/device_preflight.py plan --target 192.168.1.42:5555
```

Exit 0, status `planned`, `device_access.plan_only = true`.

### `check`

Read-only execution. Requires **all** of:

1. the exact flag `--confirm-read-only`;
2. one explicit target (`--target`, single token) — `all` / `any` / `*` /
   `list` / `devices` and multi-token targets are rejected;
3. **no loopback/localhost target** (no bypass exists — this gate exists to
   protect real hardware; emulators on `127.0.0.1` are out of scope);
4. a passing signature verification report from
   `tools/harmony_release/verify_signature.py`.

```text
python tools/harmony_release/device_preflight.py check \
  --target 192.168.1.42:5555 \
  --signature-report path/to/verify.json \
  --confirm-read-only
```

## The signature gate (fail-closed)

Before any hdc probe — and before the toolchain is even probed — the tool
consumes an **existing** report JSON. Every deviation blocks the run:

| Check | Failure code |
| --- | --- |
| `<report>.sha256` sidecar exists and its first token equals SHA-256(report bytes) | `signature_report_sidecar_missing` / `signature_report_sidecar_mismatch` |
| Strict JSON (NaN/Infinity rejected) | `signature_report_invalid_json` |
| `tool == "harmony_release_verify_signature"` | `signature_report_wrong_tool` |
| `status == "signed_and_valid"` and `exit_code == 0` | `signature_report_not_passing` |
| `signature.signed == true` **and** `signature.verified == true` (a real verifier verdict) | `signature_report_not_passing` |
| `input.sha256` is 64-hex and `input.size_bytes > 0` | `signature_report_hap_facts_invalid` |
| a present `input.relpath` is a safe POSIX relative path — absolute (`/…`), Windows drive (`C:…`, `C:/…`), UNC (`\\…`), backslash separators, colons, empty values, non-strings and `..` traversal are all rejected **before anything is serialized**, and the offending value is never echoed | `signature_report_hap_relpath_invalid` |

Signedness is taken **only** from the verifier report — never inferred from a
filename. The report's own SHA-256 and the HAP hash/size are recorded as the
gate's facts.

## Read-only probe allowlist

Exactly three probes exist in the module — nothing else can be built:

```text
hdc -t <TARGET> shell param get const.ohos.apiversion
hdc -t <TARGET> shell param get const.product.model
hdc -t <TARGET> shell bm dump -n <BUNDLE>
```

There is no code path that could provision, install, launch, stop, remove or
wipe anything on the device, and no log-reading command exists in this module.
A test pins the module source against every mutating verb (`install`,
`uninstall`, `aa start`, `force-stop`, `hilog`, `wipe`, `reboot`, `list
targets`).

## Never serialized

Raw argv, absolute paths, the raw target string, secrets, command
stdout/stderr, unrelated device identifiers, and any **unvalidated** report
`input.relpath` (an absolute/drive/UNC/`..` relpath blocks the run at the
gate and never reaches a single output byte; a validated safe POSIX relative
path is recorded only after passing `validate_relpath`). The target is recorded only
as a 12-hex SHA-256 prefix plus a loopback/other kind (`device_smoke.py`
conventions). Every release material/credential environment variable is
stripped from the child environment; only the names are recorded. Probe
output is parsed into value-free facts (`{"api_version": 12}`,
`{"bundle_present": true}`) — the model name and the bm dump are never
echoed.

## Exit semantics

| Exit | Status | Meaning |
| --- | --- | --- |
| 0 | `planned` / `ok` | plan recorded, or all probes passed |
| 1 | `failure` | missing hdc, spawn failure, timeout, nonzero probe exit, invalid probe output |
| 2 | `blocked` | malformed request, forbidden/loopback target, missing `--confirm-read-only`, any signature-gate deviation |

Failures are sorted deterministically; the JSON serialization is
`sort_keys=True`, byte-identical across runs.

## Validation (commands and results)

```text
python -m pytest tests/harmony_release/test_device_preflight.py -q     # new focused tests
python -m pytest tests/harmony_release -q                              # full package
python -m compileall tools/harmony_release tests/harmony_release       # byte-compiles clean
python -m ruff check --select F,E9 tools/harmony_release/device_preflight.py tests/harmony_release/test_device_preflight.py
git diff --check
```

Recorded results (2026-09-18 supervisor-correction round, Windows worktree,
mock-only, nothing committed):

```text
python -m pytest tests/harmony_release/test_device_preflight.py -q   # 86 passed in 0.39s
python -m pytest tests/harmony_release -q                            # 380 passed in 11.03s
python -m py_compile tools/harmony_release/device_preflight.py tests/harmony_release/test_device_preflight.py   # clean
python -m compileall -q tools/harmony_release tests/harmony_release  # clean (no output)
python -m ruff check --select F,E9 tools/harmony_release/device_preflight.py tests/harmony_release/test_device_preflight.py
                                                                     # All checks passed!
git diff --check                                                     # no output (clean)
```

`ruff check` with the full default rule set was also run as an extra
(informational, not the repo gate) and passes clean on both files.

## Boundaries — honest, mock-only scope

- **No real device was touched.** Every test injects a fake process runner,
  a fake tool resolver and fabricated report/sidecar inputs; hdc is never
  executed, no process is spawned, nothing is installed, started, stopped or
  uninstalled, and no device log or identifier is read. The test suite is
  mock-only by construction.
- **AGC is not accessed.** The tool performs no network access of any kind;
  AGC material acquisition remains an external, out-of-repo step.
- **No credentials and no signing env values are used.** Every release
  material/credential variable is stripped from the child environment and
  never recorded.
- **Real signing remains external.** The gate *consumes* a
  `verify_signature.py` report; producing that report (java +
  `hap-sign-tool.jar`, AGC profile/p12) is outside this tool and outside this
  worktree's mock-only scope.
- **Loopback refusal has no bypass.** Emulators on `127.0.0.1`/`localhost`
  cannot be `check`ed by this gate — by design. Nothing about this tool has
  been validated against a physical device; `plan` is the only mode whose
  behavior does not depend on real hardware.
- The recorded facts make **no** claim about app runtime state, install
  state beyond the bundle query output, or device identity; exit codes are
  the only lifecycle evidence.
