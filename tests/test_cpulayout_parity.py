"""The Rust and Python CPU-layout probes read the same sysfs the same way.

The first PROBE compared rather than the decision made from one, and the
reason it is worth comparing is what it feeds: `cpuset.target_cpus` decides
where a game's threads are pinned, so a layout read wrongly does not fail - it
pins a game to the wrong half of the CPU and says nothing.

Two kinds of tree. Synthetic ones, because one machine can only ever confirm
one shape and the interesting shapes are the ones this developer does not own:
a hybrid Intel part, a chiplet Ryzen, a kernel too old to publish `online`.
And this machine's real `/sys`, because a fixture is only ever the author's
idea of what sysfs looks like.
"""

from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tests._support import _SRC  # noqa: F401

from goblinmode import capabilities

_REPO = Path(__file__).resolve().parent.parent

#: name -> {relative path: contents}
TREES = {
    "just an online list": {"online": "0-11\n"},
    "no online file at all": {f"cpu{c}/x": "" for c in (0, 1, 10)},
    "directories that are not cpus": {
        "cpu0/x": "", "cpu1/x": "", "cpufreq/x": "", "cpuidle/x": "",
        "microcode/x": "",
    },
    "hybrid, classified by the kernel": {
        "online": "0-7", "types/intel_core/cpumap": "0-3",
        "cpu0/cpufreq/cpuinfo_max_freq": "1000000",
        "cpu7/cpufreq/cpuinfo_max_freq": "5000000",
    },
    "hybrid, the older file name": {
        "online": "0-3", "types/intel_core/cpus": "0,1",
    },
    "hybrid, found by frequency": {
        "online": "0-3",
        "cpu0/cpufreq/cpuinfo_max_freq": "5000000",
        "cpu1/cpufreq/cpuinfo_max_freq": "4700000",
        "cpu2/cpufreq/cpuinfo_max_freq": "3800000",
        "cpu3/cpufreq/cpuinfo_max_freq": "3800000",
    },
    # A core at 86% of the top: inside a loose threshold, outside the real
    # one. Without a case between the two, any threshold looks correct.
    "hybrid, a core between the thresholds": {
        "online": "0-3",
        "cpu0/cpufreq/cpuinfo_max_freq": "5000000",
        "cpu1/cpufreq/cpuinfo_max_freq": "4300000",
        "cpu2/cpufreq/cpuinfo_max_freq": "3800000",
        "cpu3/cpufreq/cpuinfo_max_freq": "3800000",
    },
    # And one just inside it, at 92.4%.
    "hybrid, a core just inside the threshold": {
        "online": "0-3",
        "cpu0/cpufreq/cpuinfo_max_freq": "5000000",
        "cpu1/cpufreq/cpuinfo_max_freq": "4620000",
        "cpu2/cpufreq/cpuinfo_max_freq": "3800000",
        "cpu3/cpufreq/cpuinfo_max_freq": "3800000",
    },
    "every core the same speed": {
        "online": "0-3",
        **{f"cpu{c}/cpufreq/cpuinfo_max_freq": "4000000" for c in range(4)},
    },
    "every core a p-core": {"online": "0-3", "types/intel_core/cpumap": "0-3"},
    "one cache group": {
        "online": "0-3",
        **{f"cpu{c}/cache/index3/shared_cpu_list": "0-3" for c in range(4)},
    },
    "two ccds": {
        "online": "0-3",
        **{f"cpu{c}/cache/index3/shared_cpu_list": "0-1" for c in (0, 1)},
        **{f"cpu{c}/cache/index3/shared_cpu_list": "2-3" for c in (2, 3)},
    },
    "a ryzen that is also reported hybrid": {
        "online": "0-7", "types/intel_core/cpumap": "0-3",
        **{f"cpu{c}/cache/index3/shared_cpu_list": "0-3" for c in range(4)},
        **{f"cpu{c}/cache/index3/shared_cpu_list": "4-7" for c in range(4, 8)},
    },
    "an unreadable cache list": {
        "online": "0-3", "cpu0/cache/index3/shared_cpu_list": "",
    },
    "a frequency that is not a number": {
        "online": "0-1",
        "cpu0/cpufreq/cpuinfo_max_freq": "fast",
        "cpu1/cpufreq/cpuinfo_max_freq": "3800000",
    },
    "nothing at all": {},
}


def _binary() -> Path | None:
    override = os.environ.get("GMP_CPULAYOUT_BIN")
    if override:
        return Path(override) if Path(override).exists() else None
    for profile in ("debug", "release"):
        candidate = _REPO / "target" / profile / "examples" / "cpulayout"
        if candidate.exists():
            return candidate
    return None


def _build(files: dict) -> TemporaryDirectory:
    tmp = TemporaryDirectory()
    root = Path(tmp.name)
    for relative, contents in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)
    return tmp


class BothProbesReadTheSameShape(unittest.TestCase):
    def setUp(self):
        self.binary = _binary()
        if self.binary is None:
            if os.environ.get("GMP_REQUIRE_RUST_HELPER") == "1":
                self.fail("GMP_REQUIRE_RUST_HELPER=1 but the cpulayout example "
                          "is not built - run `cargo build -p gmp-core "
                          "--example cpulayout`")
            self.skipTest("build it with `cargo build -p gmp-core "
                          "--example cpulayout`")

    def _rust(self, roots: list) -> list:
        r = subprocess.run([str(self.binary)],
                           input=json.dumps({"roots": [str(x) for x in roots]}),
                           capture_output=True, text=True, timeout=60,
                           check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def _python(self, root: Path) -> dict:
        with patch.object(capabilities, "_CPU", root):
            return capabilities._core_layout()

    def test_every_shape_of_cpu_reads_the_same(self):
        for name, files in TREES.items():
            with self.subTest(tree=name):
                tmp = _build(files)
                try:
                    root = Path(tmp.name)
                    self.assertEqual(self._rust([root])[0], self._python(root))
                finally:
                    tmp.cleanup()

    def test_this_machines_real_sysfs_reads_the_same(self):
        """A fixture is the author's idea of what sysfs looks like. This is
        what one actually is."""
        real = Path("/sys/devices/system/cpu")
        if not real.is_dir():
            self.skipTest("no /sys/devices/system/cpu here")
        self.assertEqual(self._rust([real])[0], self._python(real))

    def test_the_layout_answers_the_pinner_the_same_way(self):
        """The layout exists to be handed to `target_cpus`. Comparing the two
        probes and then the two pinners separately would leave the join
        between them untested."""
        from goblinmode import cpuset

        cpuset_binary = None
        for profile in ("debug", "release"):
            candidate = _REPO / "target" / profile / "examples" / "cpuset"
            if candidate.exists():
                cpuset_binary = candidate
        if cpuset_binary is None:
            self.skipTest("build it with `cargo build -p gmp-core --example cpuset`")

        for name, files in TREES.items():
            with self.subTest(tree=name):
                tmp = _build(files)
                try:
                    layout = self._rust([Path(tmp.name)])[0]
                    cases = [{"mode": m, "layout": layout}
                             for m in ("performance", "cache0", "off")]
                    proc = subprocess.run(
                        [str(cpuset_binary)], input=json.dumps({"cases": cases}),
                        capture_output=True, text=True, timeout=60, check=False)
                    self.assertEqual(proc.returncode, 0, proc.stderr)
                    self.assertEqual(
                        json.loads(proc.stdout),
                        [cpuset.target_cpus(c["mode"], c["layout"]) for c in cases])
                finally:
                    tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
