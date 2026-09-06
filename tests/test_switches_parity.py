"""The Rust switch plans and what the Python daemon does agree.

Two switches that are not a game: the master toggle, and force-boost. Both make
the daemon do things it otherwise only does when a game starts or stops, and
both have the same exception running through them - a forced boost is the user
holding the machine up by hand, so the sampler that watches it is not torn down
by anything else being switched off.

The profile force-boost applies is compared field for field as well as the
plans. It is a DEFAULT profile under a reserved name, which means the switch
has no settings of its own: what it does to the machine is whatever a fresh
profile asks for, and a drift in either default is a change in what the switch
does.
"""

from __future__ import annotations

import dataclasses
import itertools
import json
import os
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from tests._support import _SRC  # noqa: F401

from goblinmode import config
from goblinmode.daemon import Daemon

_REPO = Path(__file__).resolve().parent.parent


def _binary() -> Path | None:
    override = os.environ.get("GMP_SWITCHES_BIN")
    if override:
        return Path(override) if Path(override).exists() else None
    for profile in ("debug", "release"):
        candidate = _REPO / "target" / profile / "examples" / "switches"
        if candidate.exists():
            return candidate
    return None


class _Observer:
    def __init__(self, active_exes):
        self.active_exes = active_exes

    def update_settings(self, _settings):
        pass


class _Pids(dict):
    def __init__(self, log):
        super().__init__({"Wow.exe": 4242})
        self._log = log

    def clear(self):
        self._log.append("ForgetEveryPid")
        super().clear()


def _daemon(log, *, forced_boost=False, active_exes=()):
    class _Payload:
        def revert_all(self):
            log.append("RevertAll")

        def apply(self, profile, pid):
            log.append("ApplyForced")
            log.append(("profile", dataclasses.asdict(profile), pid))

        def revert(self, profile):
            log.append("RevertForced")
            log.append(("revert-exe", profile.exe))

    daemon = Daemon.__new__(Daemon)
    daemon.settings = config.Settings(profiles=[])
    daemon.observer = _Observer(list(active_exes))
    daemon.payload = _Payload()
    daemon._active_pids = _Pids(log)
    daemon._forced_boost = forced_boost
    daemon._stop_diagnostics = lambda: log.append("StopDiagnostics")
    daemon._ensure_diagnostics_running = lambda: log.append("EnsureDiagnostics")
    daemon._broadcast_status = lambda: log.append("BroadcastStatus")
    return daemon


def _steps(log):
    return [entry for entry in log if isinstance(entry, str)]


def _python_master(enabled, forced_boost) -> list:
    log: list = []
    daemon = _daemon(log, forced_boost=forced_boost)
    with patch("goblinmode.config.save"):
        self_result = daemon.set_master_enabled(enabled)
    assert self_result is True
    return _steps(log)


def _python_force(on, games_running) -> tuple[list, dict]:
    log: list = []
    daemon = _daemon(log, active_exes=["Wow.exe"] if games_running else [])
    assert daemon.force_boost(on) is True
    applied = next((entry[1] for entry in log
                    if isinstance(entry, tuple) and entry[0] == "profile"), {})
    return _steps(log), applied


class BothImplementationsAgree(unittest.TestCase):
    def setUp(self):
        self.binary = _binary()
        if self.binary is None:
            if os.environ.get("GMP_REQUIRE_RUST_HELPER") == "1":
                self.fail("GMP_REQUIRE_RUST_HELPER=1 but the switches example "
                          "is not built - run `cargo build -p gmp-core "
                          "--example switches`")
            self.skipTest("build it with `cargo build -p gmp-core "
                          "--example switches`")

    def _rust(self, payload: dict):
        r = subprocess.run([str(self.binary)], input=json.dumps(payload),
                           capture_output=True, text=True, timeout=60,
                           check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_the_master_switch_agrees(self):
        for enabled, forced in itertools.product((False, True), repeat=2):
            with self.subTest(enabled=enabled, forced_boost=forced):
                self.assertEqual(
                    self._rust({"which": "master", "on": enabled,
                                "forced_boost": forced}),
                    _python_master(enabled, forced))

    def test_force_boost_agrees(self):
        for on, running in itertools.product((False, True), repeat=2):
            with self.subTest(on=on, games_running=running):
                steps, _ = _python_force(on, running)
                self.assertEqual(
                    self._rust({"which": "force", "on": on,
                                "games_running": running}),
                    steps)

    def test_the_profile_force_boost_applies_agrees_field_for_field(self):
        _, applied = _python_force(True, False)
        self.assertEqual(self._rust({"which": "profile"}), applied)

    def test_the_forced_boost_is_applied_without_a_pid(self):
        """There is no process behind it, and the payload's renice and
        core-pin steps are guarded on the pid rather than on the profile."""
        log: list = []
        daemon = _daemon(log)
        daemon.force_boost(True)
        pid = next(entry[2] for entry in log
                   if isinstance(entry, tuple) and entry[0] == "profile")
        self.assertIsNone(pid)

    def test_the_revert_side_is_identified_by_the_reserved_name(self):
        """Only `exe` is read on the way out - the payload matches the
        reserved name to know not to touch MangoHud."""
        log: list = []
        daemon = _daemon(log)
        daemon.force_boost(False)
        exe = next(entry[1] for entry in log
                   if isinstance(entry, tuple) and entry[0] == "revert-exe")
        self.assertEqual(exe, self._rust({"which": "profile"})["exe"])


if __name__ == "__main__":
    unittest.main()
