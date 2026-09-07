"""The Rust and Python self-test readings agree.

The probes stay in Python. What is compared is the reading of their answers -
the part that can be wrong while everything still runs. A capability decoded
from the wrong bit reports a working helper as unprivileged; a polkit answer
read wrongly tells somebody their policy file is missing when it is installed
and doing its job.

`pkcheck` is run without `--allow-user-interaction`, so `auth_required` is a
HEALTHY answer here and the corpus says so.
"""

from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from tests._support import _SRC  # noqa: F401

from goblinmode import selftest

_REPO = Path(__file__).resolve().parent.parent

MASKS = [0, 1 << 21, 1 << 22, 1 << 23, 1 << 24,
         (1 << 21) | (1 << 23) | (1 << 24), (1 << 23) | (1 << 24),
         0xFFFFFFFFFFFFFFFF, 0x800000, 0x1000000, 0x200000, 1]

STATUSES = [
    {"status": "Name:\thelper\nCapEff:\t0000000000800000\n", "field": "CapEff"},
    {"status": "CapEff:\t0000000001800000\nCapPrm:\t00000000ffffffff\n", "field": "CapPrm"},
    {"status": "Name:\thelper\n", "field": "CapEff"},
    {"status": "", "field": "CapEff"},
    {"status": "NotCapEff:\t00ff\n", "field": "CapEff"},
    {"status": "CapEff has no colon 00ff\n", "field": "CapEff"},
    {"status": "CapEff:\tnothex\n", "field": "CapEff"},
    {"status": "CapEff:\tABCDEF\n", "field": "CapEff"},
    {"status": "CapEff:\tabcdef\n", "field": "CapEff"},
    {"status": "CapEff: 800000\n", "field": "CapEff"},
    {"status": "CapEff:800000\n", "field": "CapEff"},
    {"status": "prefix\nCapEff:\t00ff\nsuffix\n", "field": "CapEff"},
]

PKCHECKS = [
    {"installed": False, "code": 0, "output": ""},
    {"installed": False, "code": 1, "output": "auth_required"},
    {"installed": True, "code": 0, "output": ""},
    {"installed": True, "code": 0, "output": "auth_required"},
    {"installed": True, "code": 0, "output": "not registered"},
    {"installed": True, "code": 1, "output": "auth_required"},
    {"installed": True, "code": 2, "output": ""},
    {"installed": True, "code": 2, "output": "not registered"},
    {"installed": True, "code": 1, "output": "not registered"},
    {"installed": True, "code": 1, "output": "No such action"},
    {"installed": True, "code": 3, "output": "something went wrong\nand more\n"},
    # `pkcheck` output is another program\'s stderr. Python splits on more
    # than a newline, so where the first line ENDS can differ.
    {"installed": True, "code": 3, "output": "first\x0bsecond\n"},
    {"installed": True, "code": 3, "output": "first\x0csecond\n"},
    {"installed": True, "code": 3, "output": "first\x85second\n"},
    {"installed": True, "code": 3, "output": "first\u2028second\n"},
    {"installed": True, "code": 3, "output": "first\rsecond\n"},
    {"installed": True, "code": 7, "output": ""},
    {"installed": True, "code": 1, "output": ""},
]

WATTS = [None, 0, 45_000_000, 45_500_000, 7_250_000, 7_350_000, -1_000_000,
         1, 999_999, 500_000_000]

RYZENADJ = [
    "CPU Family\t| Renoir\nSTAPM LIMIT      | 25.000 | stapm limit\n",
    "STAPM LIMIT | 15 |\n",
    "STAPM LIMIT|7.5|\n",
    "nothing here",
    "",
    # Shapes the pattern accepts and `float` does not. A probe that raised
    # here would report "the probe itself failed" where it should report that
    # ryzenadj did.
    "STAPM LIMIT      | . | broken\n",
    "STAPM LIMIT | ... |\n",
    "STAPM LIMIT | 1.2.3 |\n",
    "STAPM LIMIT | |\n",
]


def _binary() -> Path | None:
    override = os.environ.get("GMP_SELFTEST_BIN")
    if override:
        return Path(override) if Path(override).exists() else None
    for profile in ("debug", "release"):
        candidate = _REPO / "target" / profile / "examples" / "selftest"
        if candidate.exists():
            return candidate
    return None


def _python_cap_set(status: str, field: str):
    with patch.object(selftest, "_read", lambda _p: status):
        return selftest._read_cap_set(1234, field)


def _python_pkcheck(case) -> list:
    with patch.object(selftest.shutil, "which",
                      lambda _t: "/usr/bin/pkcheck" if case["installed"] else None), \
            patch.object(selftest, "_run",
                         lambda _cmd, **kw: (case["code"], case["output"])):
        return list(selftest._pkcheck("com.goblinmode.pro.manage-performance"))


def _python_stapm(output: str):
    return selftest._parse_stapm(output)


class BothImplementationsReadTheSame(unittest.TestCase):
    def setUp(self):
        self.binary = _binary()
        if self.binary is None:
            if os.environ.get("GMP_REQUIRE_RUST_HELPER") == "1":
                self.fail("GMP_REQUIRE_RUST_HELPER=1 but the selftest example "
                          "is not built - run `cargo build -p gmp-core "
                          "--example selftest`")
            self.skipTest("build it with `cargo build -p gmp-core "
                          "--example selftest`")

    def _rust(self, payload: dict) -> dict:
        r = subprocess.run([str(self.binary)], input=json.dumps(payload),
                           capture_output=True, text=True, timeout=60,
                           check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_every_mask_names_the_same_capabilities(self):
        got = self._rust({"masks": MASKS})["caps"]
        for mask, answer in zip(MASKS, got, strict=True):
            with self.subTest(mask=hex(mask)):
                self.assertEqual(answer, selftest._decode_caps(mask))

    def test_every_status_field_is_read_the_same(self):
        got = self._rust({"statuses": STATUSES})["sets"]
        for case, answer in zip(STATUSES, got, strict=True):
            with self.subTest(status=case["status"][:24], field=case["field"]):
                self.assertEqual(answer,
                                 _python_cap_set(case["status"], case["field"]))

    def test_every_polkit_answer_is_read_the_same(self):
        got = self._rust({"pkchecks": PKCHECKS})["pkchecks"]
        for case, answer in zip(PKCHECKS, got, strict=True):
            with self.subTest(**case):
                self.assertEqual(answer, _python_pkcheck(case))

    def test_a_zero_exit_wins_over_the_output(self):
        """Named on its own: reading the text first would report an already
        authorized session as one that still needs a prompt."""
        case = {"installed": True, "code": 0, "output": "auth_required"}
        self.assertEqual(self._rust({"pkchecks": [case]})["pkchecks"][0][0], "yes")
        self.assertEqual(_python_pkcheck(case)[0], "yes")

    def test_every_wattage_prints_the_same(self):
        got = self._rust({"watts": WATTS})["watts"]
        for uw, answer in zip(WATTS, got, strict=True):
            with self.subTest(uw=uw):
                self.assertEqual(answer, selftest._w(uw))

    def test_every_ryzenadj_table_is_read_the_same(self):
        got = self._rust({"ryzenadj": RYZENADJ})["ryzenadj"]
        for output, answer in zip(RYZENADJ, got, strict=True):
            with self.subTest(output=output[:24]):
                self.assertEqual(answer, _python_stapm(output))


if __name__ == "__main__":
    unittest.main()
