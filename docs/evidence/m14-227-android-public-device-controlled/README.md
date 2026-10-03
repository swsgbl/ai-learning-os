# M14-227: Android public-device controlled remediation retry evidence

## Scope

This slice applies exactly the narrowly stated remediation from the
M14-226 evidence README's Remaining Blocker section — a user-approved
`adb uninstall com.ailearningos.app` on the single authorized physical
serial `EYFBB22923201473` — and then re-runs the unchanged M14-214
fail-closed public Android physical-device smoke chain on the same
manifest `https://ndtool.cn/aios/download-manifest.json` and the same
serial. No tool logic, gate, timeout, or assertion was changed. No other
package, setting, service, ADB lifecycle, Docker, WSL, or proxy was
touched. Raw reports are kept in the gitignored
`.verify/m14-227-android-public-device-controlled/` and
`.verify/m14-227-diagnostic/` directories and are not committed.

## Preflight

- Worktree `ai-learning-os-worktrees/m14-227-android-public-device-retry`,
  branch `ops/m14-227-android-public-device-retry`, HEAD
  `b079692b` (local main at run time, ahead of the M14-226 base
  `92c80ad0` by the M14-225H merge; no fetch/rebase at run time,
  tracked files clean before work). The branch was subsequently
  rebased, docs-only with all recorded facts unchanged, onto the
  content-current base `c3ca2ee6` (PR #315 parent, carrying the
  M14-226/M14-227/M14-228 ledger entries).
- Read-only `adb devices -l` showed exactly one attached device:
  `EYFBB22923201473`, state `device` (authorized, online), product
  `MGA-AL00` (physical Huawei hardware, no emulator attached).
- Before-state confirmation (read-only `pm path`, `dumpsys package`,
  and `adb pull` + `apksigner verify --print-certs` of the installed
  base.apk): `com.ailearningos.app` was present, versionCode `1`,
  versionName `0.1.0`, `pkgFlags=[ DEBUGGABLE ... ]`,
  firstInstallTime `2026-09-08 03:56:56`, lastUpdateTime
  `2026-09-19 04:03:17`, signer `CN=Android Debug`, certificate
  SHA-256
  `740790e3bcae474d21c4880a5a28cac3b36b20f7a864ff753bae1f99bf6d68d3`
  — byte-for-byte the same stale debug install M14-226 diagnosed
  (pulled APK SHA-256 `da54763f...` equals M14-226's
  `installed-base.apk`). The gate "exactly one physical device
  authorized and online" passed and the blocker precondition matched,
  so the approved remediation was allowed to proceed.

## Remediation (the only device mutation in this slice)

```
adb -s EYFBB22923201473 uninstall com.ailearningos.app
# -> Success
```

After-state evidence (read-only): `pm path com.ailearningos.app` exits
1 with no path, `pm list packages` has no `ailearningos` entry, and
`adb devices -l` still shows exactly `EYFBB22923201473` online in
state `device` — no other device state was disturbed. This uninstall
was performed manually under the explicit user approval recorded in
the task instruction; the smoke tool itself still performs no
destructive device operation.

## Real Execution

Single run of the unchanged chain, default bounded/no-retry/fail-closed
gates, explicit serial (2026-10-03, console captured live via
redirection — `.verify/` existed beforehand, unlike M14-226):

```
python tools/android_release/public_device_smoke.py \
  --serial EYFBB22923201473 \
  --manifest-url https://ndtool.cn/aios/download-manifest.json \
  --output .verify/m14-227-android-public-device-controlled
```

- Authoritative result: **`failed`, exit `1`**, single failure
  `launch / stable_process_window_timeout` (not blocked).
- Material progress beyond M14-226, stage by stage:
  1. physical-device precheck passed (HUAWEI MGA-AL00, Android 10,
     `state=device`, non-QEMU);
  2. the public manifest downloaded (1392 bytes) and its contract
     validated — bytes identical to M14-226's manifest;
  3. the public APK `ai-learning-os-0.1.0-release-signed.apk`
     (8029570 bytes) downloaded with matching SHA-256
     `1246c3efb5da5732088dd95dffecf6f84fc1f14617eed45d38f701e28dd4634d`;
  4. the full `verify_artifact` gate passed: `v2+v3`, non-debug
     certificate, badging matches manifest, verifier `verified`;
  5. **`adb install -r` succeeded** — the M14-226 blocker
     (`INSTALL_FAILED_UPDATE_INCOMPATIBLE` against the stale debug
     install) is cleared by the approved uninstall;
  6. post-install `dumpsys package` identity matched (versionCode 1,
     versionName 0.1.0, signing identity available), logcat cleared,
     `.MainActivity` started;
  7. `wait_for_stable_process_window` exhausted its 45-second budget
     without three consecutive matching samples and the run failed
     closed with `launch / stable_process_window_timeout`;
  8. consequently the unauthenticated public API probe never ran
     (`public_api` empty); best-effort runtime evidence was still
     captured (UI hierarchy, screenshot, logcat; logcat analysis found
     no blocking issue); downloaded-APK cleanup honestly reported
     `not_requested` (opt-in flag not given).

## Launch-failure root cause (read-only diagnostics, deterministic)

The application actually started and stayed running on this firmware;
the failure is in the probe contract, not the artifact or the app:

- logcat shows `START ... com.ailearningos.app/.MainActivity`,
  `Start proc 7724:com.ailearningos.app` (16:59:07), window added,
  `onResume`, and no FATAL/ANR/crash for this package anywhere in the
  526455-byte captured logcat.
- Post-run read-only checks: `pidof com.ailearningos.app` returns the
  same pid `7724` (alive since launch), and the full
  `dumpsys window` dump reports
  `mCurrentFocus=Window{8f8ba5d u0 com.ailearningos.app/com.ailearningos.app.MainActivity}`
  (and the same for `mFocusedWindow`).
- However, the tool's `focused_window()` probe runs
  `dumpsys window windows`, and on this Huawei EMUI 10 (Android 10)
  firmware that subcommand prints **no** `mCurrentFocus`/`mFocusedWindow`
  lines at all (full-output grep: zero matches). The
  `package_window` condition is therefore structurally unsatisfiable
  on this device, `stable_count` never accumulates, and the gate
  times out — a firmware dumpsys-output difference, not an artifact,
  signing, install, manifest, verifier, or application defect.
- Independent rendering evidence: the captured `window-layout.xml`
  contains 51 nodes owned by `com.ailearningos.app` with a fully
  rendered Compose UI (texts include `首页`, `语音陪练`,
  `设置 / API 配置`, `正在探测登录状态…`, `正在探测 API…`,
  `砚席`, `学习 / 考场`), and the captured `screenshot.png` (4590
  bytes) was taken through the same stable foreground state.
- The harness was left unchanged by design (this slice re-runs the
  chain as-is); the EMUI focus-line compatibility gap is recorded
  below as the remaining limitation for a future explicit slice.

## Before/After Package and Certificate Evidence

| File (`.verify/m14-227-diagnostic/`) | Bytes | SHA-256 |
| --- | ---: | --- |
| `before-pm-path.txt` | 74 | `e1d03dd6ea0369cb7eeddb405dea23488b8eb8059c18db3b5b6a0ffbfc570b9e` |
| `before-dumpsys-package.txt` | 4740 | `86899197d03b73430e3809cf05d009d0e1cfdf91e7eacc52862133d1affeb1ef` |
| `before-installed-base.apk` | 11454463 | `da54763fd63454aaa80f3d00dac1f1bffeec6d534070cd1d9a34bbb89687c570` |
| `before-installed-certs.txt` | 311 | `ad747d9d53afd0f014aa4c8b7ef40892a48b3b3d575e0ca479a3f5fe81ac2ec5` |
| `uninstall-output.txt` | 9 | `df5f678c3e897e8db27b8c5de5ae1938a25aa084aeee89a1a44958f104316b3d` |
| `after-package-state.txt` | 110 | `c7dc4e17b6b8f56118659ee7ba05404d2ea3cd275861290c3815de5bb4694d48` |
| `after-package-state-full.txt` | 412 | `578fccc16dd2abe70b30468ac9f6c27598e51ff7677e25bf8f3ddccae79ef549` |
| `after-adb-devices.txt` | 121 | `4d7cbf42a21f73d9aea6646fecf3b87bf2bccc658af54516747b363342010e20` |
| `after-installed-base.apk` | 8029570 | `1246c3efb5da5732088dd95dffecf6f84fc1f14617eed45d38f701e28dd4634d` |
| `after-installed-certs.txt` | 327 | `800894b1fb858fba762b17f3c3108f431d945aa405542e1997f32c49e74c95e2` |
| `release-downloaded-certs.txt` | 327 | `800894b1fb858fba762b17f3c3108f431d945aa405542e1997f32c49e74c95e2` |
| `post-smoke-process-window.txt` | 84 | `aef2b5819db19ee4521c933018e99018ac13967b76f97ac529ba4d390fd1230c` |
| `smoke-console-stdout.log` | 3725 | `6f50da33d09335acf536ecf6767043c0cf7e4d9582e9768faf3a6e78a1dfc903` |
| `smoke-console-stderr.log` | 98 | `ac2ee277355a1130578423a2918974372f420f776b8fbcff73193f0cf49cc783` |
| `smoke-exit-code.txt` | 13 | `6f212f842323e0d1e7763fca1d8424ef4611005f46d1f69c22c3f9c6329364cb` |

Certificate transitions, cross-checked against M14-226's anchors:

- Before: `CN=Android Debug`, SHA-256 `740790e3...f6d68d3` (the
  M14-226 blocker state; APK bytes identical to M14-226's
  `installed-base.apk`, `da54763f...`).
- After the smoke run, the device carries the tool-installed package
  at `/data/app/com.ailearningos.app-hlF24MYb046Z18JQ4CoZ1Q==/base.apk`,
  firstInstallTime = lastUpdateTime = `2026-10-03 16:59:01`,
  `pkgFlags=[ HAS_CODE ALLOW_CLEAR_USER_DATA ]` (no `DEBUGGABLE`),
  signatures `version:3`, and the pulled base.apk is byte-identical
  (SHA-256 `1246c3ef...`) to the verified public release APK with
  signer `CN=AI Learning OS Release, O=AI Learning OS, C=CN`,
  certificate SHA-256
  `b583ed9e75840ff4b3c019398c6ad7e3ee4e0d90f6b4d56ef4466c3d8e58e9bf`
  — exactly matching M14-226's recorded release certificate.

## Report Anchors

Tool-written files under
`.verify/m14-227-android-public-device-controlled/` (the authoritative
record is `report.json`/`report.md`):

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| `report.json` | 3602 | `20bb6e977f9b4c4cc56e3f5f452252064e56034ab767852cf2746e028811b731` |
| `report.md` | 1601 | `26ed99fc246c97a6d6e7c2e0cbdaaa542b7a24469abba92d848d82cb0ee89bb0` |
| `download-manifest.json` | 1392 | `1e2ebb33cc6eeb1919ab35db922cdc88121f4fad3829bbda79df121d613ef3b5` |
| `ai-learning-os-0.1.0-release-signed.apk` | 8029570 | `1246c3efb5da5732088dd95dffecf6f84fc1f14617eed45d38f701e28dd4634d` |
| `adb-device.json` | 347 | `9a26d343dcda06a81b2480f5c7ecc0878d9246a60a0173107655648887c0c0db` |
| `package-dumpsys.txt` | 4562 | `34470906fdafc95104140fbe29418850ed877614ce59c407989d52d067b522cc` |
| `process-window.json` | 53 | `bab77b5884839941a15cac65457e022d8c7617c9ea33e60a2589ca44d303acc0` |
| `window-layout.xml` | 16994 | `0d1105a68ae688c549a26055edc6894626c4821cd18a143b833226cc350138d9` |
| `screenshot.png` | 4590 | `1a228f3e7ebc7742b17407eaf84bd356a0cddb052ae4d4bef55ebb1c965e4269` |
| `logcat.txt` | 526455 | `8433d4f210a316f4fbdc1dc46ecfd6439347dd5113c578810b79057ce92938ad` |

`console-stdout.log` in the diagnostic directory is the live captured
stdout (3725 bytes; line-for-line identical to `report.json` modulo
Windows CRLF line endings introduced by console redirection),
`console-stderr.log` the single observed status line `status=failed
exit=1 failures=stable_process_window_timeout`, and
`smoke-exit-code.txt` records `SMOKE_EXIT=1`.

## Actual Outcome vs Report Policy Fields

- Actual outcome (authoritative): `status=failed`, `exit_code=1`, one
  failure `launch / stable_process_window_timeout`. The smoke did NOT
  pass: the launch-stability gate failed closed, so the public API
  probe stage did not run and no API evidence exists for this run.
- The install-stage blocker from M14-226 is resolved: the verified
  public release APK installed cleanly on the physical device with the
  matching release certificate (before/after evidence above).
- Policy fields, independent of outcome: `public_ready=false` is a
  fixed boundary declaration (one artifact, one device, one point in
  time never establish production readiness); a passing run would
  still report `public_ready=false`, and this failed run does too.
- `cleanup.status=not_requested` reflects the absent opt-in flag, not
  a cleanup failure (the downloaded APK remains in the gitignored
  evidence directory).

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
- Added-line scan of docs for local absolute paths, secret values,
  credential assignments, and U+FFFD: no findings (evidence stays
  under gitignored `.verify/` per README convention).

## Remaining Limitation

- The launch-stability gate remains unsatisfiable on this specific
  Huawei EMUI 10 firmware because `dumpsys window windows` omits the
  `mCurrentFocus`/`mFocusedWindow` lines the probe matches against,
  even though the app verifiably launches, renders, and holds focus
  (full `dumpsys window`, stable pid, captured UI hierarchy). Any
  harness change (for example probing a focus-bearing dump or making
  the window source configurable) is deliberately out of scope for
  this slice, which re-runs the chain unchanged; it is left to a
  future explicit slice to decide.
- No unauthenticated public API probe ran in this run.

## Limitations

- The single approved destructive operation (`adb uninstall
  com.ailearningos.app` on serial `EYFBB22923201473`) is the only
  device mutation performed in this slice; nothing else about the
  device, ADB server, Docker, WSL, proxies, or production services
  was altered.
- This slice changes no production service, container, secret,
  token, proxy process, release gate, or public deployment, and does
  not assert production readiness.
