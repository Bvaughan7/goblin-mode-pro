import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tests._support import _SRC  # noqa: F401  (sets sys.path)

from goblinmode import config


class GameProfileValidation(unittest.TestCase):
    def test_nice_value_is_clamped(self):
        self.assertEqual(config.GameProfile(exe="a", nice_value=-99).nice_value, -10)
        self.assertEqual(config.GameProfile(exe="a", nice_value=99).nice_value, 19)

    def test_power_limits_clamped_non_negative(self):
        p = config.GameProfile(exe="a", pl1_w=-5, pl2_w=99999)
        self.assertEqual(p.pl1_w, 0)
        self.assertEqual(p.pl2_w, 500)

    def test_bad_match_mode_falls_back_to_exact(self):
        self.assertEqual(config.GameProfile(exe="a", match_mode="nope").match_mode, "exact")

    def test_core_pin_rejects_unknown(self):
        self.assertEqual(config.GameProfile(exe="a", core_pin="garbage").core_pin, "off")
        self.assertEqual(config.GameProfile(exe="a", core_pin="cache0").core_pin, "cache0")

    def test_gamescope_defaults_and_clamps(self):
        p = config.GameProfile(exe="a", gamescope={"w": -1, "h": 999999, "upscale": "bogus"})
        self.assertEqual(p.gamescope["w"], 0)
        self.assertEqual(p.gamescope["h"], 10000)
        self.assertEqual(p.gamescope["upscale"], "off")
        self.assertIn("borderless", p.gamescope)

    def test_exe_rejects_path_separators_and_traversal(self):
        for bad in ("../x", "a/b", "a\\b", "..", "a\x00b", "x" * 200):
            with self.assertRaises(ValueError):
                config.GameProfile(exe=bad)

    def test_exe_strips_quotes(self):
        self.assertEqual(config.GameProfile(exe='"Wow.exe"').exe, "Wow.exe")


class SettingsRoundTrip(unittest.TestCase):
    def test_poll_interval_clamped(self):
        self.assertEqual(config.Settings(poll_interval=1).poll_interval, 3)
        self.assertEqual(config.Settings(poll_interval=999).poll_interval, 30)

    def test_corrupt_profile_is_dropped_not_fatal(self):
        raw = {
            "profiles": [
                {"exe": "Good.exe"},
                {"exe": "../evil"},          # invalid -> dropped
                "not-a-dict",                 # ignored
                {"exe": "Also.exe", "nice_value": "abc"},  # bad type -> dropped
            ]
        }
        settings = config._from_dict(raw)
        self.assertEqual([p.exe for p in settings.profiles], ["Good.exe"])

    def test_save_then_load(self):
        with TemporaryDirectory() as d:
            path = Path(d) / "config.json"
            config.CONFIG_FILE = path
            s = config.Settings(profiles=[config.GameProfile(exe="Wow.exe", nice_value=-7)])
            config.save(s)
            loaded = config.load()
            self.assertEqual(loaded.profiles[0].exe, "Wow.exe")
            self.assertEqual(loaded.profiles[0].nice_value, -7)
            self.assertEqual(json.loads(path.read_text())["schema_version"], config.SCHEMA_VERSION)


if __name__ == "__main__":
    unittest.main()
