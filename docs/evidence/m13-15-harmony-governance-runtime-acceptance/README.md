# M13-15 Harmony Governance Runtime Acceptance

## Summary

M13-15 closes the runtime-acceptance gap left open by M13-14
(`docs/evidence/m13-14-harmony-governance-controls/`): the structural fix
(fixed header outside the Scroll) was built and merged, but its on-device
behavior was never re-measured. This slice re-validates the merged
`GovernancePane.ets` on the Harmony emulator against the isolated mock server
and preserves the full runtime evidence.

The acceptance result: **the M13-14 structural fix holds at runtime.** The
fixed header 治理(只读) / 整体刷新 / service URL / read-only notice is
**visible in every captured state** — load, reload, tab-return, error,
recovery, and deep-scroll. Its controls are **enabled in every one of those
states except one deliberate case**: during the `governance-loading-mockdown`
capture (the governance load still in flight, 服务版本 加载中…) the 整体刷新 and
per-section 刷新 buttons are `visible:true` but `enabled:false` — **disabled to
prevent duplicate refresh requests** — and they are **re-enabled** as soon as
the load settles (loaded / error / recovered states all report `enabled:true`).
The governance pane is read-only end-to-end: no write operations, no payload
rendering, no account/token/password persistence.

Evidence files in this directory are the preserved, unmodified captures from
the runtime session (2026-09-13, emulator `127.0.0.1:5555`, app PID 3826).

## Environment

- Device: Harmony emulator `127.0.0.1:5555` (product "emulator", phone, API 24).
  (`127.0.0.1:15555` is an old OpenHarmony 3.2 image and was **not** used.)
- App: `com.ailearningos.app`, `EntryAbility`, page `pages/Index` — 6 tabs
  (首页 / 学习 / 搜索 / 语音 / 设置 / 治理), governance is the last tab.
- Mock server: `python tools/harmony_mock/server.py --host 0.0.0.0 --port 8765`
  (isolated; host port 8000 is the production docker stack and was never touched).
- Emulator reaches host via `10.0.2.2`; app base URL set to
  `http://10.0.2.2:8765/` through the Settings pane
  (preferences key `aios_settings/api_base_url`). The compiled-in default
  `http://127.0.0.1:8000` is unusable on the emulator — this is expected and
  covered by the settings-save acceptance below.

## Evidence table

| File | State captured |
|---|---|
| `stage0-initial-layout.json` / `stage0-initial.jpeg` | Pre-fix contrast: old layout with header inside Scroll; service URL `http://192.168.8.3:8766/` |
| `stage0b-settings-layout.json` | Settings tab layout before URL edit |
| `settings-cleared.json` / `settings-cleared2.json` | Settings field cleared (2 captures, clock 08:39) |
| `settings-typed.json` | Typed `http://10.0.2.2:8765/` |
| `settings-saved.json` | 已保存 http://10.0.2.2:8765/ confirmed; notice "仅保存服务基地址(URL);不保存任何账号、令牌或密码" |
| `stage-check-settings.jpeg` | Visual capture of settings state |
| `governance-loading-mockdown.json` | Governance tab during load: 服务版本 shows 加载中…; 整体刷新 / 刷新 buttons `enabled:false, visible:true` (load in flight → disabled against duplicate refresh) |
| `governance-loaded.json` / `governance-loaded.jpeg` | Loaded: fixed header + 服务版本 0.12.5-mock, Git 提交 m1205mockgit, Alembic m1205mockrev, ops snapshot (生成时间 2026-09-07T12:00:00+00:00, postgresql, 论文总数 34, 搜索查询总数 456, 审计条目总数 1024, Worker 运行 是, admin 2 / learner 128, parsed 40 / pending 3, succeeded 33 / queued 2, 待审草稿); refresh controls `enabled:true` |
| `governance-after-reload.json` | 整体刷新 clicked → same content, header enabled |
| `governance-after-tab-return.json` | Switched tab and back → governance content remounts, header visible/enabled |
| `governance-error-mockdown.json` | Mock server stopped → 服务版本 error 网络请求失败: 2300028 Operation timeout; header 治理(只读) / 整体刷新 still visible, controls `enabled:true` |
| `governance-recovered.json` | Mock server restarted → recovery, all data back, controls `enabled:true` |
| `governance-deepscrolled.json` / `governance-deepscrolled.jpeg` / `governance-deepscrolled-visual.jpeg` | Deep scroll: audit log rows #1001 paper.publish, #1002 worker.tick visible; fixed header STILL visible at top (scroll offset does not hide it) |
| `app-pid.txt` | `3826` — app PID throughout the session |
| `hilog-full.txt` | Full hilog capture (8538 newline-terminated lines, ~1 MB) — scanned below |
| `hilog-fatal-scan.txt` | **Failed shell-command artifact, not evidence** (see Hilog analysis) |
| `scan-refresh-enabled.cjs` | Committed Node scan of the refresh-button `enabled`/`visible` state across every `governance-*.json` capture (exact command in claim 2) |
| `scan-hilog-crash-markers.cjs` | Committed Node scan of `FATAL` / `AppCrash` / `AppFreeze` / `JS_ERR` in `hilog-full.txt`, exits nonzero if any count is nonzero (exact command in Hilog analysis) |

## Build artifact under test

- HAP size: **515871 bytes**
- SHA256: `a5829186e8c7f309f88d993db2b20ba5dfa02ce9db76e622183d849adff46095`

This is the M13-14 merged build (structural fix, unsigned boundary as before);
the acceptance ran on this artifact.

## Hilog analysis (clean bill of health)

`hilog-full.txt` is the preserved capture of the whole session log. It is
re-scanned by the committed script `scan-hilog-crash-markers.cjs`, which
case-sensitively counts the four crash-marker classes over the whole file and
**exits nonzero if any count is nonzero** — the scan is a gate, not a printed
opinion. Exact command, run from this directory:

```sh
node scan-hilog-crash-markers.cjs hilog-full.txt
```

Captured output:

```
file: hilog-full.txt
lines: 8538
case-sensitive substring occurrences:
FATAL: 0
AppCrash: 0
AppFreeze: 0
JS_ERR: 0
result: PASS (all 4 marker counts zero)
```

Exit code 0. Negative control: the same script on a one-line file containing
`FATAL` reports `FATAL: 1` → `result: FAIL (1 marker class(es) nonzero: FATAL)`
and exits 1, so the zero result above is a real measurement, not a silent
no-op.

Supporting observations on the same capture:

- 39 lines mention `com.ailearningos.app` — 24 `RECENT`
  RecentDockRefreshAlarm, 8 `XPERF_SERVICE` first-move / last-up notifications
  for PID 3826 (5 `FIRST_MOVE`, 3 `LAST_UP`), 4 `KeyCommandHandler`, 2
  `UiTestKit_Server`, 1 `HMKeyboard_CeliaRecHelper` — all at `I` (info) level,
  no errors.
- The capture is a whole-emulator `hilog -x` dump, so it also carries
  system/launcher noise: 1024 `E`-level lines (435 `A01c06/ICON` weather
  DataShare failures, `DataShareServiceImpl`, `QosManager`, …) and 316 `W`
  `SystemReadParam failed!name is:persist.init.debug.loglevel` SELinux lines.
  **Zero of the 1024 `E` lines mention `ailearningos`**, so none is
  attributable to the app under test.
- The app process was stable (no crash, no restart) across the entire
  08:39–08:45 session, PID 3826.

Note on the line count: an earlier draft of this README (and the message of
HEAD commit `b9a4d22`) quoted **8539 lines**. That figure is the `String.split('\n')`
array length, which counts the trailing empty element after the final newline;
the capture actually has **8538** newline-terminated lines — the same number
`git diff --stat` reports for this file (8538 insertions). The script prints
the accurate 8538.

Note: `hilog-fatal-scan.txt` is a **failed shell-command artifact**, not scan
evidence — it contains only the shell error from the failed `findstr`
invocation on the capture host (`/bin/sh: findstr: inaccessible or not
found`). It is preserved only to document that the scan itself failed that
way. The actual evidence is the **full hilog scan** run with the committed
Node script on the preserved `hilog-full.txt` — results above (0 FATAL /
0 AppCrash / 0 AppFreeze / 0 JS_ERR, exit 0).

## Acceptance claims (all backed by preserved captures)

1. **Fixed header always reachable.** 治理(只读) / 整体刷新 / 服务地址 line /
   只读视图 notice appear in the accessibility tree in every captured state,
   including `governance-deepscrolled.json` where the Scroll is scrolled to the
   audit log, and `governance-error-mockdown.json` where the load failed. This
   is the runtime confirmation of the M13-14 structural fix (header outside
   Scroll).
2. **Refresh control enabled-sequence (reproducible).** The committed script
   `scan-refresh-enabled.cjs` parses the fixed-header 整体刷新 button
   (accessibilityId `496`) and the per-section 刷新 buttons out of every
   `governance-*.json` capture. Exact command, run from this directory:

   ```sh
   node scan-refresh-enabled.cjs .
   ```

   Result (exit code 0): every captured state reports `visible:"true"`.
   `enabled:"true"` in `governance-loaded`, `governance-after-reload`,
   `governance-after-tab-return`, `governance-error-mockdown`,
   `governance-recovered` and `governance-deepscrolled`; `enabled:"false"`
   (with `visible:"true"`) only in `governance-loading-mockdown` — the load is
   in flight, so the control is deliberately disabled against duplicate
   refresh — i.e. the control is never *stuck* disabled, it is re-enabled once
   the load settles and after error recovery. (The pre-fix contrast captures
   `stage0-*` are outside the scan's `governance-*.json` pattern and are not
   part of this claim.)
3. **Read-only boundary honored.** The pane renders version / ops snapshot /
   audit log only; no payload content is shown; Settings saves only the base
   URL and explicitly does not persist accounts, tokens, or passwords
   (`settings-saved.json` notice text).
4. **Error + recovery path works.** Mock server stop → per-section error state
   (2300028 Operation timeout) with header intact; restart → full data
   restored (`governance-error-mockdown.json` → `governance-recovered.json`).
5. **No crashes.** `node scan-hilog-crash-markers.cjs hilog-full.txt` over the
   full session capture: 0 FATAL / 0 AppCrash / 0 AppFreeze / 0 JS_ERR with
   exit code 0; PID 3826 stable 08:39–08:45.

## Non-claims

- No real provider / production backend / production database exercised; only
  the isolated mock server (`tools/harmony_mock`).
- No write operations, installs to other devices, AGC signing, or
  production_ready claims. The HAP under test is the M13-14 merged build
  (structural fix); this slice adds no code — it is docs/evidence only.
- The pre-fix contrast captures (`stage0-*`) are from the earlier M13-14
  session and are kept for reference; the acceptance itself ran on the
  structural-fix build.

## How to reproduce

1. Start the mock server: `python tools/harmony_mock/server.py --host 0.0.0.0 --port 8765`.
2. On the emulator (`127.0.0.1:5555`), open Settings → set AIOS 服务地址 to
   `http://10.0.2.2:8765/` → 测试连接 → save.
3. Open the 治理 tab: observe 加载中… (refresh controls visible but disabled)
   then version / ops snapshot / audit log with the controls re-enabled.
4. Click 整体刷新, switch tabs and back, stop/restart the mock server
   (error → recovery), scroll to the bottom (audit log) — the fixed header
   stays visible in all states.
5. Re-run the two offline scanners from this directory — no device and no
   network needed, they only read the preserved captures:

   ```sh
   node scan-refresh-enabled.cjs .
   node scan-hilog-crash-markers.cjs hilog-full.txt
   ```
