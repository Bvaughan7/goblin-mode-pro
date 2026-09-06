"""The Rust and Python frame-rate watchers agree.

Boundary-first, because every constant in this module is a threshold and
several of them interact: a dip has to be deep enough (dip_floor, dip_ratio),
last long enough (_MIN_DIP_DURATION_S), not be an alt-tab (_NOT_RENDERING_FPS),
and recover past a higher bar than it fell through (_EXIT_HYSTERESIS,
_RECOVERY_FRAC). Each gets a case on either side.

The unit inference gets the most attention, because it is the one part whose
failure is silent and total: pick the wrong divisor and the virtual clock is
off by 1000x, every window measured against it is meaningless, and the watcher
either never reports anything or reports constantly. The corpus feeds the same
log at all four units and at the cadences where two of them are genuinely
ambiguous.

Poll boundaries are part of the input, not an implementation detail - the unit
settles and the state advances per poll, so where a poll ends changes what is
reported.
"""

from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path

from tests._support import _SRC  # noqa: F401

from goblinmode import fpswatch

_REPO = Path(__file__).resolve().parent.parent


def _binary() -> Path | None:
    override = os.environ.get("GMP_FPSWATCH_BIN")
    if override:
        return Path(override) if Path(override).exists() else None
    for profile in ("debug", "release"):
        candidate = _REPO / "target" / profile / "examples" / "fpswatch"
        if candidate.exists():
            return candidate
    return None


def rows(fps, n, *, unit=1e3, start=0, step_s=0.2, header="fps,elapsed"):
    """`n` rows at `fps`, `step_s` apart, with elapsed expressed in `unit`."""
    out = [header] if header else []
    for i in range(n):
        out.append(f"{fps},{int((start + i * step_s) * unit)}")
    return out


def log(*parts, header="fps,elapsed"):
    return [header, *parts]


# 300 frame rates - one 60 s window at a 0.2 s cadence - whose exact mean is
# 55.15. Summed as a fold that reads 55.2; summed the way CPython's `sum` does
# (compensated, since 3.12), 55.1. Inline rather than a fixture file because
# tests/fixtures belongs to the helper's state readers. Built by `find_fold.py`;
# test_sessions_parity carries the long version of why `sum` is not a fold.
_COMPENSATED_RAW = (
    "38.869495,38.170133,39.597317,17.660394,93.401021,64.313283,19.328996,"
    "85.515854,24.836645,58.213410,90.191484,28.511343,20.001800,18.709331,"
    "56.874681,50.007402,78.378475,73.762926,25.773847,41.550976,50.428414,"
    "76.881279,73.635700,38.764294,54.637613,78.556988,81.726444,41.218322,"
    "33.673053,66.969899,75.519955,83.595312,25.286531,89.968823,50.170571,"
    "77.388529,75.610873,75.740917,27.655841,31.958973,88.920638,88.493492,"
    "68.910794,75.492839,63.367251,74.627073,93.854421,63.484219,62.219901,"
    "50.238092,94.004111,45.036935,62.953039,40.878733,42.379381,41.967882,"
    "77.682435,14.502033,61.208856,89.054853,82.028604,38.550408,54.454279,"
    "20.784992,46.289507,18.415877,15.336969,25.996096,69.941217,60.860494,"
    "82.429761,61.602060,94.304231,46.242491,54.037646,52.044173,72.067380,"
    "64.146842,33.294009,36.417548,82.685630,93.579467,50.904300,15.740252,"
    "44.728778,71.594750,52.025417,17.818151,76.734739,76.035955,55.466302,"
    "22.346045,66.868104,90.152409,72.443922,88.464637,94.622222,36.682598,"
    "20.920800,75.056502,33.888573,78.261992,85.589684,79.453334,59.382854,"
    "21.521270,39.309140,88.093852,57.557596,29.808688,62.948140,38.416499,"
    "70.613596,47.336130,16.632452,89.306968,93.494661,47.570212,40.275284,"
    "84.617267,45.769168,49.001471,68.669755,62.269757,29.463757,14.769458,"
    "46.028434,16.256218,67.237057,46.506780,35.746865,19.269504,82.460514,"
    "42.450320,35.764513,71.463585,45.634775,72.122933,88.127477,56.482418,"
    "24.186059,13.994965,29.251035,44.155052,51.070418,46.776607,22.338572,"
    "89.689150,81.105145,31.170193,21.987129,71.039793,83.467004,40.294437,"
    "45.132329,61.726181,70.403950,21.379340,13.870526,22.756454,46.128208,"
    "18.706745,23.822798,80.835703,88.999143,33.923429,50.575913,30.290057,"
    "69.462859,80.306024,17.932496,61.744578,24.280386,88.586814,77.255303,"
    "26.228467,95.484397,83.993982,92.351156,28.316774,56.376797,14.807269,"
    "56.850619,28.621896,60.054534,31.548712,41.984057,52.646019,54.241453,"
    "41.780982,70.229604,57.915478,58.883165,57.100695,81.694688,76.998787,"
    "94.904566,46.949658,29.700759,34.695731,68.335341,24.694985,42.256363,"
    "63.395651,18.005870,38.349681,41.901403,19.472416,46.258666,50.480445,"
    "30.412034,91.668360,31.067489,36.962052,61.963769,69.463142,30.102738,"
    "65.110481,73.714567,65.365238,57.326363,52.914141,23.815925,76.320723,"
    "47.638106,61.575449,15.207207,33.829782,72.079063,22.843386,81.938790,"
    "28.087624,29.577644,48.287974,65.337804,58.422280,80.235786,42.929480,"
    "23.688180,20.411824,16.908505,37.728639,17.888165,90.209630,53.339894,"
    "34.612574,24.817462,70.884549,91.490057,23.222204,54.762141,82.099336,"
    "68.796334,90.066982,72.990304,80.764707,86.507239,56.207664,86.995156,"
    "19.535429,73.890811,92.619341,54.355864,36.259704,17.077078,85.216457,"
    "18.842873,26.439203,51.874646,69.911042,37.401893,38.009399,62.695662,"
    "93.021627,59.672974,53.195921,34.810584,58.441764,87.244082,71.364818,"
    "61.272754,30.184491,82.346566,79.324568,32.846791,58.955800,90.193974,"
    "93.274976,14.585442,60.952378,79.171176,42.514668,52.774583,70.386692,"
    "44.946664,74.627053,36.457975,26.591058,52.388909,348.554869"
)
_COMPENSATED = _COMPENSATED_RAW.split(",")


def compensated_window() -> list[str]:
    return ["fps,elapsed"] + [f"{v},{int(i * 0.2 * 1e3)}"
                              for i, v in enumerate(_COMPENSATED)]


STEADY = rows(60, 300)
# A dip that qualifies on every axis, then a clean recovery.
DIP = STEADY + [f"15,{int((60 + i * 0.2) * 1e3)}" for i in range(60)]
RECOVERY = DIP + [f"60,{int((72 + i * 0.2) * 1e3)}" for i in range(60)]

CASES = {
    "steady_60": STEADY,
    # The window average is the one number here that goes through a sum, and
    # `sum` compensates where a fold does not - 55.1 against 55.2 on this one.
    "a_window_whose_average_a_fold_gets_wrong": compensated_window(),
    "steady_144": rows(144, 300),
    "steady_at_the_floor": rows(22, 300),
    "steady_just_over_the_floor": rows(22.1, 300),
    "dip": DIP,
    "dip_and_recovery": RECOVERY,
    # Duration, either side of the 4s minimum at a 0.2s cadence.
    "dip_just_under_4s": STEADY + [f"15,{int((60 + i * 0.2) * 1e3)}" for i in range(20)],
    "dip_just_over_4s": STEADY + [f"15,{int((60 + i * 0.2) * 1e3)}" for i in range(22)],
    # Depth, either side of dip_ratio against a 60 fps baseline.
    "dip_to_exactly_half": STEADY + [f"30,{int((60 + i * 0.2) * 1e3)}" for i in range(60)],
    "dip_just_above_half": STEADY + [f"30.1,{int((60 + i * 0.2) * 1e3)}" for i in range(60)],
    # Recovery, either side of both bars: floor*1.15 = 25.3 and baseline*0.85.
    "recovers_to_just_under_the_bar":
        DIP + [f"50,{int((72 + i * 0.2) * 1e3)}" for i in range(60)],
    "recovers_to_exactly_the_bar":
        DIP + [f"51,{int((72 + i * 0.2) * 1e3)}" for i in range(60)],
    # Recovery lands between baseline*0.84 and baseline*0.85 - the only band
    # where the recovery fraction itself decides, rather than the floor or
    # the hysteresis on top of it.
    "recovers_between_84_and_85_percent":
        DIP + [f"50.5,{int((72 + i * 0.2) * 1e3)}" for i in range(60)],
    # A baseline between 29 and 30, so the "is anything being drawn" gate is
    # the deciding factor rather than the frame rate.
    "baseline_just_under_30_then_idle":
        rows(29.5, 300) + [f"2,{int((60 + i * 0.2) * 1e3)}" for i in range(100)],
    # Alternating rates give the baseline window an even length with distinct
    # middle values, which is the only shape where the upper median and the
    # ordinary one disagree.
    "sawtooth": ["fps,elapsed"] + [f"{60 if i % 2 else 30},{i * 200}" for i in range(300)],
    # Not rendering: below 5 fps with a real baseline behind it.
    "alt_tab": STEADY + [f"2,{int((60 + i * 0.2) * 1e3)}" for i in range(100)],
    "alt_tab_boundary_5": STEADY + [f"5,{int((60 + i * 0.2) * 1e3)}" for i in range(100)],
    "alt_tab_boundary_5_1": STEADY + [f"5.1,{int((60 + i * 0.2) * 1e3)}" for i in range(100)],
    # Never bounced back - relearn after _MAX_DIP_S.
    "endless_dip": STEADY + [f"15,{int((60 + i * 0.2) * 1e3)}" for i in range(900)],
    # A dip from a low baseline, where dip_ratio cannot fire and only the
    # floor can.
    "low_baseline": rows(24, 300) + [f"20,{int((60 + i * 0.2) * 1e3)}" for i in range(60)],
    # -- unit inference -----------------------------------------------------
    "unit_s": rows(60, 200, unit=1),
    "unit_ms": rows(60, 200, unit=1e3),
    "unit_us": rows(60, 200, unit=1e6),
    "unit_ns": rows(60, 200, unit=1e9),
    # 1e6 per row: a 1 ms cadence in ns, a 1 s cadence in us. The frame rate
    # is the only thing that decides.
    "ambiguous_1e6_at_60fps": ["fps,elapsed"] + [f"60,{i * 1000000}" for i in range(200)],
    "ambiguous_1e6_at_1fps": ["fps,elapsed"] + [f"1,{i * 1000000}" for i in range(200)],
    "ambiguous_1e3_at_60fps": ["fps,elapsed"] + [f"60,{i * 1000}" for i in range(200)],
    # Per-frame rows rather than a fixed interval.
    "per_frame_rows_ns": ["fps,elapsed"] + [f"60,{i * 16666666}" for i in range(200)],
    "per_frame_rows_us": ["fps,elapsed"] + [f"60,{i * 16666}" for i in range(200)],
    # A labelled unit, which skips inference entirely.
    "labelled_ns": rows(60, 200, unit=1e9, header="fps,elapsed_ns"),
    "labelled_ms_parenthesised": rows(60, 200, unit=1e3, header="fps,elapsed (ms)"),
    "labelled_micro_sign": rows(60, 200, unit=1e6, header="fps,elapsed_µs"),
    "labelled_unknown_suffix": rows(60, 200, unit=1e3, header="fps,elapsed_furlongs"),
    # A clock that stalls, jumps backwards, or repeats.
    "elapsed_stalls": ["fps,elapsed"] + [f"60,{1000}" for _ in range(200)],
    "elapsed_goes_backwards": ["fps,elapsed"] + [f"60,{200000 - i * 1000}" for i in range(200)],
    "one_huge_jump": ["fps,elapsed"] + [f"60,{i * 1000}" for i in range(50)]
                     + [f"60,{50000 + 30 * 1000000}"] + [f"60,{50000 + 30000000 + i * 1000}"
                                                         for i in range(50)],
    # -- header and cell handling -------------------------------------------
    "no_elapsed_column": ["fps,frametime"] + ["60,16.6" for _ in range(200)],
    "no_fps_column": ["frametime,elapsed"] + [f"16.6,{i * 1000}" for i in range(200)],
    "spaces_after_commas": ["fps, elapsed"] + [f"60, {i * 1000}" for i in range(200)],
    "extra_columns": ["fps,frametime,elapsed,cpu"] + [f"60,16.6,{i * 1000},40" for i in range(200)],
    "elapsed_before_fps": ["elapsed,fps"] + [f"{i * 1000},60" for i in range(200)],
    "uppercase_header": ["FPS,ELAPSED"] + [f"60,{i * 1000}" for i in range(200)],
    "ragged_rows": ["fps,elapsed"] + [f"60,{i * 1000}" if i % 3 else "60" for i in range(200)],
    "unparseable_cells": ["fps,elapsed"] + [f"{'n/a' if i % 5 == 0 else 60},{i * 1000}"
                                            for i in range(200)],
    "nan_and_inf": ["fps,elapsed"] + [f"{'nan' if i % 2 else 'inf'},{i * 1000}"
                                      for i in range(200)],
    "underscored_numbers": ["fps,elapsed"] + [f"6_0,{i * 1000}" for i in range(200)],
    "out_of_range_rates": ["fps,elapsed"] + [f"{0 if i % 2 else 1000},{i * 1000}"
                                             for i in range(200)],
    "blank_lines": ["fps,elapsed", "", "  "] + [f"60,{i * 1000}" for i in range(200)],
    "header_only": ["fps,elapsed"],
    "empty": [],
    "no_header_at_all": [f"60,{i * 1000}" for i in range(200)],
    # History cap.
    "over_the_history_cap": rows(60, 6600),
}

# Poll shapes. Where a poll ends decides when the unit settles and when the
# state machine runs, so it is an input in its own right.
SHAPES = {
    "one_poll": lambda lines: [lines],
    "line_by_line": lambda lines: [[line] for line in lines],
    "in_tens": lambda lines: [lines[i:i + 10] for i in range(0, len(lines), 10)] or [[]],
    "header_then_rest": lambda lines: [lines[:1], lines[1:]] if lines else [[]],
}

FLOORS = [(22.0, 0.5), (30.0, 0.75), (0.0, 0.0), (60.0, 1.0)]


class BothImplementationsAgree(unittest.TestCase):
    def setUp(self):
        self.binary = _binary()
        if self.binary is None:
            if os.environ.get("GMP_REQUIRE_RUST_HELPER") == "1":
                self.fail("GMP_REQUIRE_RUST_HELPER=1 but the fpswatch example is "
                          "not built - run `cargo build -p gmp-core --example fpswatch`")
            self.skipTest("build it with `cargo build -p gmp-core --example fpswatch`")

    def _rust(self, polls, dip_floor=22.0, dip_ratio=0.5) -> dict:
        payload = {"polls": polls, "dip_floor": dip_floor, "dip_ratio": dip_ratio}
        r = subprocess.run([str(self.binary)], input=json.dumps(payload),
                           capture_output=True, text=True, timeout=120, check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    @staticmethod
    def _python(polls, dip_floor=22.0, dip_ratio=0.5) -> dict:
        w = fpswatch.FpsWatcher(dip_floor, dip_ratio)
        per_poll = []
        for poll in polls:
            for line in poll:
                line = line.strip()
                if line:
                    w._ingest(line)
            if w._unit_div is None and len(w._unit_deltas) >= fpswatch._UNIT_MIN_DELTAS:
                w._settle_unit()
            event = w._evaluate()
            per_poll.append(None if event is None else {
                "kind": event.kind, "fps": event.fps, "baseline": event.baseline,
                "duration_s": event.duration_s,
            })
        return {
            "per_poll": per_poll,
            "events": [e for e in per_poll if e is not None],
            "current_fps": w.current_fps(),
            "stats": w.stats() or None,
            "unit_div": w._unit_div,
        }

    def test_every_log_under_every_poll_shape(self):
        for label, lines in CASES.items():
            for shape, split in SHAPES.items():
                if shape == "line_by_line" and len(lines) > 400:
                    continue  # same behaviour, 6000 subprocess-free iterations
                with self.subTest(label, shape=shape):
                    polls = split(lines)
                    self.assertEqual(self._rust(polls), self._python(polls))

    def test_dip_settings_are_honoured_identically(self):
        for floor, ratio in FLOORS:
            for label in ("steady_60", "dip", "dip_and_recovery", "low_baseline",
                          "alt_tab", "endless_dip"):
                with self.subTest(label, floor=floor, ratio=ratio):
                    polls = [CASES[label]]
                    self.assertEqual(self._rust(polls, floor, ratio),
                                     self._python(polls, floor, ratio))

    def test_unit_inference_matches_directly(self):
        # The heuristic on its own, away from the state machine, so a change
        # to it cannot hide behind a dip that was never going to fire.
        corpus = [
            [], [(0.0, 60.0)], [(100.0, 0.0)], [(-1.0, 60.0)],
            [(16_666_666.0, 60.0)] * 8,
            [(16_666.0, 60.0)] * 8,
            [(16.6, 60.0)] * 8,
            [(0.0166, 60.0)] * 8,
            [(1_000_000.0, 60.0)] * 8,
            [(1_000_000.0, 1.0)] * 8,
            [(1_000.0, 60.0)] * 8,
            [(200.0, 60.0)] * 8,
            [(200.0, 5.0)] * 8,
            [(1.0, 1000.0)] * 8,
            [(1e12, 60.0)] * 8,
            [(i * 1000.0, 60.0) for i in range(1, 9)],
            [(16_666_666.0, 60.0), (33_333_333.0, 30.0), (8_333_333.0, 120.0)],
            # Even counts with distinct deltas: the upper median and the
            # ordinary one differ, and the ordinary one would pick a unit
            # that no row in the log actually exhibits.
            [(1_000.0, 60.0), (2_000.0, 60.0)],
            [(1_000.0, 60.0), (1_000_000.0, 60.0)],
            [(16_666.0, 60.0), (16_666_666.0, 60.0)],
            [(1.0, 60.0), (1_000.0, 60.0), (1_000_000.0, 60.0), (1e9, 60.0)],
            # med/d falls between 0.4 and 0.5 frame times: inside the
            # sub-frame floor at one slack value and outside it at the other.
            [(7.5, 60.0)] * 8,
            [(8.0, 60.0)] * 8,
            [(7.0, 60.0)] * 8,
        ]
        for samples in corpus:
            with self.subTest(samples=samples[:2]):
                self.assertEqual(self._infer_rust(samples),
                                 fpswatch._infer_divisor(samples))

    def _infer_rust(self, samples):
        """The divisor the Rust heuristic picks for these (delta, fps) pairs."""
        r = subprocess.run([str(self.binary)], input=json.dumps({"samples": samples}),
                           capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)["divisor"]


if __name__ == "__main__":
    unittest.main()
