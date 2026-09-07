"""The Rust and Python core-pinning targets agree.

`target_cpus` turns a profile's `core_pin` mode plus the detected core layout
into a concrete cpu set, and it is the kind of function that is wrong without
failing: pin a game to the wrong half of a hybrid CPU and it runs on the
efficiency cores, with nothing in any log to say so.

Three answers, not two. `None` means this machine has nothing to pin to - every
core the same, or one cache group - and an empty list means the layout named a
group and the group was empty. The caller treats both as "do not pin", so the
difference is invisible downstream and would survive a rewrite that collapsed
them; it is pinned here because the two modes reach it by different routes.
"""

from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path

from tests._support import _SRC  # noqa: F401

from goblinmode import cpuset

_REPO = Path(__file__).resolve().parent.parent

MODES = ["performance", "cache0", "off", "", "cache1", "PERFORMANCE"]

LAYOUTS = [
    {},
    {"online": [0, 1, 2, 3]},
    # A hybrid CPU: P-cores named, no cache groups worth choosing between.
    {"online": list(range(20)), "performance": [0, 1, 2, 3, 4, 5, 6, 7]},
    # A chiplet Ryzen: two CCDs, no hybrid split.
    {"online": list(range(16)),
     "cache_groups": [[0, 1, 2, 3, 4, 5, 6, 7], [8, 9, 10, 11, 12, 13, 14, 15]]},
    # Both.
    {"online": list(range(24)), "performance": [0, 1, 2, 3],
     "cache_groups": [[0, 1, 2, 3], [4, 5, 6, 7]]},
    # The empty-answer cases, which are the point of the corpus.
    {"performance": []},
    {"cache_groups": []},
    {"cache_groups": [[]]},
    {"cache_groups": [[], [1, 2]]},
    {"performance": None, "cache_groups": None},
]

#: Shapes a sysfs probe should never produce and might. The two functions
#: differ here; what the GAME ends up pinned to does not.
MALFORMED = [
    {"performance": 3},
    {"performance": [0, "x", 2]},
    {"cache_groups": {}},
    {"cache_groups": [1, 2, 3]},
    {"performance": [0, 1], "cache_groups": "no"},
]

WELL_FORMED = list(LAYOUTS)
LAYOUTS = LAYOUTS + MALFORMED


def _binary() -> Path | None:
    override = os.environ.get("GMP_CPUSET_BIN")
    if override:
        return Path(override) if Path(override).exists() else None
    for profile in ("debug", "release"):
        candidate = _REPO / "target" / profile / "examples" / "cpuset"
        if candidate.exists():
            return candidate
    return None


def _python_pin(mode, layout):
    """What the Python would actually pin to.

    `target_cpus` is not the last word: whatever it returns goes to
    `sched_setaffinity`, which refuses anything that is not a list of whole
    numbers, and `_pin_cores` swallows both that and any exception from here.
    So the question both implementations have to answer the same way is not
    "what does this function return" but "what does the game end up pinned
    to" - and `None` is the answer for every shape that cannot get that far.
    """
    try:
        cpus = cpuset.target_cpus(mode, layout)
    except (AttributeError, TypeError, ValueError):
        # What `list()` of the wrong thing raises. `_pin_cores` catches wider
        # than this and deliberately so - it must never break a launch - but a
        # test that caught everything would pass on a bug in itself.
        return None
    if cpus is None or not all(isinstance(c, int) and not isinstance(c, bool)
                               for c in cpus):
        return None
    return cpus or None


class BothImplementationsAgree(unittest.TestCase):
    def setUp(self):
        self.binary = _binary()
        if self.binary is None:
            if os.environ.get("GMP_REQUIRE_RUST_HELPER") == "1":
                self.fail("GMP_REQUIRE_RUST_HELPER=1 but the cpuset example is "
                          "not built - run `cargo build -p gmp-core "
                          "--example cpuset`")
            self.skipTest("build it with `cargo build -p gmp-core "
                          "--example cpuset`")

    def _rust(self, cases: list) -> list:
        r = subprocess.run([str(self.binary)],
                           input=json.dumps({"cases": cases}),
                           capture_output=True, text=True, timeout=60,
                           check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def _cases(self):
        return [{"mode": m, "layout": lay} for m in MODES for lay in LAYOUTS]

    def test_a_well_formed_layout_gives_the_same_cpu_list(self):
        cases = [c for c in self._cases() if c["layout"] in WELL_FORMED]
        got = self._rust(cases)
        for case, answer in zip(cases, got, strict=True):
            with self.subTest(mode=case["mode"], layout=case["layout"]):
                self.assertEqual(answer,
                                 cpuset.target_cpus(case["mode"], case["layout"]))

    def test_neither_implementation_pins_on_a_layout_that_answers_oddly(self):
        """The four shapes where the two functions differ, and where it does
        not reach the game: the Python raises, or hands `sched_setaffinity`
        something it refuses. The port answers None directly. Compared on what
        the game ends up pinned to, which is the thing that matters."""
        cases = [c for c in self._cases() if c["layout"] not in WELL_FORMED]
        self.assertTrue(cases)
        got = self._rust(cases)
        for case, answer in zip(cases, got, strict=True):
            with self.subTest(mode=case["mode"], layout=case["layout"]):
                self.assertEqual(answer,
                                 _python_pin(case["mode"], case["layout"]))

    def test_an_empty_group_is_not_the_same_answer_as_no_group(self):
        """Both mean "do not pin" to the caller, and they are different
        claims. A rewrite that returned None for the empty group would pass
        every test downstream of here."""
        self.assertEqual(cpuset.target_cpus("cache0", {"cache_groups": [[]]}), [])
        self.assertIsNone(cpuset.target_cpus("cache0", {"cache_groups": []}))
        self.assertEqual(
            self._rust([{"mode": "cache0", "layout": {"cache_groups": [[]]}},
                        {"mode": "cache0", "layout": {"cache_groups": []}}]),
            [[], None])

    def test_a_partial_cpu_list_is_never_the_answer(self):
        """The one answer neither implementation gives. A game pinned to SOME
        of the cores the layout named, because a probe answered oddly, would
        be slower than not pinning it and would say nothing about why."""
        layout = {"performance": [0, "x", 2]}
        self.assertIsNone(_python_pin("performance", layout))
        self.assertEqual(self._rust([{"mode": "performance", "layout": layout}]),
                         [None])

    def test_a_layout_that_is_not_a_mapping(self):
        """The Python raises here and `_pin_cores` swallows it - the launch
        goes on unpinned with a warning. The port answers None, which is the
        same outcome by a shorter route."""
        with self.assertRaises(AttributeError):
            cpuset.target_cpus("performance", [])
        self.assertEqual(self._rust([{"mode": "performance", "layout": []}]),
                         [None])


if __name__ == "__main__":
    unittest.main()
