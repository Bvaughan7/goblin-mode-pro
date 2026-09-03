"""The Rust and Python selftest reporting agree.

The reporting layer is the first slice of the CLI port, ahead of the probes,
because it is the part a person actually reads. In particular
``_explain_call_failure`` is the text somebody sees at the exact moment the
tool is not working - a timeout there almost always means a polkit dialog
appeared on a screen they are not looking at - and it should say the same
thing whichever implementation produced it.

The corpus is built around the layout rules rather than the happy path:
section ordering, column alignment against titles of different widths, which
counts appear in the tally, and the paragraph a read-only run ends with.
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
from goblinmode.__about__ import __version__

_REPO = Path(__file__).resolve().parent.parent

MACHINE = {
    "cpu": "Core i7-10750H", "cpu_vendor": "GenuineIntel",
    "gpu": "nvidia, intel", "distro": "cachyos",
    "kernel": "7.2.2-1-cachyos", "cpufreq_driver": "intel_pstate",
    "compositor": "kwin", "handheld": None,
}


def _binary() -> Path | None:
    override = os.environ.get("GMP_SELFTEST_REPORT_BIN")
    if override:
        return Path(override) if Path(override).exists() else None
    for profile in ("debug", "release"):
        candidate = _REPO / "target" / profile / "examples" / "selftest_report"
        if candidate.exists():
            return candidate
    return None


def r(status, title, section="General", detail="a sentence", observed=None):
    return {"name": title.lower().replace(" ", "_"), "title": title,
            "status": status, "detail": detail, "section": section,
            "observed": observed or {}}


P, F, S, N = selftest.PASS, selftest.FAIL, selftest.SKIP, selftest.INFO

CASES = {
    "empty": [],
    "one_pass": [r(P, "Governor", "CPU")],
    "one_of_each": [r(P, "Governor", "CPU"), r(F, "Fans", "Thermal"),
                    r(S, "ryzenadj", "Power"), r(N, "Handheld", "System")],
    "all_pass": [r(P, f"Check {i}", "CPU") for i in range(6)],
    "all_fail": [r(F, f"Check {i}", "CPU") for i in range(6)],
    # Section ordering: the suite's order, not sorted.
    "sections_out_of_alphabetical_order": [
        r(P, "A", "Zebra"), r(P, "B", "Alpha"), r(P, "C", "Zebra"), r(P, "D", "Middle")],
    "one_result_per_section": [r(P, f"T{i}", f"S{i}") for i in range(5)],
    "same_section_repeated_later": [
        r(P, "A", "One"), r(P, "B", "Two"), r(P, "C", "One")],
    # Alignment against titles of very different widths.
    "wide_and_narrow_titles": [
        r(P, "X", "CPU"), r(P, "A considerably longer capability title", "CPU")],
    "unicode_titles": [r(P, "Проверка", "CPU"), r(P, "X", "CPU")],
    "emoji_title": [r(P, "Fans 🌀", "Thermal"), r(P, "X", "Thermal")],
    "empty_title": [r(P, "", "CPU"), r(P, "Governor", "CPU")],
    "very_long_title": [r(P, "T" * 120, "CPU")],
    # Detail text that could disturb the layout.
    "multiline_detail": [r(P, "Governor", "CPU", detail="line one\nline two")],
    "empty_detail": [r(P, "Governor", "CPU", detail="")],
    "unicode_detail": [r(P, "Governor", "CPU", detail="45 °C — fine")],
    # Sections with odd names.
    "empty_section": [r(P, "Governor", "")],
    "unicode_section": [r(P, "Governor", "Проц")],
    # Observed payloads ride along into the JSON.
    "with_observed": [r(P, "Governor", "CPU", observed={"before": "powersave",
                                                        "after": "performance",
                                                        "n": 5, "ok": True})],
    "nested_observed": [r(P, "X", "S", observed={"a": {"b": [1, 2, {"c": None}]}})],
}

FAILURES = [
    {"type": "DBusError", "text": "Timeout was reached", "method": "SetGovernor"},
    {"type": "DBusError", "text": "org.freedesktop.DBus.Error.AccessDenied",
     "method": "SetSysctl"},
    {"type": "DBusError", "text": "Not Authorized", "method": "SpinUpFans"},
    {"type": "DBusError", "text": "not authorized", "method": "SpinUpFans"},
    {"type": "DBusError", "text": "NOT AUTHORIZED", "method": "SpinUpFans"},
    {"type": "OSError", "text": "No such file or directory", "method": "ReadUndervolt"},
    {"type": "ValueError", "text": "", "method": "SetTDP"},
    # Both markers present: the timeout branch is checked first.
    {"type": "DBusError", "text": "Timeout and AccessDenied", "method": "Renice"},
    {"type": "DBusError", "text": "timeout", "method": "Renice"},  # lowercase: no match
]

MASKS = [0, 1 << 23, 1 << 24, 1 << 21, (1 << 23) | (1 << 21),
         (1 << 23) | (1 << 24) | (1 << 21), (1 << 22), 2**63 - 1]

MICROWATTS = [None, 0, 1, 45_000_000, 45_250_000, 45_150_000, 45_350_000,
              45_450_000, 15_500_000, -1_000_000, 500_000_000]


class BothImplementationsAgree(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.binary = _binary()
        if self.binary is None:
            if os.environ.get("GMP_REQUIRE_RUST_HELPER") == "1":
                self.fail("GMP_REQUIRE_RUST_HELPER=1 but the selftest_report example "
                          "is not built - run `cargo build -p gmp-cli "
                          "--example selftest_report`")
            self.skipTest("build it with `cargo build -p gmp-cli --example selftest_report`")

    def _rust(self, results, apply=False, **extra) -> dict:
        payload = {"results": results, "apply": apply, "machine": MACHINE,
                   "version": __version__, **extra}
        proc = subprocess.run([str(self.binary)], input=json.dumps(payload),
                              capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    @staticmethod
    def _results(raw):
        return [selftest.Result(**item) for item in raw]

    def _python(self, results, apply=False):
        """The real render/to_json, with only machine_summary stubbed.

        machine_summary asks capabilities.detect() about this machine, which
        is not something two implementations can agree about - so it is the
        one thing injected rather than compared.
        """
        objects = self._results(results)
        with patch.object(selftest, "machine_summary", lambda: MACHINE):
            return {
                "plain": selftest.render(objects, apply, color=False),
                "colored": selftest.render(objects, apply, color=True),
                "json": json.loads(json.dumps(selftest.to_json(objects, apply))),
            }

    def test_the_plain_table_matches(self):
        for label, results in CASES.items():
            for apply in (False, True):
                with self.subTest(label, apply=apply):
                    self.assertEqual(self._rust(results, apply)["plain"],
                                     self._python(results, apply)["plain"])

    def test_the_coloured_table_matches(self):
        for label, results in CASES.items():
            with self.subTest(label):
                self.assertEqual(self._rust(results)["colored"],
                                 self._python(results)["colored"])

    def test_the_json_export_matches(self):
        for label, results in CASES.items():
            for apply in (False, True):
                with self.subTest(label, apply=apply):
                    self.assertEqual(self._rust(results, apply)["json"],
                                     self._python(results, apply)["json"])

    def test_call_failure_explanations_match(self):
        got = self._rust([], failures=FAILURES)["failures"]
        want = [selftest._explain_call_failure(
                    type(case["type"], (Exception,), {})(case["text"]), case["method"])
                for case in FAILURES]
        self.assertEqual(got, want)

    def test_capability_decoding_matches(self):
        got = self._rust([], masks=MASKS)["caps"]
        self.assertEqual(got, [selftest._decode_caps(m) for m in MASKS])

    def test_wattage_formatting_matches(self):
        got = self._rust([], uw=MICROWATTS)["watts"]
        self.assertEqual(got, [selftest._w(v) for v in MICROWATTS])


if __name__ == "__main__":
    unittest.main()
