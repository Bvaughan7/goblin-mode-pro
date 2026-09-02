# Tests

Plain `unittest` — the only import beyond the stdlib is `psutil` (which a few of
the modules under test pull in at load time):

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

| `test_dbus_interface_freeze.py` | the helper serves the frozen v1 D-Bus contract, byte for byte |

GUI modules are import-checked in CI (`.github/workflows/ci.yml`), not unit
tested.

## `conformance/` — the cross-implementation suite

`tests/conformance/helper.py` is **not** part of the unittest suite. It talks
to whichever helper is on the system bus and asserts the frozen contract from
outside, so the same script verifies the Python helper today and a Rust one
later. That is what makes it possible to swap either implementation under a
caller that does not know which language answered.

```sh
python3 tests/conformance/helper.py            # read-only + rejections
python3 tests/conformance/helper.py --apply    # + governor round-trip
sudo python3 tests/conformance/helper.py --polkit-routing
python3 tests/conformance/helper.py --json     # results + capability report
```

Run it as **your own user, from your desktop session** — not under `sudo`.
Every mutating method is authorized through polkit, and the policy grants
`manage-performance` to an *active local session* with no prompt. A sudo shell
is not one, so under sudo the suite measures nothing but its own denial.
`--polkit-routing` is the exception: it eavesdrops the bus to read the action
id out of the helper's own `CheckAuthorization` call, which needs root, and it
expects to be denied afterwards.

Two checks cannot be observed from a single run, and say so rather than
guessing: the `Renice` ownership gate is skipped for uid 0 (the helper skips
the check itself for a root caller), and the snapshot schema is skipped for a
non-root caller (`/run/goblin-mode-pro` is 0700 by design).

The frozen contract is [`docs/dbus-interface-v1.xml`](../docs/dbus-interface-v1.xml).
CI enforces it in the `interface-freeze` job.
