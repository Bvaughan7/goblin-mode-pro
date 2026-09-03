"""The Rust and Python launch resolution agree.

This is the code path that runs for every single game launch, and a mistake
in it is the hardest kind to notice: the game starts, it just starts without
the tuning, or with another game's. So the corpus is weighted towards the
ways a match goes wrong rather than the ways it goes right - Windows paths,
quoting, case, a profile list where order decides, and patterns that only one
of the two regex engines will compile.

That last group is a real and documented divergence, not a bug to fix.
Python's ``re`` accepts lookaround and conditionals; Rust's ``regex`` refuses
both by design. A pattern using them behaves on the Rust side as one that
failed to compile - the profile is skipped and the game launches untuned,
which is the safe direction and is what already happens today for a malformed
pattern. Those cases are listed separately and asserted to differ in exactly
that way, rather than being left out of the corpus and forgotten.

The divergence is narrower than it first looks, and for a reason worth
knowing: ``sanitize_exe`` rejects backslashes as path separators, so a regex
profile can contain **no escape sequence at all** - no ``\.``, no ``\d``, no
backreference. Whatever the two engines do differently with escapes is
therefore unreachable from a stored profile.
"""

from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from tests._support import _SRC, typed  # noqa: F401

from goblinmode import runner
from goblinmode.config import Settings, _from_dict

_REPO = Path(__file__).resolve().parent.parent
MANGOHUD_DIR = "/home/u/.config/MangoHud"


def _binary() -> Path | None:
    override = os.environ.get("GMP_RUNNER_BIN")
    if override:
        return Path(override) if Path(override).exists() else None
    for profile in ("debug", "release"):
        candidate = _REPO / "target" / profile / "examples" / "runner"
        if candidate.exists():
            return candidate
    return None


def p(exe, **over):
    return {"exe": exe, **over}


def cfg(*profiles, **settings):
    return {"profiles": list(profiles), **settings}


NO_VARS = {"nvapi": False, "fsync": False, "no_esync": False, "dxvk_async": False}

CASES = {
    "no_profiles": (cfg(), ["/x/Wow.exe"]),
    "no_argv": (cfg(p("Wow.exe")), []),
    "empty_argv_token": (cfg(p("Wow.exe")), [""]),
    # -- exact matching, which is on the basename -------------------------
    "exact_plain": (cfg(p("Wow.exe")), ["Wow.exe"]),
    "exact_unix_path": (cfg(p("Wow.exe")), ["/games/wow/Wow.exe"]),
    "exact_windows_path": (cfg(p("Wow.exe")), ["C:\\Games\\World of Warcraft\\Wow.exe"]),
    "exact_mixed_separators": (cfg(p("Wow.exe")), ["Z:/games\\wow/Wow.exe"]),
    "exact_quoted": (cfg(p("Wow.exe")), ['"C:\\Games\\Wow.exe"']),
    "exact_single_quoted": (cfg(p("Wow.exe")), ["'Wow.exe'"]),
    "exact_padded": (cfg(p("Wow.exe")), ["   Wow.exe   "]),
    "exact_case_differs": (cfg(p("Wow.exe")), ["WOW.EXE"]),
    "exact_profile_uppercase": (cfg(p("WOW.EXE")), ["wow.exe"]),
    "exact_substring_of_token": (cfg(p("Wow.exe")), ["NotWow.exe"]),
    "exact_in_later_arg": (cfg(p("Wow.exe")), ["proton", "run", "C:\\Wow.exe"]),
    "exact_trailing_separator": (cfg(p("Wow.exe")), ["C:\\Games\\"]),
    "exact_only_separators": (cfg(p("Wow.exe")), ["\\\\", "//"]),
    "exact_unicode_exe": (cfg(p("ゲーム.exe")), ["C:\\g\\ゲーム.exe"]),
    "exact_turkish_i": (cfg(p("GAMEI.exe")), ["gamei.exe"]),
    # -- substring matching, on the joined command line --------------------
    "substring_plain": (cfg(p("rs2client", match_mode="substring")), ["/opt/rs2client"]),
    "substring_across_args": (cfg(p("client -x", match_mode="substring")),
                              ["client", "-x"]),
    "substring_case": (cfg(p("RS2Client", match_mode="substring")), ["/opt/rs2client"]),
    "substring_no_match": (cfg(p("rs2client", match_mode="substring")), ["/opt/other"]),
    "substring_in_a_flag": (cfg(p("wow", match_mode="substring")), ["--show-wow"]),
    # -- regex, patterns both engines accept -------------------------------
    "regex_simple": (cfg(p("Wow.*exe", match_mode="regex")), ["Wow64.exe"]),
    "regex_anchored": (cfg(p("^Wow", match_mode="regex")), ["Wow.exe"]),
    "regex_anchored_no_match": (cfg(p("^Wow", match_mode="regex")), ["xWow.exe"]),
    "regex_char_class": (cfg(p("[Ww]ow[0-9]*", match_mode="regex")), ["/x/wow64"]),
    "regex_alternation": (cfg(p("wow|runescape", match_mode="regex")), ["/x/runescape"]),
    "regex_dollar": (cfg(p("exe$", match_mode="regex")), ["Wow.exe"]),
    # An escaped dot cannot be stored at all: sanitize_exe bans backslashes as
    # path separators, so NO escape sequence survives into a pattern. Both
    # implementations therefore drop this profile before any regex engine sees
    # it - which is why the two engines' escape handling never comes up.
    "regex_escaped_dot_is_not_storable": (cfg(p(r"Wow\.exe", match_mode="regex")),
                                          ["Wow.exe"]),
    "regex_digit_class_is_not_storable": (cfg(p(r"Wow\d+", match_mode="regex")),
                                          ["Wow64"]),
    "regex_no_match": (cfg(p("^zzz", match_mode="regex")), ["Wow.exe"]),
    "regex_uncompilable": (cfg(p("*[bad", match_mode="regex"), p("Wow.exe")),
                           ["Wow.exe"]),
    "regex_unclosed_group": (cfg(p("(a", match_mode="regex")), ["a"]),
    "regex_matches_full_path": (cfg(p("Games", match_mode="regex")),
                                ["C:\\Games\\Wow.exe"]),
    # The pattern is truncated to 128 characters before compiling, which is
    # the ReDoS guard. exe itself is capped at 128, so this is exactly the
    # boundary: the 128th character has to still be part of the pattern.
    "regex_at_the_pattern_length_bound": (
        cfg(p("x" * 127 + "y", match_mode="regex")), ["x" * 127]),
    "regex_pattern_one_under_the_bound": (
        cfg(p("x" * 126 + "y", match_mode="regex")), ["x" * 126 + "y"]),
    # And each argument is truncated to 4096 characters before searching, so
    # a match that lands on the last one has to still be found.
    "regex_at_the_search_length_bound": (
        cfg(p("y$", match_mode="regex")), ["x" * 4095 + "y"]),
    "regex_past_the_search_length_bound": (
        cfg(p("y$", match_mode="regex")), ["x" * 4096 + "y"]),
    # -- profile ordering and enablement -----------------------------------
    "first_of_two_matches": (cfg(p("game", display_name="first", match_mode="substring"),
                                 p("game", display_name="second", match_mode="substring")),
                             ["/x/game"]),
    "disabled_is_skipped": (cfg(p("Wow.exe", enabled=False), p("Wow.exe",
                                display_name="second")), ["Wow.exe"]),
    "all_disabled": (cfg(p("Wow.exe", enabled=False)), ["Wow.exe"]),
    "master_off": (cfg(p("Wow.exe"), master_enabled=False), ["Wow.exe"]),
    "earlier_profile_does_not_match": (cfg(p("Other.exe"), p("Wow.exe")), ["Wow.exe"]),
    # -- the environment a match implies -----------------------------------
    "all_runner_vars": (cfg(p("Wow.exe", runner_vars={"nvapi": True, "fsync": True,
                                                      "no_esync": True,
                                                      "dxvk_async": True})),
                        ["Wow.exe"]),
    "no_runner_vars": (cfg(p("Wow.exe", runner_vars=NO_VARS)), ["Wow.exe"]),
    "gpu_tuning_radv": (cfg(p("Wow.exe", runner_vars=NO_VARS,
                              gpu_tuning={"radv_gpl": True, "radv_nggc": True})),
                        ["Wow.exe"]),
    "gpu_tuning_nvidia": (cfg(p("Wow.exe", runner_vars=NO_VARS,
                                gpu_tuning={"threaded_gl": True, "shader_cache": True})),
                          ["Wow.exe"]),
    "gpu_tuning_everything": (cfg(p("Wow.exe", runner_vars=NO_VARS, gpu_tuning={
                                  "threaded_gl": True, "shader_cache": True,
                                  "force_gsync": True, "max_fps_none": True,
                                  "glthread": True, "radv_gpl": True,
                                  "radv_nggc": True, "radv_rt": True,
                                  "anv_gpl": True})), ["Wow.exe"]),
    "mangohud_overlay": (cfg(p("Wow.exe", runner_vars=NO_VARS,
                               mangohud={"enabled": True})), ["Wow.exe"]),
    "watchdog_only": (cfg(p("Wow.exe", runner_vars=NO_VARS, fps_watchdog=True)),
                      ["Wow.exe"]),
    "per_game_mangohud": (cfg(p("Wow.exe", runner_vars=NO_VARS, fps_watchdog=True,
                                per_game_mangohud=True)), ["Wow.exe"]),
    "per_game_without_overlay": (cfg(p("Wow.exe", runner_vars=NO_VARS,
                                       per_game_mangohud=True)), ["Wow.exe"]),
    "per_game_unicode_exe": (cfg(p("ゲーム.exe", runner_vars=NO_VARS, fps_watchdog=True,
                                   per_game_mangohud=True)), ["ゲーム.exe"]),
    # -- gamescope ----------------------------------------------------------
    "gamescope_off": (cfg(p("Wow.exe")), ["Wow.exe"]),
    "gamescope_defaults": (cfg(p("Wow.exe", gamescope_enabled=True)), ["Wow.exe"]),
    "gamescope_full": (cfg(p("Wow.exe", gamescope_enabled=True,
                             gamescope={"w": 1920, "h": 1080, "refresh": 144,
                                        "upscale": "fsr", "hdr": True,
                                        "borderless": False, "steam_overlay": True})),
                       ["Wow.exe"]),
    "gamescope_nis": (cfg(p("Wow.exe", gamescope_enabled=True,
                            gamescope={"upscale": "nis"})), ["Wow.exe"]),
    "gamescope_integer": (cfg(p("Wow.exe", gamescope_enabled=True,
                                gamescope={"upscale": "integer"})), ["Wow.exe"]),
    "gamescope_width_only": (cfg(p("Wow.exe", gamescope_enabled=True,
                                   gamescope={"w": 1920, "h": 0})), ["Wow.exe"]),
    "gamescope_height_only": (cfg(p("Wow.exe", gamescope_enabled=True,
                                    gamescope={"w": 0, "h": 1080})), ["Wow.exe"]),
    "gamescope_refresh_only": (cfg(p("Wow.exe", gamescope_enabled=True,
                                     gamescope={"refresh": 60})), ["Wow.exe"]),
    "gamescope_no_overlay": (cfg(p("Wow.exe", gamescope_enabled=True,
                                   gamescope={"steam_overlay": False})), ["Wow.exe"]),
    "gamescope_out_of_range": (cfg(p("Wow.exe", gamescope_enabled=True,
                                     gamescope={"w": 99999, "h": -5})), ["Wow.exe"]),
    # -- gamemode -----------------------------------------------------------
    "gamemode_on": (cfg(p("Wow.exe", use_gamemode=True)), ["Wow.exe"]),
    "gamemode_off": (cfg(p("Wow.exe", use_gamemode=False)), ["Wow.exe"]),
    "gamemode_unmatched": (cfg(p("Other.exe")), ["Wow.exe"]),
}

# Patterns Python's `re` compiles and Rust's `regex` refuses by design. The
# Rust behaves as it does for any pattern that will not compile: it skips the
# profile. Listed so the divergence is pinned rather than forgotten.
# Backreferences are absent from this list on purpose: `\1` needs a backslash,
# and sanitize_exe rejects the profile before the pattern reaches an engine.
REGEX_ONLY_IN_PYTHON = {
    "lookahead": (cfg(p("Wow(?!64)", match_mode="regex")), ["Wow.exe"]),
    "lookbehind": (cfg(p("(?<=x)Wow", match_mode="regex")), ["xWow"]),
    "conditional": (cfg(p(r"(a)?(?(1)b|c)", match_mode="regex")), ["ab"]),
}


class BothImplementationsAgree(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.binary = _binary()
        if self.binary is None:
            if os.environ.get("GMP_REQUIRE_RUST_HELPER") == "1":
                self.fail("GMP_REQUIRE_RUST_HELPER=1 but the runner example is not "
                          "built - run `cargo build -p gmp-core --example runner`")
            self.skipTest("build it with `cargo build -p gmp-core --example runner`")

    def _rust(self, settings, argv) -> dict:
        payload = {"settings": settings, "argv": argv, "mangohud_dir": MANGOHUD_DIR}
        r = subprocess.run([str(self.binary)], input=json.dumps(payload),
                           capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    @staticmethod
    def _python(settings: dict, argv: list) -> dict:
        s: Settings = _from_dict(settings)
        with patch("goblinmode.paths.MANGOHUD_DIR", Path(MANGOHUD_DIR)):
            matched = runner.resolve_profile_for_argv(argv, s)
            return {
                "profile": matched.exe if matched else None,
                "display_name": matched.display_name if matched else None,
                "env": runner.print_env_for(argv, s),
                "gamescope": runner.print_gamescope(argv, s),
                "gamemode": runner.print_gamemode(argv, s),
                "gamescope_args": runner.gamescope_args(matched) if matched else None,
                "session": runner.gamescope_session_argv(matched),
                "basenames": [runner._basename(a) for a in argv],
            }

    def test_every_launch_resolves_the_same_way(self):
        for label, (settings, argv) in CASES.items():
            with self.subTest(label):
                self.assertEqual(typed(self._rust(settings, argv)),
                                 typed(self._python(settings, argv)))

    def test_the_regex_divergence_is_exactly_where_it_is_documented(self):
        for label, (settings, argv) in REGEX_ONLY_IN_PYTHON.items():
            with self.subTest(label):
                got = self._rust(settings, argv)
                want = self._python(settings, argv)
                self.assertIsNotNone(want["profile"],
                                     "the corpus entry should match in Python")
                self.assertIsNone(got["profile"],
                                  "Rust should skip an uncompilable pattern")
                # And skipping means a plain launch, not a partial one.
                self.assertEqual(got["env"], "")
                self.assertEqual(got["gamescope"], "")
                self.assertEqual(got["gamemode"], "1")


if __name__ == "__main__":
    unittest.main()
