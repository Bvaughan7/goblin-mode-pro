"""The Rust and Python CLI print the same report for the same daemon reply.

Every command in ``cli.py`` is two steps: ask the daemon, then turn the reply
into lines. The asking stays in Python. The lines are what a person reads, and
they are what this pins.

The corpus is weighted towards replies that are not quite what the CLI expects,
for a reason specific to this pair. All four of these replies cross the frozen
session-bus interface as a JSON **string** - ``GetStatus``, ``GetHealth``,
``GetSessions`` and ``RunPreflight`` each declare ``s`` - so the D-Bus
signature constrains nothing about what is inside them, and the freeze exists
precisely because the daemon on the other end may be a different build from the
CLI asking. An unexpected field type is a thing that happens here, not a thing
that cannot.

Before this port, eighteen of the shapes below raised out of the command:
``KeyError`` on a preflight check with no ``status``, ``AttributeError`` on a
reply field holding a list where a mapping belonged, ``TypeError`` on a
``started`` timestamp that was not a string, and ``ValueError`` from a format
code applied to one that was.

One of them needs no version skew at all. ``sessions`` guarded on ``fps_avg``
and then formatted both ``fps_avg`` and ``fps_1low``, so a record with an
average and no 1% low crashed the listing. No run this program writes produces
that pair - both are set together or neither is - but ``sessions.jsonl`` is
appended to across versions and is plain text on disk.
"""

from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path

from tests._support import _SRC, typed  # noqa: F401

from goblinmode import cli

_REPO = Path(__file__).resolve().parent.parent


def _binary() -> Path | None:
    override = os.environ.get("GMP_CLI_REPORT_BIN")
    if override:
        return Path(override) if Path(override).exists() else None
    for profile in ("debug", "release"):
        candidate = _REPO / "target" / profile / "examples" / "cli_report"
        if candidate.exists():
            return candidate
    return None


# A reply as a healthy daemon of this version really answers.
REAL_STATUS = {
    "master_enabled": True,
    "active_games": ["Wow.exe"],
    "governor": "performance",
    "tweaks": {"governor": True, "epp_boosted": True, "tearing": False,
               "adaptive_sync": False, "power_limited": True, "focus_mode": True,
               "scx_scheduler": "rusty"},
    "helper_available": True,
    "capabilities": {"cpu_model": "Intel Core i7-10750H", "gpu_vendors": ["intel", "nvidia"],
                     "kernel_release": "6.9.3-cachyos"},
    "profiles": [
        {"exe": "__forced__", "display_name": "forced boost", "enabled": True,
         "match_mode": "exact"},
        {"exe": "Wow.exe", "display_name": "World of Warcraft", "enabled": True,
         "match_mode": "exact"},
        {"exe": "rs2client", "display_name": "RuneScape", "enabled": False,
         "match_mode": "substring"},
    ],
}

REAL_HEALTH = {"score": 8, "counts": {"ok": 11, "warn": 2, "fail": 1},
               "worst": ["helper not reachable", "governor is powersave"]}

REAL_SESSIONS = [
    {"started": "2026-09-02T21:14:03", "game": "World of Warcraft", "fps_avg": 84.3,
     "fps_1low": 51.2, "benchmark": False},
    {"started": "2026-09-03T09:02:55", "game": "World of Warcraft", "fps_avg": 91.0,
     "fps_1low": 60.5, "benchmark": True},
]

REAL_PREFLIGHT = [
    {"status": "ok", "title": "CPU governor", "value": "performance"},
    {"status": "warn", "title": "cooling", "value": "95°C under load"},
    {"status": "fail", "title": "helper", "value": "not installed"},
    {"status": "info", "title": "compositor", "value": "KWin (Wayland)"},
]


def case(**over) -> dict:
    base = {"status": {}, "health": {}, "sessions": [], "preflight": [],
            "fixes": {}, "limit": 10}
    base.update(over)
    return base


CASES = {
    # -- what a healthy daemon of this build answers ----------------------
    "everything_real": case(status=REAL_STATUS, health=REAL_HEALTH,
                            sessions=REAL_SESSIONS, preflight=REAL_PREFLIGHT,
                            fixes={"applied": ["governor"], "failed": ["fans"]}),
    "everything_empty": case(),
    # -- status ------------------------------------------------------------
    "status_master_off": case(status={"master_enabled": False}),
    "status_no_games": case(status={"active_games": []}),
    "status_two_games": case(status={"active_games": ["a.exe", "b.exe"]}),
    "status_games_is_a_string": case(status={"active_games": "Wow"}),
    "status_games_are_numbers": case(status={"active_games": [1, 2]}),
    "status_governor_missing": case(status={}),
    "status_governor_null": case(status={"governor": None}),
    "status_governor_empty": case(status={"governor": ""}),
    "status_tweaks_is_a_list": case(status={"tweaks": [1]}),
    "status_tweaks_all_off": case(status={"tweaks": {k: False for k in cli.TWEAK_KEYS}}),
    "status_tweaks_all_on": case(status={"tweaks": {k: True for k in cli.TWEAK_KEYS}}),
    "status_tweaks_unknown_key": case(status={"tweaks": {"warp_drive": True}}),
    "status_tweaks_out_of_order": case(status={"tweaks": {"focus_mode": True,
                                                          "governor": True}}),
    "status_scx_only": case(status={"tweaks": {"scx_scheduler": "lavd"}}),
    "status_scx_is_a_list": case(status={"tweaks": {"scx_scheduler": ["a"]}}),
    "status_scx_empty_string": case(status={"tweaks": {"scx_scheduler": ""}}),
    "status_caps_is_a_list": case(status={"capabilities": [1]}),
    "status_cpu_model_null": case(status={"capabilities": {"cpu_model": None}}),
    "status_gpu_vendors_is_a_number": case(status={"capabilities": {"gpu_vendors": 5}}),
    "status_gpu_vendors_are_numbers": case(status={"capabilities": {"gpu_vendors": [1]}}),
    "status_gpu_vendors_empty": case(status={"capabilities": {"gpu_vendors": []}}),
    "status_is_a_list": case(status=[1, 2]),
    "status_helper_absent": case(status={"helper_available": False}),
    # -- health -------------------------------------------------------------
    "health_score_zero": case(health={"score": 0}),
    "health_score_null": case(health={"score": None}),
    "health_score_float": case(health={"score": 7.5}),
    "health_counts_is_a_list": case(health={"counts": [1]}),
    "health_counts_partial": case(health={"counts": {"ok": 3}}),
    "health_counts_null_value": case(health={"counts": {"ok": None}}),
    "health_worst_is_a_string": case(health={"worst": "everything"}),
    "health_worst_is_a_number": case(health={"worst": 5}),
    "health_worst_empty": case(health={"worst": []}),
    "health_is_a_list": case(health=[1]),
    # -- sessions ------------------------------------------------------------
    "sessions_none": case(sessions=[]),
    "sessions_no_fps": case(sessions=[{"started": "2026-09-03T09:02", "game": "g"}]),
    # The record that needed no skew: an average with no 1% low.
    "sessions_avg_without_low": case(sessions=[{"started": "x", "game": "g",
                                                "fps_avg": 60.4}]),
    "sessions_low_without_avg": case(sessions=[{"started": "x", "game": "g",
                                                "fps_1low": 40.0}]),
    "sessions_zero_avg": case(sessions=[{"started": "x", "game": "g", "fps_avg": 0,
                                         "fps_1low": 0}]),
    "sessions_fps_is_a_string": case(sessions=[{"started": "x", "game": "g",
                                                "fps_avg": "hi", "fps_1low": "lo"}]),
    "sessions_fps_is_a_bool": case(sessions=[{"started": "x", "game": "g",
                                              "fps_avg": True, "fps_1low": True}]),
    "sessions_started_is_a_number": case(sessions=[{"started": 5, "game": "g"}]),
    "sessions_started_is_long": case(sessions=[{"started": "2026-09-03T09:02:55.123456",
                                                "game": "g"}]),
    "sessions_started_missing": case(sessions=[{"game": "g"}]),
    "sessions_game_is_a_number": case(sessions=[{"started": "x", "game": 5}]),
    "sessions_game_missing": case(sessions=[{"started": "x"}]),
    "sessions_game_is_long": case(sessions=[{"started": "x", "game": "a" * 40}]),
    "sessions_row_is_a_list": case(sessions=[[1]]),
    "sessions_row_is_a_string": case(sessions=["nope"]),
    "sessions_benchmark_tag": case(sessions=[{"started": "x", "game": "g",
                                              "benchmark": True}]),
    "sessions_limit_one": case(sessions=REAL_SESSIONS, limit=1),
    "sessions_limit_larger_than_history": case(sessions=REAL_SESSIONS, limit=99),
    # `--limit` is a bare `type=int`, so both of these come off the command
    # line. Zero negates to zero and `rows[0:]` is the WHOLE history; a
    # negative limit negates to a positive start and drops rows off the FRONT.
    "sessions_limit_zero_shows_everything": case(sessions=REAL_SESSIONS, limit=0),
    "sessions_limit_negative": case(sessions=REAL_SESSIONS, limit=-1),
    "sessions_limit_negative_past_the_end": case(sessions=REAL_SESSIONS, limit=-99),
    # -- preflight ------------------------------------------------------------
    "preflight_missing_status": case(preflight=[{"title": "t", "value": "v"}]),
    "preflight_missing_title": case(preflight=[{"status": "ok", "value": "v"}]),
    "preflight_missing_value": case(preflight=[{"status": "ok", "title": "t"}]),
    "preflight_unknown_status": case(preflight=[{"status": "elsewhere", "title": "t",
                                                 "value": "v"}]),
    "preflight_check_is_a_string": case(preflight=["nope"]),
    "preflight_title_is_a_number": case(preflight=[{"status": "ok", "title": 5,
                                                    "value": "v"}]),
    "preflight_title_is_long": case(preflight=[{"status": "ok", "title": "t" * 40,
                                                "value": "v"}]),
    "preflight_fixes_nothing": case(fixes={}),
    "preflight_fixes_empty_lists": case(fixes={"applied": [], "failed": []}),
    "preflight_fixes_applied_only": case(fixes={"applied": ["governor", "epp"]}),
    "preflight_fixes_failed_only": case(fixes={"failed": ["fans"]}),
    "preflight_fixes_is_a_list": case(fixes=[1]),
    "preflight_fixes_applied_is_a_string": case(fixes={"applied": "governor"}),
    # -- games -----------------------------------------------------------------
    "games_only_forced": case(status={"profiles": [{"exe": "__forced__"}]}),
    "games_profiles_is_a_string": case(status={"profiles": "Wow"}),
    "games_profile_is_a_string": case(status={"profiles": ["Wow"]}),
    "games_profile_empty": case(status={"profiles": [{}]}),
    "games_display_name_long": case(status={"profiles": [{"exe": "a", "enabled": True,
                                                          "display_name": "n" * 40,
                                                          "match_mode": "exact"}]}),
    "games_unicode": case(status={"profiles": [{"exe": "ゲーム.exe", "enabled": True,
                                                "display_name": "ゲーム",
                                                "match_mode": "exact"}]}),
}


class BothImplementationsAgree(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.binary = _binary()
        if self.binary is None:
            if os.environ.get("GMP_REQUIRE_RUST_HELPER") == "1":
                self.fail("GMP_REQUIRE_RUST_HELPER=1 but the cli_report example is "
                          "not built - run `cargo build -p gmp-cli --example "
                          "cli_report`")
            self.skipTest("build it with `cargo build -p gmp-cli --example cli_report`")

    def _rust(self, payload: dict) -> dict:
        r = subprocess.run([str(self.binary)], input=json.dumps(payload),
                           capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    @staticmethod
    def _python(payload: dict) -> dict:
        return {
            "status": cli.status_lines(payload["status"]),
            "health": cli.health_lines(payload["health"]),
            "sessions": cli.sessions_lines(payload["sessions"], payload["limit"]),
            "preflight": cli.preflight_lines(payload["preflight"]),
            "preflight_fixes": cli.preflight_fix_lines(payload["fixes"]),
            "games": cli.games_lines(payload["status"]),
        }

    def test_every_reply_renders_the_same_way(self):
        for label, payload in CASES.items():
            with self.subTest(label):
                self.assertEqual(typed(self._rust(payload)),
                                 typed(self._python(payload)))

    def test_the_corpus_reaches_every_line_of_every_report(self):
        """A corpus that never produces a line cannot pin its wording."""
        produced = {key: set() for key in
                    ("status", "health", "sessions", "preflight", "preflight_fixes",
                     "games")}
        for payload in CASES.values():
            for key, lines in self._python(payload).items():
                produced[key].update(lines)

        every = "\n".join(line for lines in produced.values() for line in lines)
        for fragment in ("master      : on", "master      : off", "active game  : —",
                         "active tweaks : none", "helper       : connected",
                         "helper       : limited mode", "no sessions recorded yet",
                         "(no fps log)", "[benchmark]", "applied: nothing",
                         "failed : ", "✓ ", "! ", "✗ ", "i ", "? ", "● ", "○ ",
                         "system readiness: "):
            self.assertIn(fragment, every, f"nothing in the corpus produces {fragment!r}")

        # And every tweak key can appear, or the fixed order is not really pinned.
        for key in cli.TWEAK_KEYS:
            self.assertIn(key, every)


class NoReplyShapeCanCrashTheCli(unittest.TestCase):
    """What the two agree on is "carry on", not "raise in step".

    The parity corpus proves the implementations match. This proves the thing
    they match on is useful - without it a later change could make both fail
    on the same input and the diff would still be clean.
    """

    def test_no_corpus_reply_raises(self):
        for label, payload in CASES.items():
            with self.subTest(label):
                BothImplementationsAgree._python(payload)

    def test_nor_do_the_shapes_the_corpus_does_not_bother_pinning(self):
        for reply in ({"tweaks": {"governor": {"nested": 1}}},
                      {"capabilities": {"gpu_vendors": {"a": 1}}},
                      {"profiles": [{"exe": ["a"], "match_mode": {"b": 2}}]},
                      {"active_games": [[["deep"]]]}):
            with self.subTest(str(reply)[:50]):
                cli.status_lines(reply)
                cli.games_lines(reply)
        for reply in ({"counts": {"ok": [1]}}, {"worst": [{"a": 1}]},
                      {"score": [1]}):
            with self.subTest(str(reply)[:50]):
                cli.health_lines(reply)
        cli.sessions_lines([{"started": {"a": 1}, "game": ["b"]}], 10)
        cli.preflight_lines([{"status": ["ok"], "title": {"a": 1}, "value": [1]}])
        cli.preflight_fix_lines({"applied": {"a": 1}, "failed": 5})


if __name__ == "__main__":
    unittest.main()
