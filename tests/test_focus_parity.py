"""The Rust and Python focus modes run the same commands.

Focus mode quiets the desktop while a game is up: the file indexer suspended,
the screensaver held off, Do Not Disturb on. The half that matters is undoing
all three, because a focus mode that never lifts leaves somebody's indexer
paused and their notifications off with nothing on screen to say why.

The plan is carried out rather than compared as a list, because the two
stopping rules are only visible in what actually runs: the suspend path takes
the first tool that STARTS, and the cold restore takes the first that EXISTS.
Those are a line apart in the Python and produce the same output until a tool
is installed and broken.
"""

from __future__ import annotations

import itertools
import json
import os
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from tests._support import _SRC  # noqa: F401

from goblinmode import focus

_REPO = Path(__file__).resolve().parent.parent

TOOLS = ("balooctl6", "balooctl", "tracker3", "kwriteconfig6")


def _binary() -> Path | None:
    override = os.environ.get("GMP_FOCUS_BIN")
    if override:
        return Path(override) if Path(override).exists() else None
    for profile in ("debug", "release"):
        candidate = _REPO / "target" / profile / "examples" / "focus"
        if candidate.exists():
            return candidate
    return None


def _python(which: str, installed: set, state: dict, fails: set) -> dict:
    """Drive the real `FocusMode`, recording every command it runs."""
    ran: list = []
    markers: list = []

    def run(cmd):
        ran.append(list(cmd))
        return cmd[0] not in fails

    mode = focus.FocusMode.__new__(focus.FocusMode)
    mode._active = state.get("active", False)
    mode._ss_cookie = None
    mode._ss_proxy = None
    mode._baloo_suspended = state.get("baloo_suspended", False)
    mode._tracker_paused = state.get("tracker_paused", False)
    mode._kde_dnd = False
    mode._inhibit_idle = lambda: markers.append("inhibit")
    mode._uninhibit_idle = lambda: markers.append("uninhibit")

    def set_dnd(on):
        markers.append("dnd_on" if on else "dnd_off")

    mode._set_kde_dnd = set_dnd

    with patch.object(focus.shutil, "which",
                      lambda tool: f"/usr/bin/{tool}" if tool in installed else None), \
            patch.object(focus, "_run", run):
        if which == "exit":
            mode.exit()
        elif which == "restore":
            mode.force_restore()
        else:
            mode.enter()

    return {"ran": ran, "markers": markers,
            "baloo_suspended": mode._baloo_suspended,
            "tracker_paused": mode._tracker_paused}


def _cases():
    tool_sets = [
        set(TOOLS),
        set(),
        {"tracker3"},
        {"balooctl"},
        {"balooctl6"},
        {"balooctl6", "balooctl"},
        {"balooctl", "tracker3"},
        {"balooctl6", "tracker3", "kwriteconfig6"},
    ]
    states = [
        {},
        {"active": True},
        {"active": True, "baloo_suspended": True},
        {"active": True, "tracker_paused": True},
        {"active": True, "baloo_suspended": True, "tracker_paused": True},
    ]
    fail_sets = [set(), {"balooctl6"}, {"balooctl6", "balooctl"}, set(TOOLS)]
    yield from itertools.product(
        ("enter", "exit", "restore"), tool_sets, states, fail_sets)


class BothImplementationsRunTheSameCommands(unittest.TestCase):
    def setUp(self):
        self.binary = _binary()
        if self.binary is None:
            if os.environ.get("GMP_REQUIRE_RUST_HELPER") == "1":
                self.fail("GMP_REQUIRE_RUST_HELPER=1 but the focus example is "
                          "not built - run `cargo build -p gmp-core "
                          "--example focus`")
            self.skipTest("build it with `cargo build -p gmp-core "
                          "--example focus`")

    def _rust(self, cases) -> list:
        payload = {"cases": [
            {"which": which,
             "tools": {t: t in installed for t in TOOLS},
             "state": state,
             "fails": sorted(fails)}
            for which, installed, state, fails in cases]}
        r = subprocess.run([str(self.binary)], input=json.dumps(payload),
                           capture_output=True, text=True, timeout=120,
                           check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_every_combination_runs_the_same_commands(self):
        cases = list(_cases())
        got = self._rust(cases)
        for (which, installed, state, fails), answer in zip(cases, got, strict=True):
            with self.subTest(which=which, installed=sorted(installed),
                              state=sorted(state), fails=sorted(fails)):
                self.assertEqual(answer,
                                 _python(which, installed, state, fails))

    def test_a_broken_tool_hands_over_on_the_way_in_and_does_not_on_the_way_back(self):
        """The two stopping rules, side by side. Suspending needs a tool that
        STARTS, so a broken `balooctl6` gives `balooctl` a turn. The cold
        restore picks by existence, so a broken `balooctl6` is the end of it -
        both names are the same indexer and it has had its turn."""
        installed, fails = set(TOOLS), {"balooctl6"}
        entering = _python("enter", installed, {}, fails)
        self.assertEqual([c[0] for c in entering["ran"]],
                         ["balooctl6", "balooctl"])
        restoring = _python("restore", installed, {}, fails)
        self.assertEqual([c[0] for c in restoring["ran"]],
                         ["balooctl6", "tracker3"])
        cases = [("enter", installed, {}, fails), ("restore", installed, {}, fails)]
        self.assertEqual(self._rust(cases), [entering, restoring])

    def test_a_cold_restore_never_touches_the_screensaver(self):
        """It was held by a process that is gone, and the session released it
        when that process died - so there is no cookie left to hand back.
        Do Not Disturb is asked about either way, because that one is stored
        in a config file and outlives everything."""
        for installed in (set(TOOLS), set()):
            with self.subTest(installed=sorted(installed)):
                out = _python("restore", installed, {"active": True}, set())
                self.assertNotIn("inhibit", out["markers"])
                self.assertNotIn("uninhibit", out["markers"])
                self.assertEqual(out["markers"], ["dnd_off"])


if __name__ == "__main__":
    unittest.main()
