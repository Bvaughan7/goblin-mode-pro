"""The Rust and Python observers pick the same process.

The observer decides two things every poll: whether a running process belongs
to a profile, and which of several matching processes is the one to renice.
Getting the first wrong means a game runs untuned; getting the second wrong
means the tuning lands on a launcher or a Wine service while the game itself
runs at default priority - which looks exactly like the tool doing nothing,
and is the harder of the two to notice.

So the corpus is built around the ways the answer goes wrong rather than the
ways it goes right: comm truncated at the kernel's fifteen characters, Wine
wrapper processes fatter than the game they wrapped, ties in resident size,
and the two length bounds - the 128-character cap on a pattern and the 4096
on the string it is searched against.

This module asks a similar question to ``runner`` and answers it differently
on purpose, and the differences are asserted here rather than assumed:
``exact`` matches a truncated comm as a prefix, which the runner has no reason
to do, and ``substring``/``regex`` search one joined haystack rather than each
argument separately.
"""

from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace

from tests._support import _SRC  # noqa: F401

from goblinmode import observer
from goblinmode.config import _from_dict

_REPO = Path(__file__).resolve().parent.parent


def _binary() -> Path | None:
    override = os.environ.get("GMP_OBSERVER_BIN")
    if override:
        return Path(override) if Path(override).exists() else None
    for profile in ("debug", "release"):
        candidate = _REPO / "target" / profile / "examples" / "observer"
        if candidate.exists():
            return candidate
    return None


class FakeProcess:
    """Just enough of ``psutil.Process`` for ``_find_pid``.

    The real scan is plumbing and stays in Python; what is under test is the
    judgement applied to what it returns, so the input is spelled out here
    rather than read off this machine.
    """

    def __init__(self, pid, name="", exe="", cmdline=(), rss=0):
        self.info = {"pid": pid, "name": name, "exe": exe, "cmdline": list(cmdline)}
        self._rss = rss

    def memory_info(self):
        return SimpleNamespace(rss=self._rss)


def pr(pid, name="", exe="", cmdline=(), rss=0) -> dict:
    return {"pid": pid, "name": name, "exe": exe, "cmdline": list(cmdline), "rss": rss}


def p(exe, **over) -> dict:
    return {"exe": exe, **over}


def cfg(*profiles, **settings) -> dict:
    return {"profiles": list(profiles), **settings}


# A name at, one under and one over the kernel's comm cap. Fifteen is the
# floor for the prefix rule, so these three straddle it.
LONG = "VeryLongGameName.exe"
COMM_15 = LONG[:15]
COMM_14 = LONG[:14]

# Python lowercases the haystack BEFORE truncating it to 4096 characters, and
# U+0130 is a character whose lowercase is two characters long - so folding
# first keeps half as much of the string. This name is short enough to survive
# a truncate-then-fold and too long to survive a fold-then-truncate, which is
# the only way to tell the two orders apart.
GROWS_WHEN_FOLDED = "\u0130" * 2050 + "x"

CASES = {
    "empty_table": (cfg(p("Wow.exe")), []),
    "no_profiles": (cfg(), [pr(1, name="Wow.exe", rss=1000)]),
    # -- exact, on the comm and on the basename of everything else --------
    "comm_exact": (cfg(p("Wow.exe")), [pr(1, name="Wow.exe", rss=1000)]),
    "comm_case_differs": (cfg(p("Wow.exe")), [pr(1, name="WOW.EXE", rss=1000)]),
    "profile_case_differs": (cfg(p("WOW.EXE")), [pr(1, name="wow.exe", rss=1000)]),
    "exe_unix_path": (cfg(p("Wow.exe")),
                      [pr(1, name="wine", exe="/games/wow/Wow.exe", rss=1000)]),
    "exe_windows_path": (cfg(p("Wow.exe")),
                         [pr(1, name="wine", exe="C:\\Games\\Wow.exe", rss=1000)]),
    "exe_mixed_separators": (cfg(p("Wow.exe")),
                             [pr(1, name="wine", exe="Z:/g\\wow/Wow.exe", rss=1000)]),
    "cmdline_token": (cfg(p("Wow.exe")),
                      [pr(1, name="wine", cmdline=["wine", "C:\\Wow.exe"], rss=1000)]),
    "cmdline_quoted_token": (cfg(p("Wow.exe")),
                             [pr(1, name="wine", cmdline=['"C:\\Wow.exe"'], rss=1000)]),
    "cmdline_padded_token": (cfg(p("Wow.exe")),
                             [pr(1, name="wine", cmdline=["  Wow.exe  "], rss=1000)]),
    "substring_of_a_token_is_not_exact": (cfg(p("Wow.exe")),
                                          [pr(1, name="NotWow.exe", rss=1000)]),
    "unicode_exe": (cfg(p("\u30b2\u30fc\u30e0.exe")),
                    [pr(1, name="wine", exe="C:\\g\\\u30b2\u30fc\u30e0.exe", rss=1000)]),
    # A comm with a separator in it is a name, not a path. If it were split,
    # this profile would claim a process it has nothing to do with.
    "comm_with_a_slash_is_not_split": (cfg(p("name")),
                                       [pr(1, name="weird/name", rss=1000)]),
    # -- the comm truncation rule, which is exact matching's odd corner ----
    "comm_truncated_at_fifteen": (cfg(p(LONG)), [pr(1, name=COMM_15, rss=1000)]),
    "comm_one_under_fifteen": (cfg(p(LONG)), [pr(1, name=COMM_14, rss=1000)]),
    "comm_prefix_far_too_short": (cfg(p("Wow.exe")), [pr(1, name="Wow", rss=1000)]),
    "comm_prefix_that_does_not_prefix": (cfg(p(LONG)),
                                         [pr(1, name="VeryLongGameXX", rss=1000)]),
    # Fifteen characters is a character count, not a byte count - three-byte
    # characters are past fifteen bytes long before they are past fourteen.
    "comm_truncation_counts_characters": (
        cfg(p("\u30b2" * 20)), [pr(1, name="\u30b2" * 14, rss=1000)]),
    "comm_truncation_counts_characters_at_the_floor": (
        cfg(p("\u30b2" * 20)), [pr(1, name="\u30b2" * 15, rss=1000)]),
    # -- substring, on the joined haystack ---------------------------------
    "substring_in_cmdline": (cfg(p("rs2client", match_mode="substring")),
                             [pr(1, name="sh", exe="/bin/sh",
                                 cmdline=["-c", "/opt/rs2client"], rss=1000)]),
    "substring_case_folded": (cfg(p("RS2Client", match_mode="substring")),
                              [pr(1, name="sh", cmdline=["/opt/rs2client"], rss=1000)]),
    "substring_no_match": (cfg(p("rs2client", match_mode="substring")),
                           [pr(1, name="sh", cmdline=["/opt/other"], rss=1000)]),
    # The command line is joined with spaces before it is searched, so a
    # profile can name an executable and the flag that follows it. Without the
    # separator "Wow.exe --dx12" would read as "Wow.exe--dx12" and never match.
    "substring_spans_two_cmdline_tokens": (
        cfg(p("wow.exe --dx12", match_mode="substring")),
        [pr(1, name="wine", cmdline=["Wow.exe", "--dx12"], rss=1000)]),
    "regex_spans_two_cmdline_tokens": (
        cfg(p("Wow.exe --dx12", match_mode="regex")),
        [pr(1, name="wine", cmdline=["Wow.exe", "--dx12"], rss=1000)]),
    # The haystack is "name exe cmdline" with both separators unconditional,
    # so a missing exe leaves two spaces rather than closing the gap.
    "substring_spans_the_name_exe_join": (cfg(p("wow.exe wine", match_mode="substring")),
                                          [pr(1, name="Wow.exe", exe="wine", rss=1000)]),
    "substring_over_an_empty_exe": (cfg(p("wow.exe  -dx12", match_mode="substring")),
                                    [pr(1, name="Wow.exe", cmdline=["-dx12"], rss=1000)]),
    "substring_single_space_over_an_empty_exe": (
        cfg(p("wow.exe -dx12", match_mode="substring")),
        [pr(1, name="Wow.exe", cmdline=["-dx12"], rss=1000)]),
    "substring_at_the_haystack_bound": (
        cfg(p("y", match_mode="substring")),
        [pr(1, name="x" * 4093, cmdline=["y"], rss=1000)]),
    "substring_past_the_haystack_bound": (
        cfg(p("y", match_mode="substring")),
        [pr(1, name="x" * 4094, cmdline=["y"], rss=1000)]),
    "substring_is_folded_before_it_is_truncated": (
        cfg(p("x", match_mode="substring")), [pr(1, name=GROWS_WHEN_FOLDED, rss=1000)]),
    # -- regex, which is the one mode that is not case folded --------------
    "regex_char_class": (cfg(p("[Ww]ow[0-9]*", match_mode="regex")),
                         [pr(1, name="wow64", rss=1000)]),
    "regex_is_case_sensitive": (cfg(p("Wow", match_mode="regex")),
                                [pr(1, name="wow.exe", rss=1000)]),
    "regex_anchored_to_the_haystack_start": (cfg(p("^Wow", match_mode="regex")),
                                             [pr(1, name="Wow.exe", rss=1000)]),
    # The anchor is on the haystack, not on each field - so a process whose
    # name does not start with the pattern never matches it, however the exe
    # and command line read.
    "regex_anchor_does_not_see_the_exe": (cfg(p("^Wow", match_mode="regex")),
                                          [pr(1, name="wine", exe="Wow.exe", rss=1000)]),
    "regex_alternation": (cfg(p("wow|runescape", match_mode="regex")),
                          [pr(1, name="runescape", rss=1000)]),
    "regex_uncompilable_skips_the_profile": (cfg(p("*[bad", match_mode="regex"),
                                                 p("Wow.exe")),
                                             [pr(1, name="Wow.exe", rss=1000)]),
    "regex_at_the_pattern_bound": (cfg(p("x" * 127 + "y", match_mode="regex")),
                                   [pr(1, name="x" * 127, rss=1000)]),
    "regex_one_under_the_pattern_bound": (cfg(p("x" * 126 + "y", match_mode="regex")),
                                          [pr(1, name="x" * 126 + "y", rss=1000)]),
    "regex_at_the_haystack_bound": (cfg(p("y", match_mode="regex")),
                                    [pr(1, name="x" * 4093, cmdline=["y"], rss=1000)]),
    "regex_past_the_haystack_bound": (cfg(p("y", match_mode="regex")),
                                      [pr(1, name="x" * 4094, cmdline=["y"], rss=1000)]),
    # -- which of several matching processes wins --------------------------
    "fattest_wins": (cfg(p("Wow.exe")), [pr(1, name="Wow.exe", rss=1000),
                                         pr(2, name="Wow.exe", rss=900_000),
                                         pr(3, name="Wow.exe", rss=5000)]),
    "a_tie_keeps_scan_order": (cfg(p("Wow.exe")), [pr(7, name="Wow.exe", rss=4096),
                                                   pr(3, name="Wow.exe", rss=4096)]),
    "a_tie_keeps_scan_order_reversed": (cfg(p("Wow.exe")),
                                        [pr(3, name="Wow.exe", rss=4096),
                                         pr(7, name="Wow.exe", rss=4096)]),
    "zero_rss_still_counts": (cfg(p("Wow.exe")), [pr(1, name="Wow.exe", rss=0)]),
    "all_zero_rss": (cfg(p("Wow.exe")), [pr(9, name="Wow.exe", rss=0),
                                         pr(2, name="Wow.exe", rss=0)]),
    # -- the Wine blocklist, which is on the comm alone --------------------
    "a_fat_wine_wrapper_loses_to_a_thin_game": (
        cfg(p("Wow.exe", match_mode="substring")),
        [pr(1, name="explorer.exe", cmdline=["Wow.exe"], rss=900_000),
         pr(2, name="Wow.exe", cmdline=["Wow.exe"], rss=1000)]),
    "only_wrappers_means_no_pid": (
        cfg(p("Wow.exe", match_mode="substring")),
        [pr(1, name="explorer.exe", cmdline=["Wow.exe"], rss=900_000),
         pr(2, name="services.exe", cmdline=["Wow.exe"], rss=800_000)]),
    "the_blocklist_is_case_folded": (
        cfg(p("Wow.exe", match_mode="substring")),
        [pr(1, name="Explorer.EXE", cmdline=["Wow.exe"], rss=900_000)]),
    # The blocklist is checked against the comm and nothing else, because a
    # Wine game's /proc/*/exe points at the wine loader itself - excluding on
    # that would exclude every Windows game there is.
    "a_wine_exe_path_does_not_disqualify": (
        cfg(p("Wow.exe")),
        [pr(1, name="Wow.exe", exe="/usr/bin/wine64-preloader",
            cmdline=["wine", "Wow.exe"], rss=1000)]),
    "python3_is_on_the_blocklist": (
        cfg(p("game", match_mode="substring")),
        [pr(1, name="python3", cmdline=["game.py"], rss=900_000)]),
    # -- enablement, which gates all of the above --------------------------
    "disabled_profile": (cfg(p("Wow.exe", enabled=False)),
                         [pr(1, name="Wow.exe", rss=1000)]),
    "master_off": (cfg(p("Wow.exe"), master_enabled=False),
                   [pr(1, name="Wow.exe", rss=1000)]),
    "two_profiles_one_table": (cfg(p("Wow.exe"), p("runescape", match_mode="substring")),
                               [pr(1, name="Wow.exe", rss=1000),
                                pr(2, name="sh", cmdline=["/opt/runescape"], rss=2000)]),
    "both_profiles_match_the_same_process": (
        cfg(p("Wow.exe"), p("wow", match_mode="substring")),
        [pr(1, name="Wow.exe", rss=1000)]),
    # -- degenerate processes ----------------------------------------------
    "a_process_with_no_fields_at_all": (cfg(p("Wow.exe")), [pr(1)]),
    "empty_cmdline_tokens": (cfg(p("Wow.exe")),
                             [pr(1, name="wine", cmdline=["", "  ", "Wow.exe"], rss=1)]),
    "a_separator_only_token": (cfg(p("Wow.exe")),
                               [pr(1, name="wine", cmdline=["\\\\", "//"], rss=1)]),
}


class BothImplementationsAgree(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.binary = _binary()
        if self.binary is None:
            if os.environ.get("GMP_REQUIRE_RUST_HELPER") == "1":
                self.fail("GMP_REQUIRE_RUST_HELPER=1 but the observer example is not "
                          "built - run `cargo build -p gmp-core --example observer`")
            self.skipTest("build it with `cargo build -p gmp-core --example observer`")

    def _rust(self, settings: dict, procs: list) -> dict:
        payload = {"settings": settings, "procs": procs}
        r = subprocess.run([str(self.binary)], input=json.dumps(payload),
                           capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        got = json.loads(r.stdout)
        # The blocklist is a hand-maintained constant in both implementations
        # rather than a generated table, so nothing but this stops one of them
        # gaining an entry the other has not - and a missing entry means the
        # tuning lands on a Wine service instead of the game, silently.
        got["wine_infra"] = sorted(got["wine_infra"])
        return got

    @staticmethod
    def _python(settings: dict, procs: list) -> dict:
        s = _from_dict(settings)
        obs = observer.Observer(s, lambda _event: None)
        table = [FakeProcess(**proc) for proc in procs]
        return {
            "profiles": [
                {
                    "exe": profile.exe,
                    "display_name": profile.display_name,
                    "matched": [
                        f.info["pid"] for f in table
                        if observer._matches(profile, f.info["name"], f.info["exe"],
                                             f.info["cmdline"])
                    ],
                    "pid": obs._find_pid(profile, table),
                }
                for profile in s.enabled_profiles()
            ],
            "candidate_names": [
                {
                    "pid": f.info["pid"],
                    "names": sorted(observer._candidate_names(
                        f.info["name"], f.info["exe"], f.info["cmdline"])),
                }
                for f in table
            ],
            "wine_infra": sorted(observer._WINE_INFRA),
        }

    def test_every_process_table_resolves_the_same_way(self):
        for label, (settings, procs) in CASES.items():
            with self.subTest(label):
                self.assertEqual(self._rust(settings, procs),
                                 self._python(settings, procs))

    def test_the_corpus_covers_both_answers_for_every_mode(self):
        """A corpus that only ever matches would pass against a stub."""
        seen = {mode: set() for mode in ("exact", "substring", "regex")}
        for settings, procs in CASES.values():
            s = _from_dict(settings)
            for profile in s.enabled_profiles():
                for proc in procs:
                    seen[profile.match_mode].add(observer._matches(
                        profile, proc["name"], proc["exe"], proc["cmdline"]))
        for mode, answers in seen.items():
            self.assertEqual(answers, {True, False}, f"{mode} is one-sided")


if __name__ == "__main__":
    unittest.main()
