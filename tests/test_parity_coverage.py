"""Every parity harness is actually run by CI.

The workflow names the parity suites one by one rather than discovering them,
and that is deliberate: the job that runs them has a Rust toolchain and no
PyGObject, so a suite that needs one would turn a green job red on the day it
was added rather than on the day it broke.

The cost of naming them is that a new harness has to be remembered, and it was
not: ten of them were written, passed locally, and were never run by CI at all.
In the unit-test job they skip - the examples are not built there - so nothing
was red and nothing was covered. This is the check that makes forgetting
impossible, and it is the same shape as the one guarding the version sites.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_WORKFLOW = _REPO / ".github/workflows/ci.yml"


class EveryParityHarnessIsRun(unittest.TestCase):
    def test_the_workflow_names_every_parity_suite_on_disk(self):
        on_disk = {p.stem for p in (_REPO / "tests").glob("test_*_parity.py")}
        named = set(re.findall(r"tests\.(test_\w+_parity)\b",
                               _WORKFLOW.read_text()))
        missing = sorted(on_disk - named)
        self.assertEqual(
            missing, [],
            "these parity suites exist and CI never runs them - add them to "
            "the 'Rust and Python agree, module by module' step:\n  "
            + "\n  ".join(missing))

    def test_the_workflow_names_nothing_that_is_not_there(self):
        """The other direction: a renamed or deleted suite leaves a name in
        the workflow that fails the job with a module-not-found rather than
        with anything useful."""
        on_disk = {p.stem for p in (_REPO / "tests").glob("test_*_parity.py")}
        named = set(re.findall(r"tests\.(test_\w+_parity)\b",
                               _WORKFLOW.read_text()))
        self.assertEqual(sorted(named - on_disk), [])

    def test_the_step_requires_the_rust_side_rather_than_skipping_it(self):
        """Without `GMP_REQUIRE_RUST_HELPER=1` every one of these suites skips
        when the example is missing, which is the failure mode this whole file
        is about - a green job that ran nothing."""
        text = _WORKFLOW.read_text()
        step = text[text.index("Rust and Python agree, module by module"):]
        step = step[:step.index("\n      - name:")]
        self.assertIn('GMP_REQUIRE_RUST_HELPER: "1"', step)


if __name__ == "__main__":
    unittest.main()
