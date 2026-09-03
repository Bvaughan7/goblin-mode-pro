"""The Rust and Python agree on what the daemon reports about itself.

Four answers, and all four outlive the moment they are produced. The readiness
score reaches the dashboard and the CLI's status line. The tweak fingerprint is
written into ``sessions.jsonl`` and read back when two runs are compared, so it
is a stored format and not just a label. The GPU summary is quoted in bug
reports. The metric window is what an incident carries as its evidence.

Two of the four have a boundary that is easy to get wrong in a second
implementation and invisible until it matters:

``max(0, x)`` hands back whichever *argument* won rather than a coerced value,
so a clamped readiness score is the integer ``0`` while every other score is a
float. That difference survives into JSON and out to the CLI, where it is the
difference between printing ``0`` and ``0.0``.

``downsample`` keeps the last sample deliberately - it is the sample the
incident fired on - and it gets there through index arithmetic that lands on
the final row only sometimes. Every length below is checked, not just the
convenient ones.
"""

from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path

from tests._support import _SRC  # noqa: F401

from goblinmode import daemon

_REPO = Path(__file__).resolve().parent.parent


def _binary() -> Path | None:
    override = os.environ.get("GMP_STATUS_BIN")
    if override:
        return Path(override) if Path(override).exists() else None
    for profile in ("debug", "release"):
        candidate = _REPO / "target" / profile / "examples" / "status"
        if candidate.exists():
            return candidate
    return None


def typed(value):
    """A comparable that keeps what ``==`` throws away.

    Two things here are user-visible and invisible to ordinary equality. The
    readiness score's TYPE carries meaning - ``0`` and ``0.0`` print
    differently - and Python says ``0 == 0.0 == -0.0``, so a comparison of the
    parsed replies alone cannot see the clamp at all. Dict ordering is the
    other: the counts are shown in a fixed order with unfamiliar statuses
    appended, and ``==`` ignores order entirely.
    """
    if isinstance(value, bool):
        return ("bool", value)
    if isinstance(value, int):
        return ("int", value)
    if isinstance(value, float):
        return ("float", repr(value))
    if isinstance(value, dict):
        return ("dict", [(k, typed(v)) for k, v in value.items()])
    if isinstance(value, list):
        return ("list", [typed(v) for v in value])
    return (type(value).__name__, value)


def chk(status, title="t", **over) -> dict:
    return {"status": status, "title": title, "value": "v", **over}


def many(status: str, count: int) -> list:
    return [chk(status, f"check {i}") for i in range(count)]


GPU = {"vram_used_mb": 5900, "vram_total_mb": 6144, "vram_free_mb": 244,
       "pcie_gen": 3, "pcie_gen_max": 3, "pcie_width": 16, "pcie_width_max": 16,
       "pstate": "P2", "clock_gfx_mhz": 1200, "clock_gfx_max_mhz": 1900}

TWEAKS = {"governor": "performance", "epp_boosted": True, "tearing": True,
          "adaptive_sync": True, "reniced": {"4242": -5}, "power_limited": True,
          "power_limits_w": [45, 60], "pinned": {"Wow.exe": "performance"}}


def case(**over) -> dict:
    base = {"results": [], "tweaks": {}, "gpu": {}, "rows": [], "target": 20}
    base.update(over)
    return base


CASES = {
    # -- the readiness score --------------------------------------------------
    "health_empty": case(),
    "health_all_ok": case(results=many("ok", 14)),
    "health_one_fail": case(results=[*many("ok", 13), chk("fail", "helper")]),
    "health_one_warn": case(results=[*many("ok", 13), chk("warn", "cooling")]),
    "health_realistic": case(results=[*many("ok", 11), chk("warn", "a"),
                                      chk("warn", "b"), chk("fail", "helper")]),
    # The clamp: the score is the INTEGER zero here and a float everywhere else.
    "health_all_fail": case(results=many("fail", 5)),
    "health_single_fail_only": case(results=[chk("fail", "only")]),
    "health_two_of_three_fail": case(results=[chk("fail", "a"), chk("fail", "b"),
                                              chk("ok", "c")]),
    # Straddling the clamp from just above and just below.
    "health_just_above_zero": case(results=[*many("fail", 3), *many("ok", 2)]),
    "health_just_below_zero": case(results=[*many("fail", 3), *many("ok", 1)]),
    "health_one_warn_one_ok": case(results=[chk("warn", "a"), chk("ok", "b")]),
    # Exactly on the clamp. 0.6*fails == 0.8*warns puts the raw score on -0.0,
    # where `max(0, -0.0)` still hands back the integer and a `>=` test would
    # hand back a float negative zero instead.
    "health_exactly_zero": case(results=[*many("fail", 4), *many("warn", 3)]),
    "health_exactly_zero_doubled": case(results=[*many("fail", 8), *many("warn", 6)]),
    # An exact .x5, where rounding half to even and half away from zero part
    # company: 6.25 becomes 6.2, not 6.3.
    "health_on_a_rounding_half": case(results=[*many("warn", 7), chk("ok", "z")]),
    "health_info_is_neutral": case(results=[chk("info", "a"), chk("fail", "b")]),
    "health_unknown_is_neutral": case(results=[chk("unknown", "a"), chk("fail", "b")]),
    # A status outside the five gets its own key and raises the denominator.
    "health_unfamiliar_status": case(results=[chk("elsewhere", "a"), chk("fail", "b")]),
    "health_status_missing": case(results=[{"title": "t"}]),
    "health_status_is_a_number": case(results=[chk(5, "t")]),
    "health_status_is_null": case(results=[chk(None, "t")]),
    "health_result_is_a_string": case(results=["nope"]),
    "health_title_missing": case(results=[{"status": "fail"}]),
    "health_title_is_a_number": case(results=[chk("fail", 5)]),
    "health_four_fails_only_three_named": case(results=many("fail", 4)),
    "health_fails_interleaved": case(results=[chk("ok", "a"), chk("fail", "b"),
                                              chk("ok", "c"), chk("fail", "d")]),
    # -- the tweak fingerprint -------------------------------------------------
    "fingerprint_everything": case(tweaks=TWEAKS),
    "fingerprint_nothing": case(tweaks={}),
    "fingerprint_governor_pinned": case(tweaks={"governor": "performance"}),
    "fingerprint_governor_powersave": case(tweaks={"governor": "powersave"}),
    # On intel_pstate the governor stays put and only EPP moves.
    "fingerprint_epp_only": case(tweaks={"epp_boosted": True}),
    "fingerprint_governor_is_a_number": case(tweaks={"governor": 5}),
    "fingerprint_tearing_only": case(tweaks={"tearing": True}),
    "fingerprint_vrr_only": case(tweaks={"adaptive_sync": True}),
    "fingerprint_renice_only": case(tweaks={"reniced": {"1": -5}}),
    "fingerprint_renice_empty": case(tweaks={"reniced": {}}),
    "fingerprint_renice_is_a_list": case(tweaks={"reniced": [1]}),
    "fingerprint_pin_one": case(tweaks={"pinned": {"a.exe": "performance"}}),
    # Only the first pinned process is named, in insertion order.
    "fingerprint_pin_two": case(tweaks={"pinned": {"z.exe": "efficiency",
                                                   "a.exe": "performance"}}),
    "fingerprint_pin_is_a_list": case(tweaks={"pinned": [1]}),
    "fingerprint_pin_mode_is_null": case(tweaks={"pinned": {"a.exe": None}}),
    "fingerprint_power_pair": case(tweaks={"power_limited": True,
                                           "power_limits_w": [45, 60]}),
    # A one-element pair used to raise IndexError behind a truthiness test.
    "fingerprint_power_half_pair": case(tweaks={"power_limited": True,
                                                "power_limits_w": [45]}),
    "fingerprint_power_empty_pair": case(tweaks={"power_limited": True,
                                                 "power_limits_w": []}),
    "fingerprint_power_three": case(tweaks={"power_limited": True,
                                            "power_limits_w": [45, 60, 75]}),
    "fingerprint_power_is_a_string": case(tweaks={"power_limited": True,
                                                  "power_limits_w": "45"}),
    "fingerprint_power_not_limited": case(tweaks={"power_limited": False,
                                                  "power_limits_w": [45, 60]}),
    "fingerprint_power_floats": case(tweaks={"power_limited": True,
                                             "power_limits_w": [45.0, 60.5]}),
    "fingerprint_is_a_list": case(tweaks=[1]),
    # -- the GPU projection -----------------------------------------------------
    "gpu_full": case(gpu=GPU),
    "gpu_empty": case(gpu={}),
    "gpu_partial": case(gpu={"pstate": "P0"}),
    "gpu_extra_keys": case(gpu={**GPU, "something_new": 1}),
    "gpu_is_a_list": case(gpu=[1]),
    "gpu_null_values": case(gpu={"pstate": None}),
    # -- downsampling -------------------------------------------------------------
    "downsample_empty": case(rows=[], target=20),
    "downsample_under": case(rows=[{"i": i} for i in range(5)], target=20),
    "downsample_exactly": case(rows=[{"i": i} for i in range(20)], target=20),
    "downsample_one_over": case(rows=[{"i": i} for i in range(21)], target=20),
    "downsample_double": case(rows=[{"i": i} for i in range(40)], target=20),
    "downsample_odd": case(rows=[{"i": i} for i in range(99)], target=20),
    "downsample_large": case(rows=[{"i": i} for i in range(1000)], target=20),
    "downsample_target_one": case(rows=[{"i": i} for i in range(10)], target=1),
    "downsample_target_two": case(rows=[{"i": i} for i in range(10)], target=2),
    "downsample_trace_target": case(rows=[{"i": i} for i in range(200)], target=30),
    "downsample_rows_are_scalars": case(rows=[1, 2, 3, 4, 5], target=2),
}


class BothImplementationsAgree(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.binary = _binary()
        if self.binary is None:
            if os.environ.get("GMP_REQUIRE_RUST_HELPER") == "1":
                self.fail("GMP_REQUIRE_RUST_HELPER=1 but the status example is not "
                          "built - run `cargo build -p gmp-core --example status`")
            self.skipTest("build it with `cargo build -p gmp-core --example status`")

    def _rust(self, payload: dict) -> dict:
        r = subprocess.run([str(self.binary)], input=json.dumps(payload),
                           capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    @staticmethod
    def _python(payload: dict) -> dict:
        return {
            "health": daemon.health_summary(payload["results"]),
            "fingerprint": daemon.tweaks_fingerprint(payload["tweaks"]),
            "gpu_summary": daemon._gpu_summary(payload["gpu"]),
            "downsampled": daemon._downsample(payload["rows"], payload["target"]),
        }

    def test_every_case_answers_the_same_way(self):
        # Compared through `typed`, so that a score of 0 against 0.0, or the
        # counts in a different order, is a failure rather than a pass.
        for label, payload in CASES.items():
            with self.subTest(label):
                self.assertEqual(typed(self._rust(payload)),
                                 typed(self._python(payload)))

    def test_a_clamped_score_is_an_integer_and_the_rest_are_floats(self):
        """`max(0, x)` returns the winning argument, so the type varies."""
        clamped = self._python(case(results=many("fail", 5)))["health"]["score"]
        self.assertEqual(clamped, 0)
        self.assertIsInstance(clamped, int)
        self.assertNotIsInstance(clamped, float)
        # And the Rust says the same thing, in the JSON as well as in value.
        self.assertEqual(json.dumps(self._rust(case(results=many("fail", 5)))
                                    ["health"]["score"]), "0")

        healthy = self._python(case(results=many("ok", 3)))["health"]["score"]
        self.assertIsInstance(healthy, float)
        self.assertEqual(json.dumps(self._rust(case(results=many("ok", 3)))
                                    ["health"]["score"]), "10.0")

    def test_downsampling_keeps_the_first_and_last_sample_at_every_length(self):
        """The last sample is the one the incident fired on."""
        for length in range(0, 260):
            for target in (20, 30):
                rows = [{"i": i} for i in range(length)]
                got = self._python(case(rows=rows, target=target))["downsampled"]
                self.assertEqual(len(got), min(length, target), f"{length}/{target}")
                if rows:
                    self.assertEqual(got[-1], rows[-1], f"{length}/{target}")
                    self.assertEqual(got[0], rows[0], f"{length}/{target}")

    def test_the_corpus_covers_both_sides_of_every_branch(self):
        scores = set()
        fingerprints = set()
        for payload in CASES.values():
            answer = self._python(payload)
            scores.add(type(answer["health"]["score"]).__name__)
            fingerprints.update(answer["fingerprint"])
        self.assertEqual(scores, {"int", "float"}, "the clamp is one-sided")
        for token in ("governor", "tearing", "vrr", "renice"):
            self.assertIn(token, fingerprints)
        self.assertTrue(any(f.startswith("pin:") for f in fingerprints))
        self.assertTrue(any(f.startswith("pl:") for f in fingerprints))


class NoInputShapeCanBreakAStatusReply(unittest.TestCase):
    """These feed the dashboard, the CLI and every session record.

    A reply that raises is a dashboard that does not load, so the same rule
    holds here as everywhere else in the reporting path.
    """

    def test_no_corpus_case_raises(self):
        for label, payload in CASES.items():
            with self.subTest(label):
                BothImplementationsAgree._python(payload)

    def test_nor_do_shapes_the_corpus_does_not_pin(self):
        daemon.health_summary([{"status": {"a": 1}, "title": [1]}])
        daemon.health_summary([None, 5, [], {}])
        daemon.tweaks_fingerprint({"pinned": {"a": {"b": 1}}, "power_limited": True,
                                   "power_limits_w": [{"a": 1}, [2]]})
        daemon.tweaks_fingerprint(None)
        daemon._gpu_summary(None)
        daemon._gpu_summary("nope")


if __name__ == "__main__":
    unittest.main()
