# Tests

Stdlib `unittest` only — no third-party dependency, so it runs on the same
system Python the daemon uses:

```sh
python -m unittest discover -s tests
```

`pytest` also works if you have it (`pip install -e '.[test]'`), it just gives
nicer output.

The suite covers the pure logic that has no GTK or D-Bus dependency:

| File | Covers |
|---|---|
| `test_config.py` | `GameProfile` / `Settings` validation, clamping, corrupt-profile drop, save/load round-trip |
| `test_capabilities.py` | cpu-list parsing, `detect()` shape, core-layout consistency |
| `test_sessions.py` | MangoHud CSV parsing, percentiles, regression detection, `SessionTracker` lifecycle |
| `test_cpuset.py` | affinity target selection, pin/restore on the test process |
| `test_community.py` | slug sanitisation, host pinning, shareable-field allowlist |
| `test_runner.py` | profile resolution, env-var filtering, gamescope arg generation, wrapper safety |
| `test_mangohud.py` | managed-block round-trip, user-line preservation |
| `test_preflight.py` | check result shape, status values, sysctl drop-in text |

GUI modules are import-checked in CI (`.github/workflows/ci.yml`), not unit
tested.
