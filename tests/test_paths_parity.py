"""The Rust and Python resolve the same locations from the same environment.

Three processes read these files - the daemon (a systemd *user* service), the
GUI, and the `goblin-run` wrapper that runs in front of every game - and they
only ever meet on disk. A daemon and a GUI that disagree about where
`config.json` lives do not fail loudly; they quietly stop seeing each other's
writes. So this has to be exact before the Rust daemon reads anything.

The corpus is mostly environments that are odd rather than absent, because
that is where the two derivations in `paths.py` had already drifted apart from
each other. `XDG_CONFIG_HOME` set but EMPTY - which is what the spec means by
unset, and what several launchers produce - was handled by the shared helper
and not by the line that derived MangoHud's directory, so that one became the
relative path `MangoHud`. The visible effect was `MANGOHUD_CONFIGFILE` being
exported into the game's environment as a relative path, so the per-game
overlay config resolved against the *game's* working directory and silently
never applied, plus a stray `MangoHud/` created wherever the daemon happened
to be running from.
"""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from tests._support import _SRC, typed  # noqa: F401

_REPO = Path(__file__).resolve().parent.parent

#: Every name the module exports as a location, so a new one cannot be added
#: to one implementation and forgotten in the other.
FIELDS = {
    "config_dir": "CONFIG_DIR", "state_dir": "STATE_DIR", "data_dir": "DATA_DIR",
    "cache_dir": "CACHE_DIR", "config_file": "CONFIG_FILE",
    "game_log_dir": "GAME_LOG_DIR", "incident_file": "INCIDENT_FILE",
    "session_file": "SESSION_FILE", "mangohud_log_dir": "MANGOHUD_LOG_DIR",
    "applied_state_file": "APPLIED_STATE_FILE", "onboarded_marker": "ONBOARDED_MARKER",
    "mangohud_dir": "MANGOHUD_DIR", "mangohud_conf": "MANGOHUD_CONF",
    "local_bin": "LOCAL_BIN", "runner_wrapper": "RUNNER_WRAPPER",
    "helper_runtime_dir": "HELPER_RUNTIME_DIR", "helper_state_file": "HELPER_STATE_FILE",
}

HOME = "/home/u"


def _binary() -> Path | None:
    override = os.environ.get("GMP_PATHS_BIN")
    if override:
        return Path(override) if Path(override).exists() else None
    for profile in ("debug", "release"):
        candidate = _REPO / "target" / profile / "examples" / "paths"
        if candidate.exists():
            return candidate
    return None


def env(**over) -> dict:
    return {"HOME": HOME, **over}


CASES = {
    "nothing_set": env(),
    # Set but empty is the spec's way of saying unset, and is what an
    # environment that clears a variable rather than unsetting it produces.
    "config_empty": env(XDG_CONFIG_HOME=""),
    "config_spaces": env(XDG_CONFIG_HOME="   "),
    "config_tab": env(XDG_CONFIG_HOME="\t"),
    "config_newline": env(XDG_CONFIG_HOME="\n"),
    "all_empty": env(XDG_CONFIG_HOME="", XDG_STATE_HOME="", XDG_DATA_HOME="",
                     XDG_CACHE_HOME=""),
    # Ordinary overrides.
    "config_set": env(XDG_CONFIG_HOME="/tmp/cfg"),
    "state_set": env(XDG_STATE_HOME="/tmp/state"),
    "data_set": env(XDG_DATA_HOME="/tmp/data"),
    "cache_set": env(XDG_CACHE_HOME="/tmp/cache"),
    "all_set": env(XDG_CONFIG_HOME="/tmp/cfg", XDG_STATE_HOME="/tmp/state",
                   XDG_DATA_HOME="/tmp/data", XDG_CACHE_HOME="/tmp/cache"),
    # Shapes a real environment can carry.
    "trailing_slash": env(XDG_DATA_HOME="/tmp/data/"),
    "double_trailing_slash": env(XDG_DATA_HOME="/tmp/data//"),
    "surrounding_whitespace": env(XDG_CONFIG_HOME="  /tmp/cfg  "),
    "tilde": env(XDG_CONFIG_HOME="~"),
    "tilde_slash": env(XDG_CONFIG_HOME="~/cfg"),
    # `~//x` is `~/x` once the separators collapse, so it still expands.
    "tilde_double_slash": env(XDG_CONFIG_HOME="~//cfg"),
    "tilde_trailing_slashes": env(XDG_CONFIG_HOME="~//"),
    # Refused on both sides rather than honoured - pathlib raises for a user
    # who does not exist, and this module is imported by four processes.
    "tilde_username": env(XDG_CONFIG_HOME="~nosuchuser/cfg"),
    "tilde_real_username": env(XDG_CONFIG_HOME="~root/cfg"),
    "tilde_username_bare": env(XDG_CONFIG_HOME="~nosuchuser"),
    # A tilde only expands as the FIRST component of a relative path. Under an
    # anchor, or anywhere else, it is an ordinary directory name.
    "tilde_under_a_root": env(XDG_CONFIG_HOME="/~/cfg"),
    "tilde_in_the_middle": env(XDG_CONFIG_HOME="cfg/~/x"),
    # POSIX reserves exactly two leading slashes; three or more collapse.
    "double_slash_root": env(XDG_CONFIG_HOME="//srv"),
    "triple_slash_root": env(XDG_CONFIG_HOME="///srv"),
    "double_slash_alone": env(XDG_CONFIG_HOME="//"),
    "inner_double_slash": env(XDG_DATA_HOME="/tmp//data"),
    "dot_component": env(XDG_DATA_HOME="/tmp/./data"),
    "dotdot_component": env(XDG_DATA_HOME="/tmp/../data"),
    "relative": env(XDG_CONFIG_HOME="relative"),
    "dot": env(XDG_CONFIG_HOME="."),
    "root": env(XDG_CONFIG_HOME="/"),
    "deep": env(XDG_DATA_HOME="/a/b/c/d/e"),
    "unicode": env(XDG_DATA_HOME="/home/u/ゲーム"),
    "space_inside": env(XDG_DATA_HOME="/home/u/my games"),
    # A different home, which everything unset derives from.
    "other_home": {"HOME": "/var/lib/gaming"},
    "home_with_trailing_slash": {"HOME": "/home/u/"},
}


class BothImplementationsAgree(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.binary = _binary()
        if self.binary is None:
            if os.environ.get("GMP_REQUIRE_RUST_HELPER") == "1":
                self.fail("GMP_REQUIRE_RUST_HELPER=1 but the paths example is not "
                          "built - run `cargo build -p gmp-core --example paths`")
            self.skipTest("build it with `cargo build -p gmp-core --example paths`")

    def _rust(self, environment: dict) -> dict:
        r = subprocess.run([str(self.binary)], input=json.dumps(environment),
                           capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    @staticmethod
    def _python(environment: dict) -> dict:
        # paths.py resolves at import, so the module is reloaded under the
        # environment being asked about. `clear=True` matters: a variable left
        # over from this process would answer a question nobody asked.
        with patch.dict(os.environ, environment, clear=True):
            import goblinmode.paths as module
            importlib.reload(module)
            resolved = {field: str(getattr(module, name))
                        for field, name in FIELDS.items()}
        import goblinmode.paths as module
        importlib.reload(module)  # leave the process as it was found
        return resolved

    def test_every_environment_resolves_the_same_way(self):
        for label, environment in CASES.items():
            with self.subTest(label):
                self.assertEqual(typed(self._rust(environment)),
                                 typed(self._python(environment)))

    def test_the_two_implementations_export_the_same_set_of_locations(self):
        """A location added to one side and not the other is the real risk."""
        self.assertEqual(set(self._rust(env())), set(FIELDS))

    def test_no_location_is_relative_however_odd_the_environment(self):
        """The bug this found: a relative path here is silently wrong.

        Nothing raises. The daemon writes somewhere unexpected, or exports a
        relative path to a game whose working directory is somewhere else
        again, and the feature just does not work.
        """
        for label, environment in CASES.items():
            # These ask for a relative base explicitly, so they are the only
            # environments allowed to produce one. Everything else - and
            # especially every way of writing "unset" - must land on an
            # absolute path, because a relative one here fails silently: the
            # daemon writes somewhere unexpected, or exports a relative
            # MANGOHUD_CONFIGFILE that resolves against the game's own working
            # directory and never applies.
            if label in ("relative", "dot", "tilde_in_the_middle"):
                continue
            with self.subTest(label):
                for field, value in self._python(environment).items():
                    self.assertTrue(value.startswith("/"),
                                    f"{field} is relative: {value!r}")


if __name__ == "__main__":
    unittest.main()
