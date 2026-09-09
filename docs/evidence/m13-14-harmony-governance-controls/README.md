# M13-14 Harmony Governance Refresh Control Hardening

## Behavior

The governance pane now disables refresh controls while their requests are in flight:

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

## Boundaries

This is local UI hardening only. Runtime evidence remains mock-only, the HAP is unsigned, no physical HarmonyOS device was used, and no AGC signing material, real provider, production backend, production database, or write path was exercised. `production_ready` remains false.
