# M13-11 Harmony Governance Read-Only Pane — Evidence

## Scope

Adds the sixth Harmony tab `治理` and `GovernancePane`.
- Read-only version / ops snapshot / audit sections.
- Existing APIs only: `getVersion`, `getOpsSnapshot`, `getAudit`.
- All three are GET requests; no write operation, token/key/password storage, AGC operation, or signing material.
- Audit payload `before` / `after` content is not rendered.

## Static / Implementation Facts

- Independent per-section state machines with loading/empty/success/error states.
- Independent refresh/retry actions.
- Request-generation guards and disposed guards reject stale/late responses.
- URL-change event subscription refreshes the pane and is unsubscribed on dispose.
- No unrelated tabs or production behavior changed.

## Refresh Verification (2026-09-13, base `cc782b0`)

Single-commit replay of the slice onto current main; pane code unchanged.

- Rebase: `e117bd6` (2026-09-09, base `984539e`) replayed onto `cc782b0` cleanly — no conflicts; the 54 intervening main commits touch no `apps/harmony` file (only `apps/web` and Python/docs/infra). Lossless: `git diff e117bd6 HEAD -- <3 slice files>` is empty.
- Semantic re-check against current main:
  - `AiosApi.ets` on main still exports `getVersion` / `getOpsSnapshot` / `getAudit` and `VersionData` / `OpsSnapshotData` / `AuditEntry` — pane reuses them, no new backend contract.
  - Backend routes present on main: `/api/v1/version` (`routes/version.py`), `/api/v1/system/ops-snapshot` (`routes/system.py`), `/api/v1/audit` (`routes/audit.py`).
  - `Index.ets` wiring stays additive: sixth `治理` tab appended after 设置; existing five tabs and their behavior untouched; the StudyPane exams comment added by this slice remains accurate on main.
  - Read-only re-audit: all 7 `onClick` handlers in `GovernancePane.ets` are refresh/retry only (`refreshAll` / `loadVersion` / `loadOpsSnapshot` / `loadAudit`); no POST/PUT/PATCH/DELETE anywhere in the pane.
- Tests (canonical venv `D:/AI Learning OS/ai-learning-os/.venv`, Python 3.11.15, pytest 9.1.1, executed in this worktree):
  - `python tools/harmony_mock/test_contract.py` → **54/54 passed**.
  - `python -m pytest tests/android_smoke/test_mock_contract.py tests/android_smoke/test_server.py` → **36 passed**.
  - `python -m pytest tests/harmony_release` (the Harmony gate main CI runs) → **35 passed**.
  - `python -m pytest services/api/tests/test_admin_audit.py services/api/tests/test_ops_snapshot.py services/api/tests/test_auth.py` (backend suites behind the pane's three GETs) → **56 passed**.
- Build (DevEco Studio hvigor at `C:/DevEco-Studio`, `--daemon=false`): `clean` exit 0; `assembleHap` exit 0 — only expected warning `No signingConfig found for product default`.
- Refresh HAP: `entry-default-unsigned.hap`, **513486 bytes**, SHA256 **`3C2251BB76A2E46C253CFE76008859B222D72803CF0281528F6DD660DC2C0671`** (same byte size as the 2026-09-09 build; hash differs because HAP archives embed build timestamps).

### Refresh Non-Claims

- No emulator/device runtime validation in this refresh — the 2026-09-09 runtime evidence below was not re-run.
- CI had not been re-run on the refreshed head at the time of this README.
- No fresh-install factory-default URL retest (same gap as the original session).
- Environment note: bare `python` on this host resolves to a non-functional WindowsApps stub; the canonical venv above was used for every Python run.

## Original Slice Verification (2026-09-09, base `984539e`)

- `python tools/harmony_mock/test_contract.py`: **54/54 passed** (independently rerun by supervisor after rebase).
- Canonical venv pytest `tests/android_smoke/test_mock_contract.py tests/android_smoke/test_server.py`: **36 passed**.
- Clean + assembleHap after rebase: both exit 0; only expected warning `No signingConfig found for product default`.
- Final HAP: `entry-default-unsigned.hap`, **513486 bytes**, SHA256 **`C88DE2347B7A9785A8DBFFA233AED64E385E51D7D263D24A443EA97893FBEA70`**.
- Local emulator target `127.0.0.1:5557`, app PID remained **13052** through URL save, governance rendering, mock-down error, and mock recovery.
- Persisted URL was initially `http://192.168.8.3:8766/`, not factory default `http://127.0.0.1:8000/`; A01 honestly validated the resulting error state. A fresh-install factory-default test was not performed.
- Real Settings UI saved `http://192.168.8.3:8765/`; layout text showed `已保存: http://192.168.8.3:8765/`.
- Governance success evidence:
  - version `0.12.5-mock`
  - ops snapshot `postgresql`, papers `34`, search queries `456`, audit total `1024`
  - audit records `#1001 paper.publish` and `#1002 worker.tick`
- Mock-down/recovery:
  - verified listener PID `64080` and command before stopping only that process
  - port `8765` listeners became zero
  - audit refresh reached `网络请求失败: 2300028 Operation timeout` with retry
  - mock restarted hidden as PID `81444`
  - audit section refresh recovered to real records while app PID stayed `13052`
- Forbidden marker scan on success/recovery layouts found none of:
  `M12_05_BEFORE_SHOULD_NOT_RENDER`, `mock-before-1001`, `mock-after`, `before payload`, `after payload`.

## Explicit Non-Claims

- No AGC signing or signed HAP.
- No physical HarmonyOS device validation.
- No production backend/database/provider validation.
- Mock-only runtime validation (2026-09-09 session only; not re-run in the 2026-09-13 refresh).
- No HarmonyOS remote CI job.
- No push/PR/merge status prewritten.
- Default URL on a fresh install was not retested.

## Raw Evidence Pointer

`.verify/m13-11-harmony-governance-readonly/` is gitignored and authoritative for raw logs/layout/screenshot evidence (2026-09-09 session; the 2026-09-13 refresh produced build output under `apps/harmony/entry/build/`, also gitignored).
