# M14-214: Android public physical-device smoke evidence

## Scope

This slice adds the fail-closed public Android physical-device smoke
orchestrator and its offline contract tests. The verification target was the
public manifest `https://ndtool.cn/aios/download-manifest.json` and physical
adb serial `EYFBB22923201473`. Raw reports are kept in the gitignored
`.verify/m14-214-android-public-device-smoke*/` directories and are not
committed.

The tool intentionally reports `public_ready=false` even on a successful run:
one artifact, one device, and one point in time do not establish production
readiness.

## Real Execution

### Attempt 1

- Output: `.verify/m14-214-android-public-device-smoke/`.
- Result: `failed`, exit `1`, failure `internal / internal_error`.
- The physical-device precheck ran before any network request, so no manifest
  or APK was downloaded and no device mutation occurred.
- The cause was classification only: an offline-device `AdbError` was treated
  as an internal error. The orchestrator was corrected and covered by an
  offline test that now expects `device / adb_command_failed`.

### Final attempt

- Output: `.verify/m14-214-android-public-device-smoke-r2/`.
- Result: `blocked`, authoritative report exit `2`, failure
  `device / adb_command_failed`.
- The physical-device precheck again ran before download: no manifest/APK
  bytes were fetched, no APK was installed, the app was not launched, no
  logcat was cleared, and neither public API endpoint was probed.
- Cleanup was honestly reported as `not_downloaded`; `public_ready=false`.
- One targeted `adb reconnect offline` and one ADB server restart were
  attempted, but the device remained unavailable. A later read-only
  `adb devices -l` still reported `EYFBB22923201473 offline`. No factory reset,
  uninstall, package clear, or device setting change was performed.

The shell wrapper for the final run displayed exit `1`, while its report is
the authoritative record and says `exit_code: 2` for the blocked result.

## Report Anchors

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| `.verify/m14-214-android-public-device-smoke/report.json` | 659 | `4fc8b71cc7a6ea34af4d28737e03379f2d7ee3ac662e28d85ac055880a3e3387` |
| `.verify/m14-214-android-public-device-smoke/report.md` | 564 | `88351a1046bbe085771d6e01d5ab4790dcba64a01c71406343eefffa47d5f54b` |
| `.verify/m14-214-android-public-device-smoke-r2/report.json` | 685 | `3ef0743b92e50feb060132965de6fda23137bf7d17959e2e5e2c7df99dbc9633` |
| `.verify/m14-214-android-public-device-smoke-r2/report.md` | 567 | `db638964f351c9647c4cb47d8c54e6a24a6d68401cecebe9d5b9ef8f23dfad703` |

## Offline Verification

- `python -m pytest tests/android_release/test_public_device_smoke.py -q`:
  28 passed.
- `python -m pytest tests/android_release tests/android_smoke -q`:
  546 passed, 5 skipped.
- `python -m compileall tools/android_release`: passed.
- `python -m pytest services/api/tests/test_edge_deployment_templates.py -q`:
  22 passed.
- `python -m pytest services/api/tests/test_production_monitor.py -q`:
  256 passed.

## Slice Verification

- Final `git diff --check`: passed; Git emitted line-ending advisories only.
- Added-line scan for local absolute paths, secret values, credential
  assignments, and U+FFFD: no findings.

## Limitations

- No public APK download, install, launch, UI capture, process capture,
  logcat capture, or unauthenticated public API probe completed in the final
  authoritative run.
- The blocker is external device connectivity/authorization state, not a
  verified artifact or application failure.
- This slice does not change any production service, container, secret,
  token, proxy process, release gate, or public deployment.
