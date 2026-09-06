"""The Rust raise plan and what the Python daemon actually does agree.

`Daemon._raise_incident` files an incident and then makes two decisions about
it: whether the last thirty seconds are worth keeping, and whether the user is
worth interrupting. The two lists of kinds overlap without matching, which is
the part a plausible-looking rewrite gets wrong - a frame-rate dip is worth
watching back and not worth a popup, and VRAM left behind after a game exits is
worth saying out loud with nothing to watch.

Recorded at the seams where the Python acts, like the game-event harness: the
log it files to, the bus it emits on, the thread it starts to save a clip, and
the notification with its urgency and tag.
"""

from __future__ import annotations

import itertools
import json
import os
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from tests._support import _SRC  # noqa: F401

from goblinmode.daemon import Daemon

_REPO = Path(__file__).resolve().parent.parent

KINDS = ("thermal_throttle", "power_limit", "gpu_throttle", "gpu_fault",
         "fps_dip", "fps_recovered", "vram_not_freed")

DETAILS = (
    "",
    "brief",
    # Either side of the 160-character cut.
    "x" * 160,
    "x" * 400,
    # The cut is by CHARACTER. A byte slice would cut this one short, and cut
    # it inside a character it would fail outright.
    "é" * 400,
    "GPU hung \U0001f525 " + "é" * 300,
)


def _binary() -> Path | None:
    override = os.environ.get("GMP_RAISE_BIN")
    if override:
        return Path(override) if Path(override).exists() else None
    for profile in ("debug", "release"):
        candidate = _REPO / "target" / profile / "examples" / "raise"
        if candidate.exists():
            return candidate
    return None


class _Sample:
    """One diagnostics sample, as much of one as this path reads."""

    def as_dict(self):
        return {"t": 0.0}


class _Diag:
    def recent(self, _n):
        return [_Sample()]


class _Status:
    def as_dict(self):
        return {}


class _Payload:
    def status(self):
        return _Status()


class _LogWatch:
    def tail_tail(self):
        return []


class _Observer:
    def __init__(self, active_exes):
        self.active_exes = active_exes


class _Thread:
    """Run inline, so the order work is started in is the order recorded."""

    def __init__(self, target=None, args=(), **kw):
        self._target = target
        self._args = args

    def start(self):
        self._target(*self._args)


def _python_plan(kind, detail, clip_running, active_exes, pids) -> list:
    log: list = []

    class _Incidents:
        def add(self, incident):
            log.append(["File", [incident.game, incident.game_pid]])

    class _Bridge:
        def emit_incident(self, _payload):
            log.append(["Emit", []])

    class _Clip:
        def running(self):
            return clip_running

    daemon = Daemon.__new__(Daemon)
    daemon.observer = _Observer(list(active_exes))
    daemon._active_pids = dict(zip(active_exes, pids, strict=False))
    daemon.diag = _Diag()
    daemon.logwatch = _LogWatch()
    daemon.payload = _Payload()
    daemon.incidents = _Incidents()
    daemon.bridge = _Bridge()
    daemon.clip = _Clip()
    daemon._save_clip = lambda k: log.append(["SaveClip", [k]])
    daemon._notify = lambda title, body="", *, urgency=1, tag="status": log.append(
        ["Notify", [title, body, urgency, tag]])

    with patch("goblinmode.daemon.threading.Thread", _Thread):
        daemon._raise_incident(kind, detail)
    return log


class BothImplementationsAgree(unittest.TestCase):
    def setUp(self):
        self.binary = _binary()
        if self.binary is None:
            if os.environ.get("GMP_REQUIRE_RUST_HELPER") == "1":
                self.fail("GMP_REQUIRE_RUST_HELPER=1 but the raise example is "
                          "not built - run `cargo build -p gmp-core "
                          "--example raise`")
            self.skipTest("build it with `cargo build -p gmp-core "
                          "--example raise`")

    def _rust(self, kind, detail, clip_running, active_exes, pids) -> list:
        payload = {"kind": kind, "detail": detail,
                   "clip_running": clip_running,
                   "active_exes": list(active_exes), "pids": list(pids)}
        r = subprocess.run([str(self.binary)], input=json.dumps(payload),
                           capture_output=True, text=True, timeout=60,
                           check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_every_kind_and_detail_agrees(self):
        for kind, detail, clip in itertools.product(KINDS, DETAILS,
                                                    (False, True)):
            with self.subTest(kind=kind, clip=clip, detail=detail[:12]):
                exes, pids = ["Wow.exe"], [4242]
                self.assertEqual(
                    self._rust(kind, detail, clip, exes, pids),
                    _python_plan(kind, detail, clip, exes, pids))

    def test_what_the_incident_is_filed_against_agrees(self):
        for exes, pids in (
            ([], []),
            (["Wow.exe"], [4242]),
            (["Wow.exe", "rs2client"], [4242, 99]),
            # A game whose pid the observer never learned.
            (["Wow.exe"], [0]),
            (["Wow.exe", "rs2client"], [0, 99]),
        ):
            with self.subTest(exes=exes, pids=pids):
                self.assertEqual(
                    self._rust("gpu_fault", "d", False, exes, pids),
                    _python_plan("gpu_fault", "d", False, exes, pids))


if __name__ == "__main__":
    unittest.main()
