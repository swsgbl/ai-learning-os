# M13-14 Harmony Governance Refresh Control Hardening

## Behavior

The governance pane disables refresh controls while their requests are in flight:

- the top-level refresh control is disabled when any version, operations, or audit request is loading;
- each section refresh control is disabled while that section is loading;
- retry controls only render in their section error branch, so they cannot appear while that section is loading.

This prevents duplicate user-initiated requests without changing API calls, state transitions, layouts, permissions, persistence, or error handling.

## Verification

- `python tools/harmony_mock/test_contract.py`: 54/54 passed.
- Global DevEco `hvigorw.bat clean --no-daemon`: exit 0.
- Global DevEco `hvigorw.bat assembleHap --mode module -p product=default -p buildMode=debug --no-daemon`: exit 0, `BUILD SUCCESSFUL`.
- Build warning count: 1, exactly `No signingConfig found for product default`.
- `git diff --check`: passed.
- Final unsigned HAP: 514279 bytes.
- Final unsigned HAP SHA256: `0889DB2BE345F787F42ED9BC11B2E91C997F14A470BF725ACE167947EFB0C4CA`.

## Runtime reachability failure (scroll offset cannot be reset)

Follow-up on-device evidence (app PID 31014, running build from commit 678a2ea) showed the
top-level controls 治理(只读) / 整体刷新 became unreachable once the user scrolls the
governance Scroll down, because they lived inside the Scroll:

- `stage-b-scroll-top-retry`: a single synthetic swipe (`uinput -T -m 660 2000 660 500 300`)
  was accepted by the device but produced zero ArkUI layout movement (2/138 a11y nodes differ,
  clock only).
- `stage-b-touch-drag-reset`: a long-press touch drag also produced zero movement.
- `stage-b-selected-tab-click-reset`: clicking the already-selected governance tab produced
  zero movement.
- `stage-b-tab-cycle-reset`: switching to Settings and back remounts the governance content
  but preserves the prior scroll offset.

Conclusion: scroll-reset gestures cannot be relied on in this environment, so the top-level
controls must be structurally reachable instead.

## Structural fix

`apps/harmony/entry/src/main/ets/components/GovernancePane.ets` was minimally refactored:

- a fixed outer header (Column outside the Scroll) now contains 治理(只读), 整体刷新,
  the service URL line, the read-only boundary notice, and the context error (when present);
- the header participates in layout and the accessibility tree regardless of Scroll offset,
  because it is no longer inside the Scroll;
- only the three governance sections (service version / ops snapshot / audit log) remain
  inside the Scroll, which now takes the remaining height via `layoutWeight(1)` and clips
  its own content only;
- all state machines, per-section request generations, `disposed` guards, APIs, labels,
  error handling, and loading-disabled semantics from M13-14 are unchanged; no permissions,
  persistence, timers, retries, network calls, credentials, animations, or new
  route/component abstractions were added.

## Verification (structural fix build)

Commands (from `apps/harmony`, `hvigorw.bat` resolved to the global DevEco install at
`C:\DevEco-Studio\tools\hvigor\bin\hvigorw.bat`; the worktree has no wrapper script):

- `DEVECO_SDK_HOME=C:\DevEco-Studio\sdk; hvigorw.bat clean --no-daemon`: exit 0,
  `BUILD SUCCESSFUL in 1 s 255 ms`.
- `DEVECO_SDK_HOME=C:\DevEco-Studio\sdk; hvigorw.bat assembleHap --mode module -p product=default -p buildMode=debug --no-daemon`:
  exit 0, `BUILD SUCCESSFUL in 6 s 443 ms` (`:entry:default@CompileArkTS... after 3 s 270 ms`).
- Build warning count: 1, exactly `WARN: No signingConfig found for product default`.
- `python tools/harmony_mock/test_contract.py` (repository root): 54/54 passed.
- `git diff --check`: passed (no whitespace errors).
- Unsigned HAP path: `apps/harmony/entry/build/default/outputs/default/entry-default-unsigned.hap`.
- Unsigned HAP size: 515871 bytes.
- Unsigned HAP SHA256: `B4E29BD905113F6FA40FA3CA9AF6015675378ED8FA8C209CBCA7A244500C9289`.

## Boundary: not installed / not runtime-revalidated

This structural-fix build was **not** installed and **not** revalidated at runtime: the
currently running app instance (PID 31014) must remain untouched, so no install, restart,
stop, reinstall, uninstall, or data-clear was performed. The claim "header and top-level
refresh are laid out and exposed in the accessibility tree regardless of Scroll offset" is
therefore a source-structure argument (the header is outside the Scroll subtree), not a
re-measured a11y tree capture. The HAP remains unsigned; no AGC signing material, real
provider, production backend, production database, or write path was exercised.
`production_ready` remains false.
