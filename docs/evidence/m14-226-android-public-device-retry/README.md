# M14-226: Android public-device physical smoke retry evidence

## Scope

This slice retries the existing M14-214 fail-closed public Android
physical-device smoke chain on the same public manifest
`https://ndtool.cn/aios/download-manifest.json` and the same physical adb
serial `EYFBB22923201473`, now that the device is authorized and online
again. No tool logic, gate, timeout, or assertion was changed. Raw reports
are kept in the gitignored `.verify/m14-226-android-public-device-retry/`
and `.verify/m14-226-diagnostic/` directories and are not committed.

## Preflight

- Fetch verified: `origin/main` == `92c80ad00be6b10b301a5d442e7b1e635fcf06fd`.
- Worktree `ai-learning-os-worktrees/m14-226-android-public-device-retry`,
  branch `ops/m14-226-android-public-device-retry`, HEAD == base, tracked
  files clean before work.
- Read-only `adb devices -l` showed exactly one attached device:
  `EYFBB22923201473`, state `device` (authorized, online), product
  `MGA-AL00` (physical Huawei hardware, no emulator attached). The gate
  "exactly one physical device authorized and online" passed, so the chain
  was allowed to proceed. ADB was not restarted; no Docker/WSL/production
  container/proxy lifecycle was touched.

## Real Execution

Single run, default bounded/no-retry/fail-closed gates, explicit serial:

```
python tools/android_release/public_device_smoke.py \
  --serial EYFBB22923201473 \
  --manifest-url https://ndtool.cn/aios/download-manifest.json \
  --output .verify/m14-226-android-public-device-retry
```

- Authoritative result: **`blocked`, exit `2`**, failure
  `device / adb_command_failed` (blocked).
- The chain progressed materially beyond M14-214's offline-device block:
  1. physical-device precheck passed (HUAWEI MGA-AL00, Android 10,
     `state=device`, non-QEMU);
  2. the public manifest was downloaded (1392 bytes) and its contract
     validated;
  3. the public APK `ai-learning-os-0.1.0-release-signed.apk` (8029570
     bytes) was downloaded once, bounded, with matching SHA-256
     `1246c3efb5da5732088dd95dffecf6f84fc1f14617eed45d38f701e28dd4634d`;
  4. the full `verify_artifact` gate passed: `v2+v3`, non-debug
     certificate, aapt badging matches manifest (`com.ailearningos.app`,
     versionName `0.1.0`, versionCode `1`), verifier `verified`;
  5. the replace-install on the physical device failed and was
     fail-closed reported as `device / adb_command_failed`.
- Consequently no launch, no logcat clear/capture, no UI/screenshot
  evidence, and no public API probe ran; `launch.stable_process_window`
  stayed `false`, `public_api` empty, `logcat.captured=false`. Downloaded
  APK cleanup honestly reported `not_requested` (opt-in flag not given;
  the APK remains in the gitignored evidence directory).

## Root Cause (read-only diagnostics, deterministic)

- `pm path` + `adb pull` (read-only, no device mutation) of the installed
  `com.ailearningos.app` shows it is a **debug build signed with
  `CN=Android Debug`** (cert SHA-256
  `740790e3bcae474d21c4880a5a28cac3b36b20f7a864ff753bae1f99bf6d68d3`,
  `pkgFlags=[ DEBUGGABLE ... ]`, firstInstallTime 2026-09-08, lastUpdate
  2026-09-19 — left by earlier local slices).
- The public release APK is signed with `CN=AI Learning OS Release`
  (cert SHA-256
  `b583ed9e75840ff4b3c019398c6ad7e3ee4e0d90f6b4d56ef4466c3d8e58e9bf`).
- Same package name plus different signer certificates makes
  `adb install -r` deterministically rejected by Android
  (`INSTALL_FAILED_UPDATE_INCOMPATIBLE` class). This is external device
  state, not an artifact, manifest, verifier, or harness defect — the
  tool behaved exactly per its fail-closed contract, so no harness repair
  was made (none was needed).
- The M14-214 tool contract explicitly performs no uninstall and no
  package clear; removing the stale debug installation is a destructive
  device operation outside both the tool's contract and this slice's
  boundary, so it was not attempted.
- Device integrity after the run: `firstInstallTime`/`lastUpdateTime`
  unchanged (the rejected replace-install is atomic), device still
  `device`/online in the post-run `adb devices -l`. No factory reset,
  uninstall, package clear, or settings change was performed.

## Actual Outcome vs Report Policy Fields

- Actual outcome (authoritative): `status=blocked`, `exit_code=2`,
  one blocked failure `device / adb_command_failed`. The smoke did NOT
  pass: install never succeeded, so launch/runtime/API evidence does not
  exist for this run.
- Policy fields, independent of outcome: `public_ready=false` is a fixed
  boundary declaration (one artifact, one device, one point in time never
  establish production readiness) and must not be read as an extra
  failure; conversely a future passing run would still report
  `public_ready=false`.
- `cleanup.status=not_requested` reflects the absent opt-in flag, not a
  cleanup failure.

## Report Anchors

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| `.verify/m14-226-android-public-device-retry/report.json` | 2517 | `ff33ce29333c026b95bf248c8fc04c7c0e2d146515443f052dbf95e78ab0bd1e` |
| `.verify/m14-226-android-public-device-retry/report.md` | 1046 | `52b6078638c10053791630e9ed1050fd827d26bb22534e0b9d26998e67311887` |
| `.verify/m14-226-android-public-device-retry/download-manifest.json` | 1392 | `1e2ebb33cc6eeb1919ab35db922cdc88121f4fad3829bbda79df121d613ef3b5` |
| `.verify/m14-226-android-public-device-retry/ai-learning-os-0.1.0-release-signed.apk` | 8029570 | `1246c3efb5da5732088dd95dffecf6f84fc1f14617eed45d38f701e28dd4634d` |
| `.verify/m14-226-android-public-device-retry/adb-device.json` | 261 | `345cd736ccdccbf737a6d94573384010c0f6a891d9ae020ee5e1e5080678aeeb` |
| `.verify/m14-226-android-public-device-retry/console-stdout.log` | 2517 | `ff33ce29333c026b95bf248c8fc04c7c0e2d146515443f052dbf95e78ab0bd1e` |
| `.verify/m14-226-android-public-device-retry/console-stderr.log` | 87 | `a97f5357552f4508cffd83f641589a0c2fe3340d3778f9fbfbf50ccb03865488` |
| `.verify/m14-226-android-public-device-retry/console-log-provenance.txt` | 404 | `1024447d12eff79a1a54210299c325e90f9e3c3ed23f03bda187c5ebbee4f651` |
| `.verify/m14-226-diagnostic/installed-base.apk` | 11454463 | `da54763fd63454aaa80f3d00dac1f1bffeec6d534070cd1d9a34bbb89687c570` |
| `.verify/m14-226-diagnostic/installed-certs.txt` | 311 | `ad747d9d53afd0f014aa4c8b7ef40892a48b3b3d575e0ca479a3f5fe81ac2ec5` |
| `.verify/m14-226-diagnostic/release-certs.txt` | 327 | `800894b1fb858fba762b17f3c3108f431d945aa405542e1997f32c49e74c95e2` |
| `.verify/m14-226-diagnostic/post-run-adb-devices.txt` | 121 | `4d7cbf42a21f73d9aea6646fecf3b87bf2bccc658af54516747b363342010e20` |
| `.verify/m14-226-diagnostic/post-run-package-state.txt` | 180 | `4bb37a0ac6f2c96ef389bf78cbcab5652c758cbc99532faa571aa24889731ec5` |

Console-log provenance: the live `tee` failed because `.verify/` did not
exist before the run; both console files were reconstructed
deterministically from the session capture — `console-stdout.log` is
byte-identical to `report.json` (matching SHA-256 above proves it), and
`console-stderr.log` is the single observed status line. The
tool-written `report.json`/`report.md` remain the authoritative record.

## Offline Verification

- `python -m pytest tests/android_release/test_public_device_smoke.py -q`:
  28 passed.
- `python -m pytest tests/android_release tests/android_smoke -q`:
  546 passed, 5 skipped.
- `python -m compileall tools/android_release`: passed.
- No source file was modified in this slice; the tests re-confirm the
  unchanged harness contract rather than new code.

## Slice Verification

- Final `git diff --check`: passed.
- Added-line scan for local absolute paths, secret values, credential
  assignments, and U+FFFD: no findings (see README "no local paths"
  convention — all evidence stays under gitignored `.verify/`).

## Remaining Blocker

- The physical device still carries the stale debug-signed
  `com.ailearningos.app` (Android-Debug certificate) from earlier local
  slices. Clearing it requires a user-approved `adb uninstall
  com.ailearningos.app` (destructive, outside this slice's boundary);
  after that, re-running this exact chain should be able to reach
  install/launch/API stages. Device connectivity itself is no longer a
  blocker.

## Limitations

- No public APK install, launch, UI capture, process capture, logcat
  capture, or unauthenticated public API probe completed in this run.
- The blocker is deterministic external device state (signature
  mismatch against a stale debug install), not a verified artifact,
  manifest, verifier, or application failure.
- This slice does not change any production service, container, secret,
  token, proxy process, release gate, or public deployment, and performs
  no destructive device operation.
