"""The Rust and Python kscreen-doctor parsing agree.

This is the first module whose port is a real parser, so the corpus is built
around the ways a parser drifts rather than around thresholds: whitespace that
is not a plain space, markers in the wrong order, ids that repeat, outputs
that repeat, `Modes:` before any `Output:` line, and the `!`/`*` pair that
decides which mode is current.

The `!` (preferred) versus `*` (active) distinction gets its own cases in both
orders. A panel's preferred mode is regularly not the mode it is running, so
confusing the two would restore a display to a refresh rate it was never on.
"""

from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from tests._support import _SRC  # noqa: F401

from goblinmode import compositor

_REPO = Path(__file__).resolve().parent.parent


def _binary() -> Path | None:
    override = os.environ.get("GMP_COMPOSITOR_BIN")
    if override:
        return Path(override) if Path(override).exists() else None
    for profile in ("debug", "release"):
        candidate = _REPO / "target" / profile / "examples" / "compositor"
        if candidate.exists():
            return candidate
    return None


def out(name: str, *props: str) -> str:
    """One output block in the shape kscreen-doctor actually prints."""
    return f"Output: 1 {name}\n" + "".join(f"\t{p}\n" for p in props)


LAPTOP = (
    "Output: 1 eDP-1\n\tenabled\n\tconnected\n\tpriority 1\n\tPanel\n"
    "\tModes: 87:1920x1080@144!*  88:1920x1080@60  89:1600x900@60  90:1280x720@60\n"
    "\tGeometry: 0,0 1920x1080\n\tScale: 1\n\tVrr: Automatic\n"
    "Output: 2 DP-2\n\tenabled\n\tconnected\n\tpriority 2\n\tDisplayPort\n"
    "\tModes: 12:2560x1440@165*  13:2560x1440@120  14:2560x1440@60\n"
    "\tGeometry: 1920,0 2560x1440\n\tScale: 1\n\tVrr: Incapable\n"
)

DUMPS = {
    "laptop_plus_monitor": LAPTOP,
    "empty": "",
    "no_outputs": "kscreen-doctor: no backend\n",
    # Modes with no preceding Output: line - dropped by both.
    "orphan_modes": "\tModes: 1:1920x1080@60*\n",
    # Marker handling, both orders and neither.
    "preferred_not_active": out("eDP-1", "Modes: 1:1920x1080@144!  2:1920x1080@60*"),
    "active_not_preferred": out("eDP-1", "Modes: 1:1920x1080@144*  2:1920x1080@60!"),
    "both_markers": out("eDP-1", "Modes: 1:1920x1080@144!*  2:1920x1080@60"),
    "star_before_bang": out("eDP-1", "Modes: 1:1920x1080@144*!  2:1920x1080@60"),
    "no_markers": out("eDP-1", "Modes: 1:1920x1080@144  2:1920x1080@60"),
    "two_actives": out("eDP-1", "Modes: 1:1920x1080@144*  2:1920x1080@60*"),
    # Duplicates: Python dict semantics on both keys.
    "repeated_mode_id": out("eDP-1", "Modes: 7:1920x1080@60*  8:1280x720@60  7:800x600@75"),
    # A repeated id whose STALE value would still match a plan while the
    # fresh one does not. Without this case, an implementation that appends a
    # repeated id instead of updating it in place is invisible: the JSON
    # rendering collapses duplicates through a dict exactly as Python does,
    # so only the plan can tell the two apart.
    "repeated_id_that_changes_a_plan": out(
        "eDP-1", "Modes: 5:1920x1080@144*  7:1920x1080@60  7:800x600@75"),
    "repeated_output": out("eDP-1", "Modes: 1:1920x1080@60*") + out("eDP-1", "Modes: 2:1280x720@60"),
    "duplicate_mode_values": out("eDP-1", "Modes: 10:1920x1080@60*  11:1920x1080@60"),
    # Whitespace that is not a plain space.
    "tabs_between_modes": out("eDP-1", "Modes: 1:1920x1080@144*\t\t2:1920x1080@60"),
    "wide_gap": "Output:    1    eDP-1\n\tModes:   1:1920x1080@60*\n",
    "no_gap_after_colon": "Output:1 eDP-1\n\tModes: 1:1920x1080@60*\n",
    "leading_space_on_output": "   Output: 1 eDP-1\n\tModes: 1:1920x1080@60*\n",
    # Shapes that are close to a mode but are not one.
    "malformed_modes": out("eDP-1", "Modes: 1:1920x1080  2:x1080@60  :1920x1080@60  3:1920X1080@60"),
    "modes_word_elsewhere": out("eDP-1", "Description: Modes: not really 5:640x480@60*"),
    "geometry_looks_like_a_mode": out("eDP-1", "Modes: 1:1920x1080@60*", "Geometry: 0,0 1920x1080"),
    "zero_values": out("eDP-1", "Modes: 0:0x0@0*"),
    "leading_zeros": out("eDP-1", "Modes: 007:1920x1080@060*"),
    # VRR states, including the one that must be omitted.
    "vrr_never": out("eDP-1", "Modes: 1:1920x1080@60*", "Vrr: Never"),
    "vrr_lowercase": out("eDP-1", "Modes: 1:1920x1080@60*", "Vrr: automatic"),
    "vrr_incapable": out("eDP-1", "Modes: 1:1920x1080@60*", "Vrr: Incapable"),
    "vrr_no_space": out("eDP-1", "Modes: 1:1920x1080@60*", "Vrr:Always"),
    "vrr_before_modes": out("eDP-1", "Vrr: Always", "Modes: 1:1920x1080@60*"),
    "vrr_twice": out("eDP-1", "Vrr: Always", "Vrr: Never"),
    "vrr_without_output": "\tVrr: Always\n",
    # Panel naming.
    "edp_variants": out("eDP", "Modes: 1:1920x1080@60*") + out("eDP-1-unknown", "Modes: 2:800x600@60"),
    "external_only": out("DP-2", "Modes: 1:2560x1440@60*") + out("HDMI-A-1", "Modes: 2:1920x1080@60"),
    # "eDP" present but not at the start. Contrived - no kscreen backend
    # names an output this way today - but it is the only shape that
    # distinguishes startswith from a substring search, and the rule being
    # tested is "the panel is the output whose name BEGINS eDP".
    "edp_not_at_start": out("DP-1-eDP-mirror", "Modes: 1:1920x1080@60*"),
    "edp_second": out("DP-2", "Modes: 1:2560x1440@60*") + out("eDP-1", "Modes: 2:1920x1080@60*"),
}

RATES = [0, 30, 60, 90, 120, 144, 165, 240]
POLICIES = ["never", "automatic", "always", "Never", "auto", "", "always always"]


class BothImplementationsAgree(unittest.TestCase):
    def setUp(self):
        self.binary = _binary()
        if self.binary is None:
            if os.environ.get("GMP_REQUIRE_RUST_HELPER") == "1":
                self.fail("GMP_REQUIRE_RUST_HELPER=1 but the compositor example is "
                          "not built - run `cargo build -p gmp-core --example compositor`")
            self.skipTest("build it with `cargo build -p gmp-core --example compositor`")

    def _rust(self, stdout: str, output: str = "eDP-1", hz: int = 60,
              policy: str = "never") -> dict:
        payload = {"stdout": stdout, "output": output, "hz": hz, "policy": policy}
        r = subprocess.run([str(self.binary)], input=json.dumps(payload),
                           capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_parsing_matches(self):
        for label, dump in DUMPS.items():
            with self.subTest(label):
                want = compositor._parse_output_modes(dump)
                # Python's tuples become JSON arrays; compare like for like.
                want_json = {name: {"modes": {mid: list(mode)
                                              for mid, mode in o["modes"].items()},
                                    "current": o["current"]}
                             for name, o in want.items()}
                self.assertEqual(self._rust(dump)["parsed"], want_json)

    def test_mode_ordering_matches(self):
        # find_mode_id returns the FIRST match, so the order the ids come back
        # in is part of the answer, not an implementation detail.
        for label, dump in DUMPS.items():
            with self.subTest(label):
                got = self._rust(dump)["parsed"]
                want = compositor._parse_output_modes(dump)
                self.assertEqual(list(got), list(want), "output order")
                for name, o in want.items():
                    self.assertEqual(list(got[name]["modes"]), list(o["modes"]),
                                     f"mode order for {name}")

    def test_internal_panel_matches(self):
        for label, dump in DUMPS.items():
            with self.subTest(label):
                names = list(compositor._parse_output_modes(dump))
                want = next((n for n in names if n.startswith("eDP")), None)
                self.assertEqual(self._rust(dump)["internal_panel"], want)

    def test_find_mode_id_matches(self):
        for label, dump in DUMPS.items():
            for hz in RATES:
                with self.subTest(label, hz=hz):
                    self.assertEqual(self._rust(dump, hz=hz)["plan"],
                                     self._python_plan(dump, "eDP-1", hz))

    def test_the_plan_for_the_external_output_matches_too(self):
        for hz in RATES:
            with self.subTest(hz=hz):
                self.assertEqual(self._rust(LAPTOP, output="DP-2", hz=hz)["plan"],
                                 self._python_plan(LAPTOP, "DP-2", hz))

    def test_vrr_map_matches(self):
        for label, dump in DUMPS.items():
            with self.subTest(label):
                self.assertEqual(self._rust(dump)["vrr"], self._python_vrr(dump))

    def test_policy_validation_matches(self):
        for policy in POLICIES:
            with self.subTest(policy=policy):
                self.assertEqual(self._rust("", policy=policy)["valid_vrr"],
                                 policy in compositor._VRR_VALUES)

    # -- the Python side, driven through its real entry points ---------------

    def _python_plan(self, dump: str, output: str, hz: int):
        """``_set_refresh_rate``'s decision, with only the subprocess faked."""
        calls: list[list[str]] = []

        def fake_run(cmd, timeout=6):
            calls.append(cmd)
            if cmd[1:] == ["-o"]:
                return subprocess.CompletedProcess(cmd, 0, dump, "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with patch.object(compositor, "_run", fake_run):
            ok, prev = compositor._set_refresh_rate(output, hz)
        if not ok:
            return None
        target = calls[-1][1].rsplit(".", 1)[1]
        return [target, prev]

    def _python_vrr(self, dump: str) -> dict:
        with patch.object(compositor, "_run",
                          lambda cmd, timeout=6: subprocess.CompletedProcess(cmd, 0, dump, "")):
            return compositor._vrr_outputs()


if __name__ == "__main__":
    unittest.main()
