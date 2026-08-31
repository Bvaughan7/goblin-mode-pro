"""The AMD (`ryzenadj`) TDP paths, which no test had ever touched.

Nobody involved has an AMD laptop, so these paths shipped on reasoning alone.
That is exactly the situation `selftest` exists to make visible - and auditing
them for these tests turned up a real bug: `set_tdp` raises the *fast* (burst)
limit to stapm+8 W, but the snapshot only recorded STAPM, so `reset_tdp` put
the fast limit back to the *sustained* value. A machine shipping stapm=25 W /
fast=30 W silently lost 5 W of burst headroom after any set/reset cycle, and
kept losing it until the next reboot.

`ryzenadj` itself is faked: these test our parsing, snapshotting and restore
logic, which is where that bug lived. They do not prove ryzenadj talks to real
silicon - only an AMD machine can, and `selftest --apply` is what asks it.
"""

from __future__ import annotations

import json
import logging
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

_HELPER_DIR = Path(__file__).resolve().parent.parent / "helper"
if str(_HELPER_DIR) not in sys.path:
    sys.path.insert(0, str(_HELPER_DIR))

import goblin_helper as gh

# the helper logs at INFO on every write; keep the suite output readable
logging.getLogger("goblin-helper").setLevel(logging.CRITICAL)

#: a real `ryzenadj --info` table, trimmed
_INFO = """CPU Family: Renoir
SMU BIOS Interface Version: 15
PM Table Version: 370005
|        Name         |   Value   |     Parameter      |
|---------------------|-----------|--------------------|
| STAPM LIMIT         |   25.000  | stapm-limit        |
| STAPM VALUE         |   12.345  | stapm-value        |
| PPT LIMIT FAST      |   30.000  | fast-limit         |
| PPT VALUE FAST      |   14.000  | fast-value         |
| PPT LIMIT SLOW      |   25.000  | slow-limit         |
| THM LIMIT CORE      |   95.000  | tctl-temp          |
"""


class InfoParsing(unittest.TestCase):
    def test_reads_each_limit_in_milliwatts(self):
        self.assertEqual(gh._parse_ryzenadj_row(_INFO, "STAPM LIMIT"), 25_000)
        self.assertEqual(gh._parse_ryzenadj_row(_INFO, "PPT LIMIT FAST"), 30_000)
        self.assertEqual(gh._parse_ryzenadj_row(_INFO, "PPT LIMIT SLOW"), 25_000)

    def test_does_not_confuse_a_limit_with_its_current_value(self):
        """`STAPM VALUE` sits next to `STAPM LIMIT` and means something else."""
        self.assertEqual(gh._parse_ryzenadj_row(_INFO, "STAPM LIMIT"), 25_000)
        self.assertNotEqual(gh._parse_ryzenadj_row(_INFO, "STAPM LIMIT"), 12_345)

    def test_accepts_a_table_already_in_milliwatts(self):
        mw = _INFO.replace("25.000", "25000.000")
        self.assertEqual(gh._parse_ryzenadj_row(mw, "STAPM LIMIT"), 25_000)

    def test_a_missing_row_is_none_not_a_crash(self):
        self.assertIsNone(gh._parse_ryzenadj_row(_INFO, "NO SUCH ROW"))
        self.assertIsNone(gh._parse_ryzenadj_row("", "STAPM LIMIT"))

    def test_a_zero_limit_is_not_believed(self):
        self.assertIsNone(
            gh._parse_ryzenadj_row(_INFO.replace("25.000", "0.000"), "STAPM LIMIT"))


class _FakeRyzenadj:
    """Records the flags ryzenadj was called with."""

    def __init__(self, info=_INFO):
        self.info = info
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, *args: str) -> str:
        self.calls.append(args)
        return self.info if args == ("--info",) else ""

    def flags(self) -> dict[str, int]:
        """The last write call's flags, as {flag: value}."""
        writes = [c for c in self.calls if c != ("--info",)]
        if not writes:
            return {}
        return {a.split("=")[0].lstrip("-"): int(a.split("=")[1]) for a in writes[-1]}


class TdpSnapshotAndRestore(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        d = Path(self._tmp.name)
        self._orig = (gh.STATE_DIR, gh.STATE_FILE, gh.RYZENADJ, gh._ryzenadj)
        gh.STATE_DIR = d
        gh.STATE_FILE = d / "state.json"
        gh.RYZENADJ = "/usr/bin/ryzenadj"
        self.fake = _FakeRyzenadj()
        gh._ryzenadj = self.fake

    def tearDown(self):
        gh.STATE_DIR, gh.STATE_FILE, gh.RYZENADJ, gh._ryzenadj = self._orig
        self._tmp.cleanup()

    def _state(self) -> dict:
        return json.loads(gh.STATE_FILE.read_text())

    def test_the_snapshot_records_every_limit_not_just_stapm(self):
        gh.set_tdp(35)
        limits = self._state()["ryzenadj_limits_mw"]
        self.assertEqual(limits["stapm-limit"], 25_000)
        self.assertEqual(limits["fast-limit"], 30_000)
        self.assertEqual(limits["slow-limit"], 25_000)

    def test_setting_a_tdp_writes_the_asked_for_watts(self):
        gh.set_tdp(35)
        flags = self.fake.flags()
        self.assertEqual(flags["stapm-limit"], 35_000)
        self.assertEqual(flags["slow-limit"], 35_000)
        self.assertEqual(flags["fast-limit"], 43_000)   # +8 W burst headroom

    def test_reset_restores_each_limit_to_its_own_original(self):
        """The regression. Restoring all three to STAPM clamps burst headroom."""
        gh.set_tdp(35)
        gh.reset_tdp()
        flags = self.fake.flags()
        self.assertEqual(flags["stapm-limit"], 25_000)
        self.assertEqual(flags["slow-limit"], 25_000)
        self.assertEqual(
            flags["fast-limit"], 30_000,
            "fast-limit must go back to the machine's own 30 W, not to STAPM")

    def test_a_second_set_does_not_overwrite_the_snapshot(self):
        gh.set_tdp(35)
        self.fake.info = _INFO.replace("25.000", "35.000")   # machine moved
        gh.set_tdp(45)
        self.assertEqual(self._state()["ryzenadj_limits_mw"]["stapm-limit"], 25_000)

    def test_a_snapshot_from_an_older_helper_still_restores(self):
        """Upgrading the helper under a running daemon must not strand it."""
        gh.STATE_FILE.write_text(json.dumps({"ryzenadj_stapm_mw": 25_000}))
        self.assertTrue(gh.reset_tdp())
        self.assertEqual(self.fake.flags()["stapm-limit"], 25_000)

    def test_reset_with_no_snapshot_is_a_no_op_not_a_failure(self):
        self.assertTrue(gh.reset_tdp())
        self.assertEqual(self.fake.flags(), {})

    def test_requested_watts_are_clamped_to_the_supported_range(self):
        gh.set_tdp(9999)
        self.assertLessEqual(self.fake.flags()["stapm-limit"], gh.TDP_MAX_W * 1000)
        self.fake.calls.clear()
        gh.set_tdp(1)
        self.assertGreaterEqual(self.fake.flags()["stapm-limit"], gh.TDP_MIN_W * 1000)

    def test_no_ryzenadj_means_a_clean_false_not_an_exception(self):
        gh.RYZENADJ = None
        self.assertFalse(gh.set_tdp(35))
        self.assertTrue(gh.reset_tdp())


if __name__ == "__main__":
    unittest.main()
