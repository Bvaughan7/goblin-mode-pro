"""The Rust frame-rate-event plan and what the Python daemon does agree.

`Daemon._on_fps_event` decides two things that are easy to write plausibly and
wrongly. Which events are worth remembering - only a dip classified REAL, so
that the exit plan asks the GPU whether it let go once the game is gone - and
what window a dip is classified against.

That window is the interesting half. It is the WORST moment in the last few
seconds rather than the last sample or an average, because a dip is caused by a
spike and a spike is invisible in either. And its three fields do not treat a
missing reading alike: a sample with no per-core readings is skipped, while a
sample with no disk reading counts as zero. So a window that never read the
disk reports "no disk activity" where an empty window reports nothing at all.

The window becomes observable at `describe_dip`'s arguments, which is where
this records it.
"""

from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from tests._support import _SRC  # noqa: F401

from goblinmode.daemon import Daemon
from goblinmode.diagnostics import Sample
from goblinmode.fpswatch import FpsEvent

_REPO = Path(__file__).resolve().parent.parent


def _binary() -> Path | None:
    override = os.environ.get("GMP_FPSEVENT_BIN")
    if override:
        return Path(override) if Path(override).exists() else None
    for profile in ("debug", "release"):
        candidate = _REPO / "target" / profile / "examples" / "fpsevent"
        if candidate.exists():
            return candidate
    return None


#: (cpu_load, per_core, disk_read_mbps) per sample.
WINDOWS = (
    [],
    [(0.0, [], None)],
    [(50.0, [], None)],
    [(1.0, [1.0], None)],
    [(1.0, [1.0], 0.0)],
    [(10.0, [5.0, 90.0], 1.0), (80.0, [70.0, 60.0], 400.0),
     (20.0, [10.0, 10.0], 3.0)],
    [(10.0, [99.0, 1.0], None), (90.0, [90.0, 90.0], None)],
    [(50.0, [], None), (10.0, [42.0], 7.0)],
    # A window read at different moments, some of it missing.
    [(0.0, [0.0], None), (0.0, [], 12.5), (0.0, [0.0, 0.0], None)],
)

RECOVERIES = ((143.7, 12.4), (2.5, 3.5), (0.0, 0.0), (59.4, 0.5), (60.0, 119.9))


def _samples(window):
    return [Sample(t=float(i), cpu_temp=None, cpu_load=load,
                   per_core=list(cores), pkg_power_w=None, pl1_w=None,
                   pl2_w=None, gpu_load=None, gpu_temp=None,
                   gpu_throttle_reasons="", cpu_throttled=False,
                   disk_read_mbps=disk)
            for i, (load, cores, disk) in enumerate(window)]


class _Recording(Daemon):
    """A daemon that notices being told a real dip happened.

    `_fps_dip_seen` is a plain attribute, and its assignment is a step in the
    plan rather than a side effect to check afterwards - so it is recorded in
    the order it happens, like everything else here.
    """

    def __setattr__(self, name, value):
        if name == "_fps_dip_seen" and value:
            self._log.append(["RememberDip", []])
        object.__setattr__(self, name, value)


class _GpuMonitor:
    def deep(self, force=False):
        return {}


def _python_plan(kind, *, fps=0.0, baseline=0.0, duration_s=0.0,
                 window=(), detail="d", real=True) -> list:
    log: list = []

    class _Diag:
        def recent(self, _n):
            return _samples(window)

    daemon = _Recording.__new__(_Recording)
    object.__setattr__(daemon, "_log", log)
    daemon.diag = _Diag()
    daemon.gpu_monitor = _GpuMonitor()
    daemon.fpswatch = type("_W", (), {"recent_trace": lambda self: []})()
    daemon._raise_incident = lambda kind, detail, **kw: log.append(
        ["Raise", [kind, detail]])

    def describe_dip(_state, *, fps, baseline, cpu_load, disk_read,
                     cpu_core_max):
        log.append(["DipContext", [cpu_load, cpu_core_max, disk_read]])
        return detail, real

    with patch("goblinmode.daemon.threading.Thread", _Thread), \
            patch("goblinmode.daemon.GLib.idle_add", lambda fn: fn()), \
            patch("goblinmode.daemon.gpu.describe_dip", describe_dip):
        daemon._on_fps_event(FpsEvent(kind=kind, fps=fps, baseline=baseline,
                                      duration_s=duration_s))
    return log


class _Thread:
    def __init__(self, target=None, args=(), **kw):
        self._target = target
        self._args = args

    def start(self):
        self._target(*self._args)


class BothImplementationsAgree(unittest.TestCase):
    def setUp(self):
        self.binary = _binary()
        if self.binary is None:
            if os.environ.get("GMP_REQUIRE_RUST_HELPER") == "1":
                self.fail("GMP_REQUIRE_RUST_HELPER=1 but the fpsevent example "
                          "is not built - run `cargo build -p gmp-core "
                          "--example fpsevent`")
            self.skipTest("build it with `cargo build -p gmp-core "
                          "--example fpsevent`")

    def _rust(self, payload: dict) -> list:
        r = subprocess.run([str(self.binary)], input=json.dumps(payload),
                           capture_output=True, text=True, timeout=60,
                           check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_every_recovery_reads_the_same(self):
        for fps, duration in RECOVERIES:
            with self.subTest(fps=fps, duration=duration):
                self.assertEqual(
                    self._rust({"kind": "recovered", "fps": fps,
                                "duration_s": duration}),
                    _python_plan("recovered", fps=fps, duration_s=duration))

    def test_every_window_is_summarised_the_same(self):
        for window in WINDOWS:
            for real in (False, True):
                with self.subTest(window=window, real=real):
                    samples = [{"t": float(i), "cpu_load": load,
                                "per_core": list(cores),
                                "disk_read_mbps": disk}
                               for i, (load, cores, disk) in enumerate(window)]
                    self.assertEqual(
                        self._rust({"kind": "dip", "detail": "why",
                                    "real": real, "samples": samples}),
                        _python_plan("dip", window=window, detail="why",
                                     real=real))


if __name__ == "__main__":
    unittest.main()
