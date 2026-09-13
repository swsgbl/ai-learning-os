# M13-15 Harmony Governance Runtime Acceptance

## Summary

M13-15 closes the runtime-acceptance gap left open by M13-14
(`docs/evidence/m13-14-harmony-governance-controls/`): the structural fix
(fixed header outside the Scroll) was built and merged, but its on-device
behavior was never re-measured. This slice re-validates the merged
`GovernancePane.ets` on the Harmony emulator against the isolated mock server
and preserves the full runtime evidence.

The acceptance result: **the M13-14 structural fix holds at runtime.** The
fixed header 治理(只读) / 整体刷新 / service URL / read-only notice stays visible
and enabled across load, reload, tab-return, error, recovery, and deep-scroll
states. The governance pane is read-only end-to-end: no write operations, no
payload rendering, no account/token/password persistence.

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
| `governance-loading-mockdown.json` | Governance tab during load: 服务版本 shows 加载中… |
| `governance-loaded.json` / `governance-loaded.jpeg` | Loaded: fixed header + 服务版本 0.12.5-mock, Git 提交 m1205mockgit, Alembic m1205mockrev, ops snapshot (生成时间 2026-09-07T12:00:00+00:00, postgresql, 论文总数 34, 搜索查询总数 456, 审计条目总数 1024, Worker 运行 是, admin 2 / learner 128, parsed 40 / pending 3, succeeded 33 / queued 2, 待审草稿) |
| `governance-after-reload.json` | 整体刷新 clicked → same content, header enabled |
| `governance-after-tab-return.json` | Switched tab and back → governance content remounts, header visible/enabled |
| `governance-error-mockdown.json` | Mock server stopped → 服务版本 error 网络请求失败: 2300028 Operation timeout; header 治理(只读) / 整体刷新 still visible |
| `governance-recovered.json` | Mock server restarted → recovery, all data back |
| `governance-deepscrolled.json` / `governance-deepscrolled.jpeg` / `governance-deepscrolled-visual.jpeg` | Deep scroll: audit log rows #1001 paper.publish, #1002 worker.tick visible; fixed header STILL visible at top (scroll offset does not hide it) |
| `app-pid.txt` | `3826` — app PID throughout the session |
| `hilog-full.txt` | Full hilog capture (8539 lines, ~1 MB) |
| `hilog-fatal-scan.txt` | FATAL / AppCrash / AppFreeze / JS_ERR scan result (see below) |

## Hilog analysis (clean bill of health)

`hilog-full.txt` (8539 lines) was scanned for crash markers:

- `FATAL` → **0**
- `AppCrash` → **0**
- `AppFreeze` → **0**
- `JS_ERR` → **0**
- App lines: 39 for `com.ailearningos.app` / 24 for `EntryAbility` — all
  RECENT-dock / XPERF_FIRST_MOVE notifications for PID 3826, no errors.
- Only warnings: benign SELinux `SystemReadParam` failures for
  `persist.init.debug.loglevel` (system-level, unrelated to the app).

Note: `hilog-fatal-scan.txt` contains only the shell error from the failed
`findstr` invocation on the capture host (`/bin/sh: findstr: inaccessible or
not found`); the actual scan was re-run locally with a Node script on the
preserved `hilog-full.txt` — results above. The app process was stable (no
crash, no restart) across the entire 08:39–08:45 session.

## Acceptance claims (all backed by preserved captures)

1. **Fixed header always reachable.** 治理(只读) / 整体刷新 / 服务地址 line /
   只读视图 notice appear in the accessibility tree in every captured state,
   including `governance-deepscrolled.json` where the Scroll is scrolled to the
   audit log. This is the runtime confirmation of the M13-14 structural fix
   (header outside Scroll).
2. **Refresh control enabled-sequence.** `_summary.cjs` (see the worktree
   history) parsed the 整体刷新 button `enabled` attribute across the captures:
   `governance-loaded`, `governance-error-mockdown`, `governance-recovered` all
   report `enabled: true` — the control is never stuck disabled after load or
   after error recovery.
3. **Read-only boundary honored.** The pane renders version / ops snapshot /
   audit log only; no payload content is shown; Settings saves only the base
   URL and explicitly does not persist accounts, tokens, or passwords
   (`settings-saved.json` notice text).
4. **Error + recovery path works.** Mock server stop → per-section error state
   (2300028 Operation timeout) with header intact; restart → full data
   restored (`governance-error-mockdown.json` → `governance-recovered.json`).
5. **No crashes.** Full hilog: zero FATAL/AppCrash/AppFreeze/JS_ERR; PID 3826
   stable 08:39–08:45.

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
3. Open the 治理 tab: observe 加载中… then version / ops snapshot / audit log.
4. Click 整体刷新, switch tabs and back, stop/restart the mock server
   (error → recovery), scroll to the bottom (audit log) — the fixed header
   stays visible in all states.
