"""The Rust and Python config loaders agree, in both directions.

This is the module where a divergence costs a user their settings rather than
a frame rate, so the comparison is a full round trip: take a config file, load
it, normalise it, write it back, and diff the result key by key. A key that
goes missing between reading and writing is a setting somebody loses.

Two properties are pinned deliberately, because both are surprising and both
are load-bearing:

* **Unknown keys are dropped, not preserved.** A key written by a newer build
  does not survive an older build reading and saving the file. That is what
  the Python has always done, so the Rust does it too - a port that quietly
  improved on it would round-trip files the Python would not, and the two
  would stop being interchangeable.

* **Nothing is coerced.** Python's dataclasses keep whatever the file held, so
  a bool field containing "yes" stays the string "yes". A strictly typed Rust
  would reject the profile and the user would watch a game's settings vanish,
  so the loosely-used fields are carried as raw JSON on both sides.

The corpus is built from what a config file looks like after somebody edits it
by hand, an import goes wrong, or a downgrade happens - not from what the GUI
writes.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import subprocess
import unittest
from dataclasses import asdict
from pathlib import Path

from tests._support import _SRC  # noqa: F401

from goblinmode import config as C

_REPO = Path(__file__).resolve().parent.parent


def _binary() -> Path | None:
    override = os.environ.get("GMP_CONFIG_BIN")
    if override:
        return Path(override) if Path(override).exists() else None
    for profile in ("debug", "release"):
        candidate = _REPO / "target" / profile / "examples" / "config"
        if candidate.exists():
            return candidate
    return None


def cfg(*profiles, **settings) -> dict:
    return {"profiles": list(profiles), **settings}


FULL_PROFILE = {
    "exe": "Wow.exe", "display_name": "World of Warcraft", "enabled": True,
    "match_mode": "exact", "auto_created": False, "renice_enabled": True,
    "nice_value": -5, "use_gamemode": True, "core_pin": "performance",
    "scx_scheduler": "lavd", "scx_mode": "gaming", "tearing_enabled": True,
    "refresh_rate_hz": 144, "adaptive_sync_enabled": True,
    "vrr_outputs": ["eDP-1", "DP-2"], "governor_boost": True, "focus_mode": True,
    "power_limit_enabled": True, "pl1_w": 45, "pl2_w": 60,
    "battery_pl1_w": 15, "battery_pl2_w": 25, "undervolt_reapply": True,
    "amd_undervolt_reapply": False, "fan_spinup_enabled": True,
    "per_game_mangohud": True,
    "mangohud": {"enabled": True, "fps": True, "cpu_temp": False, "gpu_temp": True,
                 "ram": True, "frame_timing": True},
    "fps_watchdog": True, "fps_dip_floor": 30, "fps_dip_ratio": 0.6,
    "clip_on_incident": True,
    "runner_vars": {"nvapi": True, "fsync": True, "no_esync": True, "dxvk_async": True},
    "gamescope_enabled": True,
    "gamescope": {"w": 1920, "h": 1080, "refresh": 144, "upscale": "fsr",
                  "hdr": True, "borderless": False, "steam_overlay": True},
    "gpu_tuning": {"radv_gpl": True, "radv_nggc": True, "threaded_gl": True},
    "steam_app_id": "12345", "notes": "raid night",
}

CASES = {
    # -- the ordinary paths --------------------------------------------------
    "empty_object": {},
    "no_profiles": cfg(),
    "minimal_profile": cfg({"exe": "Wow.exe"}),
    "fully_populated": cfg(FULL_PROFILE),
    "several_profiles": cfg({"exe": "a.exe"}, {"exe": "b.exe"}, {"exe": "c"}),
    "shipped_defaults": json.loads(json.dumps(asdict(C.default_settings()))),
    # -- shapes that are not objects at all ---------------------------------
    "list_at_top": [],
    "string_at_top": "nope",
    "null_at_top": None,
    "number_at_top": 5,
    "profiles_as_string": {"profiles": "abc"},
    "profiles_as_object": {"profiles": {"a": 1}},
    "profiles_as_null": {"profiles": None},
    "profiles_with_junk": cfg({"exe": "good.exe"}, "junk", 5, None, [], {"exe": "b.exe"}),
    # -- unknown keys, in both places ---------------------------------------
    "unknown_settings_key": cfg(**{"a_key_from_2027": {"nested": [1, 2]}}),
    "unknown_profile_key": cfg({"exe": "a", "future_toggle": True}),
    "unknown_everywhere": cfg({"exe": "a", "x": 1, "y": [2]}, z=3),
    # -- schema version ------------------------------------------------------
    "schema_from_the_future": cfg({"exe": "a"}, schema_version=99),
    "schema_missing": {"profiles": [{"exe": "a"}]},
    "schema_as_string": cfg({"exe": "a"}, schema_version="99"),
    # -- an exe that is not one ---------------------------------------------
    "exe_missing": cfg({"display_name": "no exe"}),
    "exe_empty": cfg({"exe": ""}),
    "exe_whitespace": cfg({"exe": "   "}),
    "exe_dot": cfg({"exe": "."}),
    "exe_dotdot": cfg({"exe": ".."}),
    "exe_with_dotdot": cfg({"exe": "a..b"}),
    "exe_with_slash": cfg({"exe": "dir/game.exe"}),
    "exe_with_backslash": cfg({"exe": "dir\\game.exe"}),
    "exe_with_control_char": cfg({"exe": "game\tx"}),
    "exe_with_newline": cfg({"exe": "game\nx"}),
    "exe_with_del": cfg({"exe": "game\x7fx"}),
    "exe_quoted": cfg({"exe": "'Wow.exe'"}),
    "exe_double_quoted": cfg({"exe": '"Wow.exe"'}),
    "exe_quoted_and_spaced": cfg({"exe": "  \"Wow.exe\"  "}),
    "exe_128_chars": cfg({"exe": "a" * 128}),
    "exe_129_chars": cfg({"exe": "a" * 129}),
    "exe_as_int": cfg({"exe": 5}, ),
    "exe_as_null": cfg({"exe": None}),
    "exe_as_list": cfg({"exe": ["a"]}),
    "exe_unicode": cfg({"exe": "ゲーム.exe"}),
    "exe_regex": cfg({"exe": "Wow.*exe$", "match_mode": "regex"}),
    # -- clamping, on every boundary ----------------------------------------
    "nice_low": cfg({"exe": "a", "nice_value": -11}),
    "nice_edge_low": cfg({"exe": "a", "nice_value": -10}),
    "nice_edge_high": cfg({"exe": "a", "nice_value": 19}),
    "nice_high": cfg({"exe": "a", "nice_value": 20}),
    "nice_float": cfg({"exe": "a", "nice_value": -5.9}),
    "nice_negative_float": cfg({"exe": "a", "nice_value": -0.5}),
    "nice_bool": cfg({"exe": "a", "nice_value": True}),
    "nice_string": cfg({"exe": "a", "nice_value": "-5"}),
    "nice_null": cfg({"exe": "a", "nice_value": None}),
    "pl_edges": cfg({"exe": "a", "pl1_w": 0, "pl2_w": 500, "battery_pl1_w": 501,
                     "battery_pl2_w": -1}),
    # Each of the four power fields at its own upper edge and one past it.
    # They are clamped by four separate calls, so testing one proves nothing
    # about the others.
    "pl_all_at_500": cfg({"exe": "a", "pl1_w": 500, "pl2_w": 500,
                          "battery_pl1_w": 500, "battery_pl2_w": 500}),
    "pl_all_over": cfg({"exe": "a", "pl1_w": 501, "pl2_w": 501,
                        "battery_pl1_w": 501, "battery_pl2_w": 501}),
    "refresh_edges": cfg({"exe": "a", "refresh_rate_hz": 1000},),
    "refresh_over": cfg({"exe": "a", "refresh_rate_hz": 1001}),
    "dip_floor_edges": cfg({"exe": "a", "fps_dip_floor": 5}),
    "dip_floor_under": cfg({"exe": "a", "fps_dip_floor": 4}),
    "dip_floor_over": cfg({"exe": "a", "fps_dip_floor": 121}),
    "dip_ratio_edges": cfg({"exe": "a", "fps_dip_ratio": 0.1}),
    "dip_ratio_under": cfg({"exe": "a", "fps_dip_ratio": 0.09}),
    "dip_ratio_over": cfg({"exe": "a", "fps_dip_ratio": 0.91}),
    "dip_ratio_int": cfg({"exe": "a", "fps_dip_ratio": 1}),
    "dip_ratio_bool": cfg({"exe": "a", "fps_dip_ratio": True}),
    "dip_ratio_string": cfg({"exe": "a", "fps_dip_ratio": "0.5"}),
    # NaN and infinity reach this field through a quoted value, and Python's
    # min/max, Rust's min/max and f64::clamp all disagree about them.
    "dip_ratio_nan": cfg({"exe": "a", "fps_dip_ratio": "nan"}),
    "dip_ratio_inf": cfg({"exe": "a", "fps_dip_ratio": "inf"}),
    "dip_ratio_neg_inf": cfg({"exe": "a", "fps_dip_ratio": "-inf"}),
    "poll_interval_low": cfg(poll_interval=1),
    "poll_interval_high": cfg(poll_interval=99),
    "poll_interval_edges": cfg(poll_interval=3),
    "poll_interval_float": cfg(poll_interval=7.9),
    "poll_interval_string": cfg(poll_interval="9"),
    "poll_interval_list": cfg(poll_interval=[]),
    # -- enums that are not in the list -------------------------------------
    "unknown_match_mode": cfg({"exe": "a", "match_mode": "fuzzy"}),
    "match_mode_as_int": cfg({"exe": "a", "match_mode": 5}),
    "match_mode_as_null": cfg({"exe": "a", "match_mode": None}),
    "unknown_core_pin": cfg({"exe": "a", "core_pin": "all"}),
    "unknown_scx_mode": cfg({"exe": "a", "scx_mode": "turbo"}),
    "unknown_upscaler": cfg({"exe": "a", "gamescope": {"upscale": "dlss"}}),
    "upscaler_as_int": cfg({"exe": "a", "gamescope": {"upscale": 3}}),
    # -- scheduler names ----------------------------------------------------
    "scx_short": cfg({"exe": "a", "scx_scheduler": "lavd"}),
    "scx_prefixed": cfg({"exe": "a", "scx_scheduler": "scx_lavd"}),
    "scx_spaced": cfg({"exe": "a", "scx_scheduler": "  lavd  "}),
    "scx_uppercase": cfg({"exe": "a", "scx_scheduler": "LAVD"}),
    "scx_path": cfg({"exe": "a", "scx_scheduler": "../lavd"}),
    "scx_too_long": cfg({"exe": "a", "scx_scheduler": "a" * 40}),
    "scx_empty": cfg({"exe": "a", "scx_scheduler": ""}),
    "scx_as_int": cfg({"exe": "a", "scx_scheduler": 5}),
    "scx_as_zero": cfg({"exe": "a", "scx_scheduler": 0}),
    "scx_as_null": cfg({"exe": "a", "scx_scheduler": None}),
    # -- the dict-valued fields ---------------------------------------------
    "mangohud_partial": cfg({"exe": "a", "mangohud": {"fps": False}}),
    "mangohud_empty": cfg({"exe": "a", "mangohud": {}}),
    "mangohud_extra_key": cfg({"exe": "a", "mangohud": {"fps": True, "nonsense": True}}),
    "mangohud_as_list": cfg({"exe": "a", "mangohud": []}),
    "mangohud_as_string": cfg({"exe": "a", "mangohud": "x"}),
    "mangohud_as_null": cfg({"exe": "a", "mangohud": None}),
    "runner_vars_partial": cfg({"exe": "a", "runner_vars": {"nvapi": False}}),
    "runner_vars_as_list": cfg({"exe": "a", "runner_vars": []}),
    "gamescope_partial": cfg({"exe": "a", "gamescope": {"w": 1280}}),
    "gamescope_as_string": cfg({"exe": "a", "gamescope": "x"}),
    "gamescope_size_over": cfg({"exe": "a", "gamescope": {"w": 99999, "h": -5, "refresh": 0}}),
    "gamescope_size_null": cfg({"exe": "a", "gamescope": {"w": None, "h": "", "refresh": False}}),
    "gamescope_size_float": cfg({"exe": "a", "gamescope": {"w": 1920.9}}),
    "gamescope_size_string": cfg({"exe": "a", "gamescope": {"w": "1920"}}),
    # -- gpu_tuning filtering -----------------------------------------------
    "gpu_tuning_normal": cfg({"exe": "a", "gpu_tuning": {"radv_gpl": True}}),
    "gpu_tuning_long_key": cfg({"exe": "a", "gpu_tuning": {"k" * 39: True, "k" * 40: True}}),
    "gpu_tuning_truthy": cfg({"exe": "a", "gpu_tuning": {"a": 1, "b": 0, "c": "x", "d": "",
                                                         "e": [], "f": [1], "g": None}}),
    "gpu_tuning_as_list": cfg({"exe": "a", "gpu_tuning": []}),
    "gpu_tuning_as_null": cfg({"exe": "a", "gpu_tuning": None}),
    # -- text fields --------------------------------------------------------
    "display_name_empty": cfg({"exe": "a", "display_name": ""}),
    "display_name_long": cfg({"exe": "a", "display_name": "x" * 300}),
    "display_name_unicode": cfg({"exe": "a", "display_name": "я" * 300}),
    "display_name_as_int": cfg({"exe": "a", "display_name": 5}),
    "notes_long": cfg({"exe": "a", "notes": "n" * 900}),
    "notes_unicode": cfg({"exe": "a", "notes": "я" * 900}),
    "notes_as_int": cfg({"exe": "a", "notes": 5}),
    "notes_as_null": cfg({"exe": "a", "notes": None}),
    "app_id_digits": cfg({"exe": "a", "steam_app_id": "12345"}),
    "app_id_mixed": cfg({"exe": "a", "steam_app_id": "app #12345 (wow)"}),
    "app_id_as_int": cfg({"exe": "a", "steam_app_id": 12345}),
    "app_id_long": cfg({"exe": "a", "steam_app_id": "1" * 20}),
    "app_id_as_null": cfg({"exe": "a", "steam_app_id": None}),
    "app_id_no_digits": cfg({"exe": "a", "steam_app_id": "none"}),
    # -- vrr_outputs, including the string case -----------------------------
    "vrr_list": cfg({"exe": "a", "vrr_outputs": ["eDP-1"]}),
    "vrr_many": cfg({"exe": "a", "vrr_outputs": [f"DP-{i}" for i in range(40)]}),
    "vrr_long_name": cfg({"exe": "a", "vrr_outputs": ["x" * 200]}),
    "vrr_as_string": cfg({"exe": "a", "vrr_outputs": "abc"}),
    "vrr_as_null": cfg({"exe": "a", "vrr_outputs": None}),
    "vrr_empty": cfg({"exe": "a", "vrr_outputs": []}),
    "vrr_mixed_types": cfg({"exe": "a", "vrr_outputs": [1, None, True, "x"]}),
    "vrr_as_object": cfg({"exe": "a", "vrr_outputs": {"eDP-1": 1}}),
    # -- loosely typed booleans, which Python keeps verbatim ----------------
    "enabled_as_string": cfg({"exe": "a", "enabled": "yes"}),
    "enabled_as_zero": cfg({"exe": "a", "enabled": 0}),
    "enabled_as_null": cfg({"exe": "a", "enabled": None}),
    "master_as_string": cfg({"exe": "a"}, master_enabled="yes"),
    "master_as_zero": cfg({"exe": "a"}, master_enabled=0),
    "settings_wrong_types": cfg(ignored_games=[1, 2], llm_model_hint=5,
                                diagnostics_sample_interval="fast",
                                prometheus_textfile=None, auto_detect="maybe"),
    # -- a whole file of trouble --------------------------------------------
    "everything_broken": cfg(
        {"exe": 5}, {"exe": "ok.exe", "mangohud": []}, {"exe": "fine.exe"},
        poll_interval=[], master_enabled="yes", schema_version="x"),
}


class BothImplementationsAgree(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        # The corpus deliberately feeds invalid scheduler names, and the
        # loader is right to warn about them - just not onto the test output.
        quiet = logging.getLogger("goblinmode.config")
        previous = quiet.level
        quiet.setLevel(logging.CRITICAL)
        self.addCleanup(quiet.setLevel, previous)

        self.binary = _binary()
        if self.binary is None:
            if os.environ.get("GMP_REQUIRE_RUST_HELPER") == "1":
                self.fail("GMP_REQUIRE_RUST_HELPER=1 but the config example is "
                          "not built - run `cargo build -p gmp-core --example config`")
            self.skipTest("build it with `cargo build -p gmp-core --example config`")

    def _rust(self, raw) -> dict:
        r = subprocess.run([str(self.binary)], input=json.dumps(raw),
                           capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    @staticmethod
    def _python(raw) -> dict:
        settings = C._from_dict(copy.deepcopy(raw))
        # asdict then round-trip through JSON, which is exactly what save()
        # writes and load() reads back.
        saved = json.loads(json.dumps(asdict(settings)))
        return {
            "saved": saved,
            "env": [[[k, v] for k, v in p.env_assignments().items()]
                    for p in settings.profiles],
            "enabled": [p.exe for p in settings.enabled_profiles()],
        }

    def test_a_config_survives_a_round_trip_identically(self):
        for label, raw in CASES.items():
            with self.subTest(label):
                self.assertEqual(self._rust(raw), self._python(raw))

    def test_loading_twice_changes_nothing(self):
        # Normalisation has to be idempotent, or the file churns on every save
        # and a value drifts a little further each time.
        for label, raw in CASES.items():
            with self.subTest(label):
                once = self._python(raw)["saved"]
                self.assertEqual(self._rust(once)["saved"], once)
                self.assertEqual(self._python(once)["saved"], once)

    def test_every_schema_field_is_present_after_a_round_trip(self):
        # The check that catches a field the port forgot: both sides must
        # write the complete schema, not merely agree on what they wrote.
        want_settings = {f.name for f in __import__("dataclasses").fields(C.Settings)}
        want_profile = {f.name for f in __import__("dataclasses").fields(C.GameProfile)}
        got = self._rust(cfg(FULL_PROFILE))["saved"]
        self.assertEqual(set(got), want_settings)
        self.assertEqual(set(got["profiles"][0]), want_profile)


if __name__ == "__main__":
    unittest.main()
