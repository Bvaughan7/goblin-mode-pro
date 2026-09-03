"""The Rust and Python throttle assessment agree.

The engine is stateful, so the unit of comparison is a whole sequence of
samples and the whole list of verdicts it produces - a single sample proves
almost nothing here.

Built boundary-first. Every constant in the module is a threshold for staying
QUIET (five hits, not one; 20s window; 60% load; 98% of PL1; a 90s grace
period; a 900s reminder for chronic conditions), so the corpus sits on each
of those edges from both sides. The failure that matters is not a missed
incident, it is three popups during a raid.
"""

from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path

from tests._support import _SRC  # noqa: F401

from goblinmode.diagnostics import DiagnosticEngine, Sample

_REPO = Path(__file__).resolve().parent.parent


def _binary() -> Path | None:
    override = os.environ.get("GMP_DIAGNOSTICS_BIN")
    if override:
        return Path(override) if Path(override).exists() else None
    for profile in ("debug", "release"):
        candidate = _REPO / "target" / profile / "examples" / "diagnostics"
        if candidate.exists():
            return candidate
    return None


def s(t, *, throttled=False, cpu_temp=None, cpu_load=0.0, pkg=None, pl1=None,
      gpu_temp=None, reasons="") -> dict:
    return {"t": float(t), "cpu_throttled": throttled, "cpu_temp": cpu_temp,
            "cpu_load": cpu_load, "pkg_power_w": pkg, "pl1_w": pl1,
            "gpu_temp": gpu_temp, "gpu_throttle_reasons": reasons}


def run(*samples) -> list[dict]:
    return list(samples)


def steady(n, start=1, **kw) -> list[dict]:
    return [s(start + i, **kw) for i in range(n)]


# nvidia-smi's clocks_event_reasons.active field, as actually observed and as
# feared. The parse must never invent a throttle from an unreadable value.
REASONS = ["", " ", "0x0", "0x4", "0x8", "0X8", "8", "0x000000000000000c",
           "0xC8", "0x1_0", "1_0", "0x1__0", "+8", "-8", "-0x8", "0x",
           "N/A", "[N/A]", "Not Supported", "--", "0xZZ", "  0x8  ",
           "0x8f", "999999999999999999999999", "0b1000", "8.0", "8,0"]

SEQUENCES = {
    # -- the throttle window, either side of five hits ----------------------
    "four_hits_is_silence": steady(4, throttled=True, cpu_temp=96.0),
    "five_hits_fires": steady(5, throttled=True, cpu_temp=96.0),
    "six_hits_fires_once": steady(6, throttled=True, cpu_temp=96.0),
    # Five hits spread wider than the 20s window never accumulate.
    "hits_outside_the_window": [s(1 + i * 6, throttled=True, cpu_temp=96.0)
                                for i in range(6)],
    # Exactly on the window edge: t=1 and t=21 are 20s apart.
    "hits_on_the_window_edge": [s(1, throttled=True), s(6, throttled=True),
                                s(11, throttled=True), s(16, throttled=True),
                                s(21, throttled=True), s(21.0001, throttled=True)],
    "interleaved_hits": [s(i, throttled=(i % 2 == 0), cpu_temp=96.0)
                         for i in range(1, 21)],
    # -- the episode machine ------------------------------------------------
    "one_quiet_sample_is_the_same_episode":
        [*steady(5, throttled=True, cpu_temp=96.0), s(6),
         *steady(5, start=7, throttled=True, cpu_temp=96.0)],
    "long_gap_is_a_new_episode":
        steady(5, throttled=True, cpu_temp=96.0)
        + [s(t) for t in range(6, 131)]
        + steady(5, start=131, throttled=True, cpu_temp=96.0),
    # A gap just under the grace period, measured from where the window
    # actually stops holding the condition true (t=25), not from t=5.
    "gap_just_under_the_grace_period":
        steady(5, throttled=True, cpu_temp=96.0)
        + [s(t) for t in range(6, 114)]
        + steady(5, start=114, throttled=True, cpu_temp=96.0),
    # The grace period is only visible if the condition becomes true again on
    # the exact boundary sample. Last seen is t=25 (the window holds the
    # condition true for 20s past the final tick), so the fifth hit of the
    # next burst has to land on t=114 for 89-vs-90 to change an answer.
    "resumes_exactly_on_the_grace_boundary":
        steady(5, throttled=True, cpu_temp=96.0)
        + [s(t) for t in range(6, 110)]
        + [s(t, throttled=True, cpu_temp=96.0) for t in range(110, 115)],
    # The grace period is genuinely awkward to observe, and the reason is
    # worth stating: assess() refreshes issue_last_seen BEFORE it runs the
    # expiry pass, so the sample on which a condition returns can never expire
    # its own episode. The boundary is only visible when the condition is
    # ABSENT on exactly the boundary sample and present on the next one - and
    # then only for a kind whose reminder is short enough that the surviving
    # episode stays silent. power_limit (180s) is that kind; the chronic
    # thermal one (900s) would look identical either way.
    "power_limit_absent_on_the_grace_boundary":
        [s(1, cpu_load=90.0, pkg=45.0, pl1=45.0)]
        + [s(t) for t in range(2, 91)]
        + [s(91, cpu_load=90.0, pkg=45.0, pl1=45.0)],
    # The same boundary from the other side: absent at exactly last-seen+90
    # and present at +91. The pair pins the grace period from both
    # directions - one case alone only rules out a shorter window.
    "power_limit_absent_just_past_the_grace_boundary":
        [s(1, cpu_load=90.0, pkg=45.0, pl1=45.0)]
        + [s(t) for t in range(2, 92)]
        + [s(92, cpu_load=90.0, pkg=45.0, pl1=45.0)],
    # Likewise the reminder: the first report is at t=5, so only a sample at
    # exactly t=905 distinguishes a 900s reminder from an 899s one.
    "reminds_on_the_exact_boundary":
        [s(t, throttled=True, cpu_temp=96.0) for t in range(1, 911)],
    # Between 45*0.979 and 45*0.98 - the only band where the tolerance itself
    # is the deciding factor rather than the load test.
    "power_between_979_and_98_percent": [s(1, cpu_load=90.0, pkg=44.07, pl1=45.0)],
    "chronic_throttle_reminds_at_900_not_180":
        steady(5, throttled=True, cpu_temp=96.0)
        + [s(t, throttled=True, cpu_temp=96.0) for t in range(6, 1000, 7)],
    # -- power limit, either side of both thresholds ------------------------
    "load_exactly_60": [s(1, cpu_load=60.0, pkg=45.0, pl1=45.0)],
    "load_just_over_60": [s(1, cpu_load=60.1, pkg=45.0, pl1=45.0)],
    "power_exactly_at_98_percent": [s(1, cpu_load=90.0, pkg=44.1, pl1=45.0)],
    "power_just_under_98_percent": [s(1, cpu_load=90.0, pkg=44.0, pl1=45.0)],
    "power_over_the_cap": [s(1, cpu_load=90.0, pkg=52.0, pl1=45.0)],
    "power_with_no_pl1": [s(1, cpu_load=90.0, pkg=45.0, pl1=None)],
    "pl1_with_no_power": [s(1, cpu_load=90.0, pkg=None, pl1=45.0)],
    "zero_pl1": [s(1, cpu_load=90.0, pkg=0.0, pl1=0.0)],
    "power_limit_reminds_at_180":
        [s(t, cpu_load=90.0, pkg=45.0, pl1=45.0) for t in range(1, 400, 3)],
    # -- gpu reasons --------------------------------------------------------
    "gpu_power_cap_only": [s(1, reasons="0x4")],
    "gpu_hw_slowdown": [s(1, reasons="0x8", gpu_temp=88.0)],
    "gpu_all_bad_bits": [s(1, reasons="0xE8", gpu_temp=88.0)],
    "gpu_no_temp": [s(1, reasons="0x8", gpu_temp=None)],
    "gpu_zero_temp": [s(1, reasons="0x8", gpu_temp=0.0)],
    "gpu_every_reason_string": [s(i + 1, reasons=r, gpu_temp=88.0)
                                for i, r in enumerate(REASONS)],
    # -- temperature formatting --------------------------------------------
    "temp_rounds_half_to_even":
        [s(i + 1, throttled=True, cpu_temp=temp)
         for i, temp in enumerate([96.5, 97.5, 98.5, 99.5, 0.5, -0.5])],
    "zero_cpu_temp": steady(5, throttled=True, cpu_temp=0.0),
    "negative_cpu_temp": steady(5, throttled=True, cpu_temp=-5.0),
    # -- several conditions at once ----------------------------------------
    "everything_at_once": [s(t, throttled=True, cpu_temp=96.0, cpu_load=99.0,
                             pkg=45.0, pl1=45.0, reasons="0xC8", gpu_temp=88.0)
                           for t in range(1, 20)],
    # -- degenerate timelines ----------------------------------------------
    "empty": [],
    "repeated_timestamps": [s(1, throttled=True, cpu_temp=96.0) for _ in range(8)],
    "time_goes_backwards": [s(10, throttled=True), s(9, throttled=True),
                            s(8, throttled=True), s(7, throttled=True),
                            s(6, throttled=True), s(5, throttled=True)],
    "zero_time": [s(0, throttled=True) for _ in range(6)],
}


class BothImplementationsAgree(unittest.TestCase):
    def setUp(self):
        self.binary = _binary()
        if self.binary is None:
            if os.environ.get("GMP_REQUIRE_RUST_HELPER") == "1":
                self.fail("GMP_REQUIRE_RUST_HELPER=1 but the diagnostics example is "
                          "not built - run `cargo build -p gmp-core --example diagnostics`")
            self.skipTest("build it with `cargo build -p gmp-core --example diagnostics`")

    def _rust(self, samples: list[dict]) -> dict:
        r = subprocess.run([str(self.binary)], input=json.dumps({"samples": samples}),
                           capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    @staticmethod
    def _sample(d: dict) -> Sample:
        return Sample(t=d["t"], cpu_temp=d["cpu_temp"], cpu_load=d["cpu_load"],
                      per_core=[], pkg_power_w=d["pkg_power_w"], pl1_w=d["pl1_w"],
                      pl2_w=None, gpu_load=None, gpu_temp=d["gpu_temp"],
                      gpu_throttle_reasons=d["gpu_throttle_reasons"],
                      cpu_throttled=d["cpu_throttled"])

    def _python_verdicts(self, samples: list[dict]) -> list:
        engine = DiagnosticEngine.__new__(DiagnosticEngine)
        engine._throttle_hits = __import__("collections").deque()
        engine._incident_seen = {}
        engine._issue_last_seen = {}
        return [list(v) if v else None
                for v in (engine.assess(self._sample(d)) for d in samples)]

    def _python_issues(self, samples: list[dict]) -> list:
        engine = DiagnosticEngine.__new__(DiagnosticEngine)
        engine._throttle_hits = __import__("collections").deque()
        engine._incident_seen = {}
        engine._issue_last_seen = {}
        return [[list(pair) for pair in engine._current_issues(self._sample(d)).items()]
                for d in samples]

    def test_verdicts_match(self):
        for label, samples in SEQUENCES.items():
            with self.subTest(label):
                self.assertEqual(self._rust(samples)["verdicts"],
                                 self._python_verdicts(samples))

    def test_per_sample_issues_match(self):
        # Diffed separately from the episode machine, so a detection change
        # cannot hide behind the de-duplication on top of it.
        for label, samples in SEQUENCES.items():
            with self.subTest(label):
                self.assertEqual(self._rust(samples)["issues"],
                                 self._python_issues(samples))

    def test_gpu_reason_parsing_matches(self):
        samples = [s(1, reasons=r) for r in REASONS]
        engine = DiagnosticEngine.__new__(DiagnosticEngine)
        want = [str(engine._parse_gpu_reasons(r)) for r in REASONS]
        self.assertEqual(self._rust(samples)["reasons"], want)


if __name__ == "__main__":
    unittest.main()
