"""Both fake bridges implement everything the GUI actually calls.

The GUI talks to the daemon through one object, and two stand-ins pretend to be
it: `tests/gui_smoke.py` (CI) and `scripts/make-demo.py` (the README's demo
animation). Adding a bridge call to a page silently breaks whichever stand-in
nobody re-runs.

That is not hypothetical. `make-demo.py` had been dead for three releases -
`get_health_async` was added to MainWindow and the demo's fake never grew it -
so `docs/demo.gif`, the first image in the README, could not be regenerated and
quietly went stale through an entire UI redesign. This test reads the bridge
calls straight out of the GUI source and fails the build when either stand-in
falls behind.

Static analysis on purpose: it needs no GTK, so it runs in the ordinary suite
rather than only in the smoke-test job.
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

from tests._support import _SRC  # noqa: F401

_REPO = Path(__file__).resolve().parent.parent
_GUI = _REPO / "src" / "goblinmode" / "gui"

#: not bridge *calls* - state the GUI reads or callbacks it registers
_NOT_METHODS = {"available"}


def _bridge_calls() -> set[str]:
    """Every `bridge.<name>` the GUI source touches."""
    found: set[str] = set()
    for path in _GUI.rglob("*.py"):
        for m in re.finditer(r"\bbridge\.([a-z_][a-z0-9_]*)", path.read_text()):
            found.add(m.group(1))
    return found - _NOT_METHODS


def _defined_names(path: Path, class_name: str) -> set[str]:
    """Method and attribute names on a class, by parsing - no import, so this
    works for scripts/make-demo.py without pulling in GTK or Pillow."""
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            names = {n.name for n in node.body if isinstance(n, ast.FunctionDef)}
            for n in node.body:
                if isinstance(n, ast.Assign):
                    names |= {t.id for t in n.targets if isinstance(t, ast.Name)}
            return names
    raise AssertionError(f"{class_name} not found in {path}")


class FakeBridgeCoverage(unittest.TestCase):
    def test_the_gui_calls_something(self):
        """Guard the guard: a regex that matched nothing would pass silently."""
        self.assertGreater(len(_bridge_calls()), 15)

    def test_the_smoke_test_bridge_is_complete(self):
        missing = _bridge_calls() - _defined_names(
            _REPO / "tests" / "gui_smoke.py", "_FakeBridge")
        self.assertEqual(missing, set(),
                         f"gui_smoke.py's _FakeBridge is missing: {sorted(missing)}")

    def test_the_demo_bridge_is_complete(self):
        missing = _bridge_calls() - _defined_names(
            _REPO / "scripts" / "make-demo.py", "FakeBridge")
        self.assertEqual(missing, set(),
                         f"make-demo.py's FakeBridge is missing: {sorted(missing)} - "
                         "docs/demo.gif cannot be regenerated until these exist")

    def test_the_real_client_has_them_too(self):
        """The GUI must not call something the actual BridgeClient lacks."""
        missing = _bridge_calls() - _defined_names(
            _REPO / "src" / "goblinmode" / "ipc" / "daemon_bridge.py", "BridgeClient")
        self.assertEqual(missing, set(),
                         f"BridgeClient is missing: {sorted(missing)}")


if __name__ == "__main__":
    unittest.main()
