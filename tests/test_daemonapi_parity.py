"""The Rust and Python API-layer decisions agree.

`daemon_api` mostly forwards. Three things are its own, and each changes what
somebody gets back from a method they pressed a button for: how much of a log
the analyser reads, which game's AppID it is analysed against, and how an
incident is rebuilt from the on-disk history when there is no live one.

That last is where the defaults matter, and they are not interchangeable. A
kind is switched on by every reader, a detail is prose, and a missing pid is
missing rather than process zero.
"""

from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path

from tests._support import _SRC  # noqa: F401

from goblinmode.incidents import Incident

_REPO = Path(__file__).resolve().parent.parent

SIZES = [0, 1, 1_000, 199_999, 200_000, 200_001, 5_000_000, 10 ** 12]

ACTIVES = [
    {"active": [], "appids": {}},
    {"active": ["Wow.exe"], "appids": {}},
    {"active": ["Wow.exe"], "appids": {"Wow.exe": "1091500"}},
    {"active": ["Wow.exe", "rs2client"],
     "appids": {"rs2client": "1343400"}},
    {"active": ["Wow.exe", "rs2client"],
     "appids": {"Wow.exe": "1091500", "rs2client": "1343400"}},
    {"active": ["blank", "rs2client"],
     "appids": {"blank": "", "rs2client": "1343400"}},
    {"active": ["rs2client", "blank"],
     "appids": {"blank": "", "rs2client": "1343400"}},
]

ROWS = [
    {},
    {"kind": "gpu_fault"},
    {"kind": "gpu_fault", "detail": "device lost", "game": "Wow.exe",
     "game_pid": 4242, "metrics_window": [{"t": 1}], "logs_tail": ["a"],
     "active_tweaks": {"governor": "performance"}, "ts": "ignored"},
    {"kind": None},
    {"game_pid": None},
    {"detail": "", "game": ""},
    {"metrics_window": [], "logs_tail": [], "active_tweaks": {}},
]


def _binary() -> Path | None:
    override = os.environ.get("GMP_DAEMONAPI_BIN")
    if override:
        return Path(override) if Path(override).exists() else None
    for profile in ("debug", "release"):
        candidate = _REPO / "target" / profile / "examples" / "daemonapi"
        if candidate.exists():
            return candidate
    return None


def _python_from(size: int) -> int:
    """`fh.seek(max(0, fh.tell() - 200_000))`, with tell() at the end."""
    return max(0, size - 200_000)


def _python_appid(case) -> str:
    appid = ""
    for exe in case["active"]:
        value = case["appids"].get(exe)
        if value:
            appid = value
            break
    return appid


def _python_incident(row) -> dict:
    """The projection `export_last_incident` builds from a history row."""
    incident = Incident(
        kind=row.get("kind", "unknown"),
        detail=row.get("detail", ""),
        game=row.get("game", ""),
        game_pid=row.get("game_pid"),
        metrics_window=row.get("metrics_window", []),
        logs_tail=row.get("logs_tail", []),
        active_tweaks=row.get("active_tweaks", {}),
    )
    return {"kind": incident.kind, "detail": incident.detail,
            "game": incident.game, "game_pid": incident.game_pid,
            "metrics_window": incident.metrics_window,
            "logs_tail": incident.logs_tail,
            "active_tweaks": incident.active_tweaks}


class BothImplementationsDecideTheSame(unittest.TestCase):
    def setUp(self):
        self.binary = _binary()
        if self.binary is None:
            if os.environ.get("GMP_REQUIRE_RUST_HELPER") == "1":
                self.fail("GMP_REQUIRE_RUST_HELPER=1 but the daemonapi example "
                          "is not built - run `cargo build -p gmp-core "
                          "--example daemonapi`")
            self.skipTest("build it with `cargo build -p gmp-core "
                          "--example daemonapi`")

    def _rust(self, payload: dict) -> dict:
        r = subprocess.run([str(self.binary)], input=json.dumps(payload),
                           capture_output=True, text=True, timeout=60,
                           check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_every_log_is_read_from_the_same_offset(self):
        got = self._rust({"sizes": SIZES})["froms"]
        for size, answer in zip(SIZES, got, strict=True):
            with self.subTest(size=size):
                self.assertEqual(answer, _python_from(size))

    def test_the_same_game_supplies_the_app_id(self):
        got = self._rust({"actives": ACTIVES})["appids"]
        for case, answer in zip(ACTIVES, got, strict=True):
            with self.subTest(**case):
                self.assertEqual(answer, _python_appid(case))

    def test_every_history_row_rebuilds_the_same_incident(self):
        got = self._rust({"rows": ROWS})["incidents"]
        for row, answer in zip(ROWS, got, strict=True):
            with self.subTest(row=row):
                self.assertEqual(answer, _python_incident(row))

    def test_a_present_null_is_not_an_absent_field(self):
        """`.get(key, default)` hands back the null it found; only a missing
        key takes the default. A kind of null and a kind that is not there
        are different rows."""
        got = self._rust({"rows": [{"kind": None}, {}]})["incidents"]
        self.assertIsNone(got[0]["kind"])
        self.assertEqual(got[1]["kind"], "unknown")
        self.assertEqual(got, [_python_incident({"kind": None}),
                               _python_incident({})])


if __name__ == "__main__":
    unittest.main()
