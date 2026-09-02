"""The files both helper implementations read and write.

Three files under /run/goblin-mode-pro are a compatibility surface, not an
implementation detail: whichever helper is running may find one the other
wrote. That is live right now - the Rust helper writes state.json and a Python
daemon reads it - and it is the thing a rollback depends on. Roll back to
Python mid-session and it has to restore from whatever Rust recorded.

The fixtures under tests/fixtures/ are the record. Each was captured by running
the real writer of each implementation, not written by hand. They are checked
in so a change to either serializer has to face them; do not regenerate one
casually to make a test pass, because that is how the record stops recording.

Key ORDER differs between the two - Python preserves insertion order, Rust's
BTreeMap sorts - and that is fine. JSON object order carries no meaning and
both parsers accept either. Semantic equality is what is asserted here.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_FIXTURES = _REPO / "tests" / "fixtures"


def _load_helper():
    """The Python helper, imported standalone the way it is installed."""
    spec = importlib.util.spec_from_file_location(
        "goblin_helper_compat", _REPO / "helper" / "goblin_helper.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["goblin_helper_compat"] = module
    spec.loader.exec_module(module)
    return module


def _fixture(name: str) -> str:
    return (_FIXTURES / name).read_text()


class FixturesAgree(unittest.TestCase):
    """The two implementations write the same thing, modulo key order."""

    PAIRS = (("state.python.json", "state.rust.json"),
             ("fans.python.json", "fans.rust.json"),
             ("sysctls.python.json", "sysctls.rust.json"))

    def test_each_pair_is_semantically_identical(self):
        for python, rust in self.PAIRS:
            with self.subTest(pair=python):
                self.assertEqual(
                    json.loads(_fixture(python)), json.loads(_fixture(rust)),
                    f"{python} and {rust} describe different machines",
                )

    def test_the_fixtures_are_not_empty_or_trivial(self):
        """A truncated fixture would make every test above pass vacuously.

        This has happened in this repo: a crash left state.python.json at zero
        bytes and the Rust test that include_str!s it kept compiling.
        """
        state = json.loads(_fixture("state.python.json"))
        self.assertEqual(state["governor"], "powersave")
        self.assertEqual(state["pl1_uw"], 107_000_000)
        # the AMD limits must each be present with their OWN value - restoring
        # them all to STAPM was a real bug (f33c437)
        limits = state["ryzenadj_limits_mw"]
        self.assertEqual(limits["stapm-limit"], 25_000)
        self.assertEqual(limits["fast-limit"], 33_000)
        self.assertNotEqual(limits["fast-limit"], limits["stapm-limit"])
        self.assertEqual(len(json.loads(_fixture("fans.python.json"))), 2)


class PythonReadsWhatRustWrote(unittest.TestCase):
    """The rollback path, exercised rather than assumed.

    Each test runs the REAL Python helper function against a file captured from
    the Rust implementation. Asserting that json.loads succeeds would prove
    almost nothing; what matters is that the helper acts on it correctly.
    """

    def setUp(self):
        self.gh = _load_helper()
        self.tmp = Path(tempfile.mkdtemp())
        self.run = self.tmp / "run"
        self.run.mkdir()
        self.gh.STATE_DIR = self.run
        self.gh.STATE_FILE = self.run / "state.json"
        self.gh.FAN_STATE_FILE = self.run / "fans.json"

    def test_revert_all_restores_from_a_rust_written_snapshot(self):
        cpu = self.tmp / "cpu"
        rapl = self.tmp / "rapl"
        for core in ("cpu0", "cpu1"):
            (cpu / core / "cpufreq").mkdir(parents=True)
            (cpu / core / "cpufreq" / "scaling_governor").write_text("performance\n")
            (cpu / core / "cpufreq" / "energy_performance_preference").write_text(
                "performance\n")
        rapl.mkdir()
        for idx in (0, 1):
            (rapl / f"constraint_{idx}_power_limit_uw").write_text("40000000")
        self.gh.CPU_BASE, self.gh.RAPL_BASE = cpu, rapl
        self.gh.RYZENADJ = None          # no ryzenadj here; the AMD keys are ignored
        self.gh.STATE_FILE.write_text(_fixture("state.rust.json"))

        self.assertTrue(self.gh.revert_all())
        for core in ("cpu0", "cpu1"):
            self.assertEqual(
                (cpu / core / "cpufreq" / "scaling_governor").read_text().strip(),
                "powersave", "the governor Rust recorded was not restored")
        self.assertEqual(
            (rapl / "constraint_0_power_limit_uw").read_text().strip(), "107000000")

    def test_reset_fans_hands_back_channels_rust_recorded(self):
        hwmon = self.tmp / "hwmon"
        written = json.loads(_fixture("fans.rust.json"))
        remapped = {}
        for path, saved in written.items():
            local = hwmon / Path(path).parent.name / Path(path).name
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_text("204\n")                       # left spun up
            local.with_name(f"{local.name}_enable").write_text("1\n")   # manual
            remapped[str(local)] = saved
        self.gh.FAN_STATE_FILE.write_text(json.dumps(remapped, indent=2))

        self.assertTrue(self.gh.reset_fans())
        for path, saved in remapped.items():
            self.assertEqual(Path(path).read_text().strip(), saved["pwm"])
            self.assertEqual(
                Path(path).with_name(Path(path).name + "_enable").read_text().strip(),
                saved["enable"], "the channel was not handed back to the EC")

    def test_the_sysctl_state_rust_writes_is_the_shape_python_reverts_from(self):
        (self.run / "sysctls.json").write_text(_fixture("sysctls.rust.json"))
        data = json.loads((self.run / "sysctls.json").read_text())
        self.assertEqual(data, {"vm.swappiness": "60"})
        # Python writes str(int(original)) back, so the recorded value must
        # survive that round trip unchanged.
        for key, value in data.items():
            self.assertIn(key, self.gh.SYSCTL_ALLOW)
            self.assertEqual(str(int(value)), value)


class ToleranceRules(unittest.TestCase):
    """Python's json is permissive and serde is strict by default.

    The asymmetry is where a silent break lives, so the rules are asserted
    rather than trusted: a missing field loads, an unknown field loads and
    survives, and a number may arrive as an int or a float.
    """

    def setUp(self):
        self.gh = _load_helper()
        self.tmp = Path(tempfile.mkdtemp())
        self.gh.STATE_DIR = self.tmp
        self.gh.STATE_FILE = self.tmp / "state.json"

    def test_python_loads_a_snapshot_with_a_field_it_does_not_know(self):
        """The rollback direction: a NEWER Rust helper adds a key, and this
        older Python one must still load the file rather than choke."""
        data = json.loads(_fixture("state.rust.json"))
        data["future_knob"] = {"added": "by a later version"}
        self.gh.STATE_FILE.write_text(json.dumps(data, indent=2))
        loaded = self.gh._load_state()
        self.assertEqual(loaded["governor"], "powersave")
        self.assertEqual(loaded["future_knob"], {"added": "by a later version"})

    def test_python_loads_power_limits_written_as_floats(self):
        data = json.loads(_fixture("state.rust.json"))
        data["pl1_uw"] = 107000000.0
        self.gh.STATE_FILE.write_text(json.dumps(data))
        self.assertEqual(int(self.gh._load_state()["pl1_uw"]), 107_000_000)

    def test_an_absent_state_file_is_none_not_a_crash(self):
        self.assertIsNone(self.gh._load_state())

    def test_a_corrupt_state_file_is_none_not_a_crash(self):
        self.gh.STATE_FILE.write_text("{not json")
        self.assertIsNone(self.gh._load_state())


class EveryFixtureLoads(unittest.TestCase):
    """Both readers, every committed fixture, enumerated not listed.

    Listing them by hand is how a fixture gets added and covered by nothing.
    """

    def test_every_fixture_loads_with_the_python_reader(self):
        seen = 0
        for path in sorted(_FIXTURES.glob("*.json")):
            with self.subTest(fixture=path.name):
                text = path.read_text()
                self.assertTrue(text.strip(), f"{path.name} is empty")
                data = json.loads(text)
                self.assertIsInstance(data, dict)
                if path.name.startswith("sysctls."):
                    for key, value in data.items():
                        self.assertIsInstance(value, str,
                                              f"{key} must be the string form")
                seen += 1
        self.assertGreaterEqual(seen, 6, "expected the six captured fixtures")

    def test_the_fixture_names_say_which_reader_owns_them(self):
        """The Rust sweep panics on a name it cannot route; keep them in step."""
        for path in sorted(_FIXTURES.glob("*.json")):
            with self.subTest(fixture=path.name):
                self.assertRegex(path.name, r"^(state|fans|sysctls)\.(python|rust)\.json$")


if __name__ == "__main__":
    unittest.main()
