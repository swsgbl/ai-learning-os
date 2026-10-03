# M14-229: Android public-device focus probe repair and on-device verification

## Scope

This slice repairs the focus probe of the M14-214 fail-closed public
Android physical-device smoke chain
(`tools/android_release/public_device_smoke.py`) and verifies the repair on
the real public device chain against manifest
`https://ndtool.cn/aios/download-manifest.json` and physical adb serial
`EYFBB22923201473`. Android tool + tests only; no Docker/production/proxy/
Harmony change; no gate, timeout, or assertion was weakened. Raw reports are
kept in the gitignored `.verify/m14-229-android-public-device-focus-fallback/`
(run 1) and `.verify/m14-229-android-public-device-focus-fallback-r2/`
(run 2) directories and are not committed.

## Defect being repaired (two EMUI 10 layers)

The probe's evidence source was `dumpsys window windows` alone. On EMUI 10
(HUAWEI MGA-AL00, Android 10 — the same device family as the M14-226
preflight):

1. `dumpsys window windows` contains **no** `mCurrentFocus`/`mFocusedWindow`
   line at all, while the full `dumpsys window` still reports them — so the
   probe read no focus evidence and `package_window` was structurally false.
2. In the full `dumpsys window` output the focus lines are **indented**
   (`mCurrentFocus` at two spaces, `mFocusedWindow` at four, real-device
   capture below), so a column-0 `^m...` anchor can never match them either.

Both layers are evidence-format defects, not gate defects: on the failing
run the app process was alive and stable, the UI hierarchy showed the
package fullscreen, and the live focus line pointed at our package. The
correct fix is to widen where focus evidence is read from, never to relax
what counts as a pass.

## Repair (fail-closed preserved)

- `RealAdbSmokeDevice.focused_window()` runs `dumpsys window windows` first
  and falls back to one full `dumpsys window` only when **no**
  `mCurrentFocus=`/`mFocusedWindow=` line is present (presence regex
  `FOCUS_LINE_RE`). A present foreign or null focus line never triggers the
  fallback.
- Both regexes tolerate leading whitespace (`^\s*`), matching the real EMUI
  indentation. The ownership filter `FOCUSED_WINDOW_RE` still requires a
  `Window{...}` payload, so `mCurrentFocus=null` (indented or not) never
  counts as package-owned.
- The pass gate is unchanged: three consecutive stable nonempty PID samples
  plus a package-owned focus line; stable PID and a captured UI dump alone
  never pass. Probe/stability logic and the offline `AdbSmokeDevice`
  injection surface are unchanged.

## Real Execution

### Run 1 — repair layer 1 only (17:52–17:53, commit `9ee060eb`)

```
python tools/android_release/public_device_smoke.py \
  --serial EYFBB22923201473 \
  --manifest-url https://ndtool.cn/aios/download-manifest.json \
  --output .verify/m14-229-android-public-device-focus-fallback
```

- Authoritative result: **`failed`, exit `1`**, failure
  `launch / stable_process_window_timeout` (console stderr captured; full
  JSON report in the evidence directory).
- Progress beyond M14-226: the external stale debug-signature install was
  cleared before this run (read-only preflight showed a fresh
  `2026-10-03 16:59:01` install, `pkgFlags=[ HAS_CODE
  ALLOW_CLEAR_USER_DATA ]`, no DEBUGGABLE), so this run passed device
  precheck → manifest (1392 B, SHA-256 `1e2ebb33…`) → bounded APK download
  (`ai-learning-os-0.1.0-release-signed.apk`, 8029570 B, SHA-256
  `1246c3ef…`) → full `verify_artifact` gate (v2+v3, non-debug cert,
  badging equal) → **replace-install succeeded** → activity started →
  launch stability gate failed.
- Read-only root cause on device (no mutation):
  - `dumpsys window windows | grep -nE "CurrentFocus|FocusedWindow"` → no
    output (layer 1 confirmed);
  - `dumpsys window | grep -nE "CurrentFocus|FocusedWindow"` →
    `  mCurrentFocus=Window{8f1d0b1 u0 com.ailearningos.app/com.ailearningos.app.MainActivity}`
    (line 105, two-space indent) and
    `    mFocusedWindow=Window{8f1d0b1 u0 …}` (line 272, four-space indent)
    — layer 2 found;
  - `pidof com.ailearningos.app` → `11590`, alive since
    `17:52:20 Start proc 11590:…for activity {…MainActivity}`, no process
    death in logcat;
  - `window-layout.xml` root node `package="com.ailearningos.app"`
    fullscreen `[0,0][720,1555]`; logcat had no FATAL EXCEPTION (the five
    `AndroidRuntime` lines are the uid-2000 uiautomator tool's own
    startup/shutdown).
  - Verdict: stable PID, foreground UI, focus on our package — the failure
    was purely the column-0 regex anchor. Repair layer 2 applied (TDD:
    fixtures switched to the real indented capture; 2 tests red → green).

### Run 2 — full repair (18:04:34–18:05:26)

```
python tools/android_release/public_device_smoke.py \
  --serial EYFBB22923201473 \
  --manifest-url https://ndtool.cn/aios/download-manifest.json \
  --output .verify/m14-229-android-public-device-focus-fallback-r2
```

- Authoritative result: **`blocked`, exit `2`**, failure
  `health / health_network_unavailable` (blocked).
- **The focus probe repair is verified on the real device**: this run passed
  device precheck → manifest → bounded APK download (same hashes) →
  `verify_artifact` → replace-install → activity start → **launch stability
  gate passed on the EMUI fallback path**: 3 samples, `package_window =
  [true, true, true]`, stable PID `13306` (focus evidence read via the full
  window dump because the windows dump omits the focus lines) → UI dump,
  screenshot, logcat captured with no blocking issue.
- The chain then stopped fail-closed at the first public API probe.

## Remaining external block (not this slice's defect)

Read-only probes of the public host after run 2:

- `https://ndtool.cn/aios/health` → 404 (HTML);
- `https://ndtool.cn/health` → 200 but serves a different application
  (`{"status":"healthy","app":"AI简历智能生成平台","version":"1.1.0"}`; the
  smoke contract requires `"status":"ok"`, so even this path would fail the
  body gate);
- `https://ndtool.cn/api/v1/auth/status` and
  `https://ndtool.cn/aios/api/v1/auth/status` → 404 (FastAPI route-level).

The public host currently serves only static downloads (manifest + APK);
the AI Learning OS API face is not deployed there. The tool's fail-closed
behavior is correct: a full pass requires install, stable launch,
UI/logcat, `health`, and `auth_status`, and `public_ready` stays `false` in
both runs (it is a fixed boundary statement, never an outcome). Note on
classification (pre-existing tool semantics, unchanged here): urllib raises
`HTTPError` (an `OSError` subclass) for HTTP error statuses, so a 404 on the
health probe is reported as `health_network_unavailable`.

## Test and hygiene evidence

- TDD: fixtures moved to the real indented EMUI capture made
  `test_focus_probe_passes_via_full_window_dump_fallback_on_emui10` and
  `test_focused_window_does_not_fall_back_when_focus_line_reports_null` red
  before the `^\s*` regex fix, green after.
- Focused suite
  `python -m pytest tests/android_release/test_public_device_smoke.py -q`:
  **36 passed** (M14-226 baseline 28 + 8 focus tests: windows-first
  adapter+e2e, EMUI fallback success, indented null no-fallback,
  AOSP-foreign no-fallback fail, EMUI-fallback-foreign fail,
  both-dumps-absent fail).
- Android neighbor regression `tests/android_release tests/android_smoke`:
  **554 passed / 5 skipped**.
- `python -m compileall tools/android_release`, `ruff check` on both changed
  files, `git diff --check`: clean. Added tracked lines scanned for
  secrets/local absolute paths: clean.
