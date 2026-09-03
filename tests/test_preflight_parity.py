"""The Rust and Python preflight decisions agree.

Two things are compared. The DECISIONS - each threshold from both sides, plus
the detail sentence, which the user actually reads. And the AGGREGATION - the
severity cap, the summary counts, and which sysctls a run proposes, since that
last one crosses into the privileged helper.
"""

from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from tests._support import _SRC  # noqa: F401

from goblinmode import preflight

_REPO = Path(__file__).resolve().parent.parent


def _binary() -> Path | None:
    override = os.environ.get("GMP_PREFLIGHT_BIN")
    if override:
        return Path(override) if Path(override).exists() else None
    for profile in ("debug", "release"):
        candidate = _REPO / "target" / profile / "examples" / "preflight"
        if candidate.exists():
            return candidate
    return None


def _res(c) -> list:
    return [c.status, c.value, c.detail]


class BothImplementationsAgree(unittest.TestCase):
    def setUp(self):
        self.binary = _binary()
        if self.binary is None:
            if os.environ.get("GMP_REQUIRE_RUST_HELPER") == "1":
                self.fail("GMP_REQUIRE_RUST_HELPER=1 but the preflight example is not "
                          "built - run `cargo build -p gmp-core --example preflight`")
            self.skipTest("build it with `cargo build -p gmp-core --example preflight`")

    def _rust(self, p: dict) -> dict:
        r = subprocess.run([str(self.binary)], input=json.dumps(p),
                           capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def _python(self, p: dict) -> dict:
        reads_int = {"/proc/sys/vm/max_map_count": p.get("max_map_count"),
                     "/proc/sys/vm/compaction_proactiveness": p.get("compaction"),
                     "/proc/sys/vm/swappiness": p.get("swappiness")}
        reads = {"/proc/sys/kernel/split_lock_mitigate": p.get("split_lock")}
        rows = p.get("rows", [])
        with patch.object(preflight, "_read_int", lambda k: reads_int.get(k)), \
                patch.object(preflight, "_read", lambda k: reads.get(k)), \
                patch.object(preflight, "_nofile_hard", lambda: p.get("nofile")), \
                patch.object(preflight, "_kernel_ver",
                             lambda: (p.get("kernel_major", 0), p.get("kernel_minor", 0))):
            out = {
                "max_map_count": _res(preflight._c_max_map_count()),
                "nofile": _res(preflight._c_nofile()),
                "split_lock": _res(preflight._c_split_lock()),
                "compaction": _res(preflight._c_compaction()),
                "swappiness": _res(preflight._c_swappiness()),
                "fsync": _res(preflight._c_fsync()),
            }
        status, severity = p.get("status", ""), p.get("severity", "")
        capped = status
        if status == preflight.FAIL and severity == preflight.WARN:
            capped = preflight.WARN
        elif status == preflight.WARN and severity == preflight.INFO:
            capped = preflight.INFO
        counts = {s: 0 for s in (preflight.OK, preflight.WARN, preflight.FAIL,
                                 preflight.INFO, preflight.UNKNOWN)}
        for r in rows:
            counts[r["status"]] = counts.get(r["status"], 0) + 1
        out["capped"] = capped
        out["summary"] = dict(sorted(counts.items()))
        out["pending_sysctls"] = [list(s) for s in preflight.pending_sysctls(rows)]
        out["dropin"] = preflight.sysctl_dropin_text(rows)
        return out

    def _same(self, p: dict) -> dict:
        py, rs = self._python(p), self._rust(p)
        self.assertEqual(py, rs)
        return py

    # ---- each threshold, both sides ---------------------------------------

    def test_every_numeric_threshold(self):
        for key, values in (
            ("max_map_count", (None, 0, 1_048_575, 1_048_576, 2_147_483_642)),
            ("nofile", (None, 0, 524_287, 524_288, 1_048_576)),
            ("compaction", (None, 0, 5, 6, 20)),
            ("swappiness", (None, 0, 20, 21, 60)),
        ):
            for v in values:
                with self.subTest(check=key, value=v):
                    self._same({key: v})

    def test_the_split_lock_knob(self):
        for v in (None, "0", "1", "2", ""):
            with self.subTest(value=v):
                self._same({"split_lock": v})

    def test_the_fsync_kernel_boundary(self):
        for major, minor in ((5, 15), (5, 16), (5, 17), (6, 0), (4, 19), (0, 0)):
            with self.subTest(kernel=f"{major}.{minor}"):
                self._same({"kernel_major": major, "kernel_minor": minor})

    # ---- the aggregation ---------------------------------------------------

    def test_the_severity_cap_for_every_combination(self):
        statuses = (preflight.OK, preflight.WARN, preflight.FAIL,
                    preflight.INFO, preflight.UNKNOWN)
        for status in statuses:
            for severity in statuses:
                with self.subTest(status=status, severity=severity):
                    self._same({"status": status, "severity": severity})

    def test_only_failing_checks_propose_a_sysctl(self):
        rows = [
            {"id": "a", "status": preflight.OK, "sysctl": ["vm.swappiness", "10"]},
            {"id": "b", "status": preflight.FAIL, "sysctl": ["vm.max_map_count", "2147483642"]},
            {"id": "c", "status": preflight.WARN, "sysctl": ["vm.compaction_proactiveness", "0"]},
            {"id": "d", "status": preflight.FAIL, "sysctl": None},
            {"id": "e", "status": preflight.INFO, "sysctl": ["vm.swappiness", "10"]},
            {"id": "f", "status": preflight.UNKNOWN, "sysctl": ["vm.swappiness", "10"]},
        ]
        out = self._same({"rows": rows})
        self.assertEqual(out["pending_sysctls"],
                         [["vm.max_map_count", "2147483642"],
                          ["vm.compaction_proactiveness", "0"]])
        self.assertIn("vm.max_map_count = 2147483642", out["dropin"])
        self.assertNotIn("vm.swappiness", out["dropin"])

    def test_an_empty_run(self):
        self._same({"rows": []})

    # ---- the metadata that crosses into the helper -------------------------

    def test_the_check_tables_match_field_for_field(self):
        import re
        src = (_REPO / "crates/gmp-core/src/preflight.rs").read_text()
        block = src.split("pub const CHECKS: &[Check] = &[", 1)[1].split("\n];", 1)[0]
        chunks = block.split("Check {")[1:]
        self.assertEqual(len(chunks), len(preflight.CHECKS),
                         "the two check tables are different lengths")
        for py, chunk in zip(preflight.CHECKS, chunks, strict=True):
            with self.subTest(check=py.id):
                def field(name, chunk=chunk):
                    m = re.search(rf'^\s*{name}: r(#*)"(.*?)"\1,\s*$', chunk, re.M | re.S)
                    return m.group(2) if m else None
                self.assertEqual(py.id, field("id"))
                self.assertEqual(py.title, field("title"))
                self.assertEqual(py.why, field("why"))
                self.assertEqual(py.fix_hint, field("fix_hint"))
                self.assertEqual(py.severity, field("severity"))
                m = re.search(r'^\s*sysctl: Some\(\(r(#*)"(.*?)"\1, r(#*)"(.*?)"\3\)\),\s*$',
                              chunk, re.M | re.S)
                self.assertEqual(py.sysctl, (m.group(2), m.group(4)) if m else None)

    def test_every_proposable_key_is_one_the_helper_accepts(self):
        """The boundary, checked against the Rust table this time."""
        import importlib.util
        import sys as _sys
        spec = importlib.util.spec_from_file_location(
            "goblin_helper_pfp", _REPO / "helper" / "goblin_helper.py")
        helper = importlib.util.module_from_spec(spec)
        _sys.modules["goblin_helper_pfp"] = helper
        spec.loader.exec_module(helper)
        src = (_REPO / "crates/gmp-core/src/preflight.rs").read_text()
        import re
        for key, value in re.findall(r'sysctl: Some\(\(r"([^"]+)", r"([^"]+)"\)\)', src):
            with self.subTest(key=key):
                self.assertIn(key, helper.SYSCTL_ALLOW)
                low, high = helper.SYSCTL_ALLOW[key]
                self.assertTrue(low <= int(value) <= high)


if __name__ == "__main__":
    unittest.main()
