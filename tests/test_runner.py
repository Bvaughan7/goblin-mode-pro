import unittest

from tests._support import _SRC  # noqa: F401

from goblinmode import runner
from goblinmode.config import GameProfile, Settings


class Basename(unittest.TestCase):
    def test_splits_unix_and_windows_paths(self):
        self.assertEqual(runner._basename("/opt/games/Wow.exe"), "Wow.exe")
        self.assertEqual(runner._basename(r"C:\\Games\\Wow.exe"), "Wow.exe")
        self.assertEqual(runner._basename('"rs2client"'), "rs2client")


class ProfileResolution(unittest.TestCase):
    def _settings(self):
        return Settings(profiles=[
            GameProfile(exe="Wow.exe", match_mode="exact"),
            GameProfile(exe="rs2client", match_mode="substring"),
        ])

    def test_exact_match_on_basename(self):
        p = runner.resolve_profile_for_argv(["/x/Wow.exe", "-opengl"], self._settings())
        self.assertIsNotNone(p)
        self.assertEqual(p.exe, "Wow.exe")

    def test_substring_match_anywhere(self):
        p = runner.resolve_profile_for_argv(["/usr/bin/env", "run-rs2client-thing"], self._settings())
        self.assertEqual(p.exe, "rs2client")

    def test_no_match_returns_none(self):
        self.assertIsNone(runner.resolve_profile_for_argv(["/bin/true"], self._settings()))


class EnvPrinting(unittest.TestCase):
    def test_print_env_for_emits_validated_lines(self):
        s = Settings(profiles=[GameProfile(
            exe="Wow.exe",
            runner_vars={"nvapi": True, "fsync": True, "no_esync": False, "dxvk_async": False},
        )])
        out = runner.print_env_for(["/x/Wow.exe"], s)
        lines = dict(line.split("=", 1) for line in out.splitlines())
        self.assertEqual(lines["WINEFSYNC"], "1")
        self.assertEqual(lines["PROTON_ENABLE_NVAPI"], "1")
        self.assertNotIn("PROTON_NO_ESYNC", lines)

    def test_env_names_and_values_are_shell_safe(self):
        # the regexes that gate what the wrapper is allowed to export
        self.assertTrue(runner._ENV_NAME_RE.match("PROTON_LOG"))
        self.assertFalse(runner._ENV_NAME_RE.match("bad name"))
        self.assertFalse(runner._ENV_NAME_RE.match("2FOO"))
        self.assertFalse(runner._ENV_VALUE_RE.match("has\nnewline"))

    def test_a_trailing_newline_is_not_a_valid_name_or_value(self):
        # Python's `$` also matches just before a trailing newline, so the
        # obvious ^...$ spelling accepts "FOO\n" - which print_env_for emits as
        # "FOO\n=1" and the wrapper reads back as `FOO` with an empty value,
        # dropping the setting with nothing logged. The anchor is \Z for that
        # reason and this is the test that says so.
        self.assertFalse(runner._ENV_NAME_RE.match("PROTON_LOG\n"))
        self.assertFalse(runner._ENV_VALUE_RE.match("1\n"))
        self.assertTrue(runner._ENV_VALUE_RE.match("1"))


class GamescopeArgs(unittest.TestCase):
    def test_disabled_returns_empty(self):
        self.assertEqual(runner.gamescope_args(GameProfile(exe="a")), [])

    def test_full_arg_line(self):
        p = GameProfile(exe="a", gamescope_enabled=True, gamescope={
            "w": 1920, "h": 1080, "refresh": 60, "upscale": "fsr",
            "hdr": False, "borderless": True, "steam_overlay": True,
        })
        args = runner.gamescope_args(p)
        self.assertEqual(args[:6], ["-W", "1920", "-H", "1080", "-r", "60"])
        self.assertIn("-F", args)
        self.assertIn("fsr", args)
        self.assertIn("-b", args)
        self.assertIn("-e", args)

    def test_wrapper_template_never_evals_or_sources(self):
        for line in runner._WRAPPER_TEMPLATE.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            self.assertFalseStartsWith(stripped, "eval ")
            self.assertFalseStartsWith(stripped, "source ")
            self.assertNotIn("| sh", stripped)
        self.assertIn("goblin-mode-pro-daemon --print-gamescope", runner._WRAPPER_TEMPLATE)

    def assertFalseStartsWith(self, text, prefix):
        self.assertFalse(text.startswith(prefix), f"{text!r} starts with {prefix!r}")

    def test_print_gamemode_reflects_the_per_game_toggle(self):
        from goblinmode.config import Settings

        s = Settings(profiles=[
            GameProfile(exe="On.exe", use_gamemode=True),
            GameProfile(exe="Off.exe", use_gamemode=False),
        ])
        self.assertEqual(runner.print_gamemode(["/x/On.exe"], s), "1")
        self.assertEqual(runner.print_gamemode(["/x/Off.exe"], s), "0")
        self.assertEqual(runner.print_gamemode(["/x/Unknown.exe"], s), "1")

    def test_wrapper_consults_print_gamemode(self):
        self.assertIn("--print-gamemode", runner._WRAPPER_TEMPLATE)

    def test_wrapper_env_name_guard_anchors_the_whole_token(self):
        # the old guard was `case "$gmp_k" in [A-Za-z_]*)` which also matched
        # "A B" and "A;x" - the fix anchors both ends of the identifier.
        self.assertIn("=~ ^[A-Za-z_][A-Za-z0-9_]*$", runner._WRAPPER_TEMPLATE)
        self.assertNotIn("[A-Za-z_]*) export", runner._WRAPPER_TEMPLATE)


class GamescopeSessionArgv(unittest.TestCase):
    def test_no_profile_defaults_to_steam_big_picture(self):
        argv = runner.gamescope_session_argv(None)
        self.assertEqual(argv[0], "gamescope")
        self.assertIn("--", argv)
        self.assertEqual(argv[argv.index("--") + 1:], runner.DEFAULT_SESSION_COMMAND)

    def test_no_profile_uses_generic_borderless_args(self):
        argv = runner.gamescope_session_argv(None)
        self.assertIn("-b", argv[:argv.index("--")])
        self.assertIn("-e", argv[:argv.index("--")])

    def test_profile_args_are_reused_from_gamescope_args(self):
        p = GameProfile(exe="a", gamescope_enabled=True,
                         gamescope={"w": 1280, "h": 800, "refresh": 0,
                                    "upscale": "off", "hdr": False,
                                    "borderless": True, "steam_overlay": True})
        argv = runner.gamescope_session_argv(p)
        self.assertEqual(argv[1:argv.index("--")], runner.gamescope_args(p))

    def test_explicit_command_overrides_default(self):
        argv = runner.gamescope_session_argv(None, ["my-launcher", "--fullscreen"])
        self.assertEqual(argv[argv.index("--") + 1:], ["my-launcher", "--fullscreen"])


if __name__ == "__main__":
    unittest.main()
