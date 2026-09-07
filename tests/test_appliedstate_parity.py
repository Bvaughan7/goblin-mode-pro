"""The Rust applied-state writer and the Python one agree, byte for byte.

The file is what stands between a daemon that dies badly and a machine left on
the performance governor with a compositor tearing hint set: a `--revert` from
a DIFFERENT process reads it and undoes what the dead one did. The reader half
has been ported for a while; this is the writer.

Compared as text rather than as parsed data. Nothing reads the file
positionally, so a re-ordered document would still revert correctly - but
during the cutover the two implementations will be writing and reading each
other's copies, and a file that differs anywhere except where the machine
differed is a diff somebody has to explain every time they look.

The corpus carries non-ASCII executable names on purpose. `json.dumps` escapes
them and `serde_json` does not, and a game's name is what goes in these fields.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests._support import _SRC  # noqa: F401

from goblinmode import payload as payload_module
from goblinmode.payload import PerformancePayload

_REPO = Path(__file__).resolve().parent.parent

RECORDS = [
    # Nothing applied - what a clean shutdown leaves behind.
    {},
    # Everything at once.
    {
        "active": ["Wow.exe", "rs2client"],
        "governor_applied": True,
        "power_applied": True,
        "power_backend": "rapl",
        "tearing_applied": True,
        "adaptive_sync_applied": True,
        "refresh_cap_applied": True,
        "focus_mode": True,
        "scx_applied": "lavd",
        "scx_previous": "bpfland",
        "reniced": {"Wow.exe": 4242},
        "compositor": {"tearing_active": True, "refresh_active": 60,
                       "saved_refresh_hz": 144, "x11_suspended": False},
    },
    # A game whose name is not ASCII, in every field that holds one.
    {
        "active": ["Pok\u00e9mon.exe", "\U0001f525.exe"],
        "reniced": {"Pok\u00e9mon.exe": 7, "\u2122.exe": 9},
        "compositor": {"output": "DP-1 \u2013 32\u2033"},
    },
    # The scheduler recorded but nothing else.
    {"scx_applied": "lavd", "scx_previous": None},
]


def _binary() -> Path | None:
    override = os.environ.get("GMP_APPLIEDSTATE_BIN")
    if override:
        return Path(override) if Path(override).exists() else None
    for profile in ("debug", "release"):
        candidate = _REPO / "target" / profile / "examples" / "appliedstate"
        if candidate.exists():
            return candidate
    return None


def _python_file(record: dict) -> str:
    """Drive the real `_write_applied_state` and read back what it wrote."""

    class _Compositor:
        def restore_state(self):
            return record.get("compositor", {})

    p = PerformancePayload.__new__(PerformancePayload)
    p._active = {exe: None for exe in record.get("active", [])}
    p._governor_applied = record.get("governor_applied", False)
    p._power_applied = record.get("power_applied", False)
    p._power_backend = record.get("power_backend")
    p._tearing_applied = record.get("tearing_applied", False)
    p._vrr_applied = record.get("adaptive_sync_applied", False)
    p._refresh_cap_applied = record.get("refresh_cap_applied", False)
    p._focus_applied = record.get("focus_mode", False)
    p._scx_applied = record.get("scx_applied")
    p._scx_previous = record.get("scx_previous")
    p._reniced = dict(record.get("reniced", {}))
    p.compositor = _Compositor()

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "applied.json"
        with patch.object(payload_module, "APPLIED_STATE_FILE", path), \
                patch.object(payload_module, "ensure_user_dirs", lambda: None):
            p._write_applied_state()
        return path.read_text()


class BothImplementationsWriteTheSameFile(unittest.TestCase):
    def setUp(self):
        self.binary = _binary()
        if self.binary is None:
            if os.environ.get("GMP_REQUIRE_RUST_HELPER") == "1":
                self.fail("GMP_REQUIRE_RUST_HELPER=1 but the appliedstate "
                          "example is not built - run `cargo build -p gmp-core "
                          "--example appliedstate`")
            self.skipTest("build it with `cargo build -p gmp-core "
                          "--example appliedstate`")

    def _rust(self, record: dict) -> str:
        r = subprocess.run([str(self.binary)], input=json.dumps(record),
                           capture_output=True, text=True, timeout=60,
                           check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_every_record_is_written_identically(self):
        for i, record in enumerate(RECORDS):
            with self.subTest(record=i):
                self.assertEqual(self._rust(record), _python_file(record))

    def test_what_the_writer_produces_is_what_the_reader_expects(self):
        """The two halves are ported separately and read by different
        processes. A drift between them is a revert that runs on every start
        or one that never runs at all."""
        for i, record in enumerate(RECORDS):
            with self.subTest(record=i):
                data = json.loads(self._rust(record))
                self.assertEqual(sorted(data["active"]),
                                 sorted(record.get("active", [])))
                self.assertEqual(data["compositor"],
                                 record.get("compositor", {}))


if __name__ == "__main__":
    unittest.main()
