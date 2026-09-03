"""The scored signal stack that decides what counts as a game.

This module is where a mistake is most visible to a user. A false NEGATIVE
means the tool does nothing at all; a false POSITIVE means it reniced their
browser and pinned the governor for a text editor. The scoring rules exist to
make the second one hard, and these tests pin the rules that do that work.

Everything here patches the /proc and psutil signals rather than reading them,
so the tests describe the DECISION and not this machine.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from tests._support import _SRC  # noqa: F401

from goblinmode import gamedetect


class WindowsBasename(unittest.TestCase):
    """Proton reports Windows paths; the rest of the module wants a basename."""

    def test_splits_on_both_separators(self):
        self.assertEqual(gamedetect._win_basename(r"C:\\Games\\Wow.exe"), "Wow.exe")
        self.assertEqual(gamedetect._win_basename("/usr/bin/game"), "game")

    def test_strips_the_quotes_a_command_line_carries(self):
        self.assertEqual(gamedetect._win_basename('"C:\\x\\Wow.exe"'), "Wow.exe")
        self.assertEqual(gamedetect._win_basename("'game'"), "game")

    def test_empty_stays_empty(self):
        self.assertEqual(gamedetect._win_basename(""), "")
        self.assertEqual(gamedetect._win_basename("   "), "")


class Blocklist(unittest.TestCase):
    def test_exact_names_are_blocked(self):
        for name in ("firefox", "code", "steamwebhelper", "python3"):
            self.assertTrue(gamedetect._blocked(name, name), name)

    def test_matching_is_case_insensitive(self):
        self.assertTrue(gamedetect._blocked("FireFox", "FIREFOX"))

    def test_desktop_environment_processes_are_blocked_by_stem(self):
        for name in ("kwin_wayland", "plasmashell", "gsd-power", "xdg-desktop-portal-kde"):
            self.assertTrue(gamedetect._blocked(name, name), name)

    def test_the_tool_never_detects_itself(self):
        """goblin-mode-pro holds a DRM fd and links GL. Without this it would
        score itself as a game and tune the machine for its own GUI."""
        self.assertTrue(gamedetect._blocked("goblin-mode-pro", "goblin-mode-pro"))
        self.assertTrue(gamedetect._blocked("goblin-mode-pro-daemon", "x"))

    def test_every_blocklist_entry_actually_blocks(self):
        """`Xorg` and `Xwayland` were written with capitals while the lookup
        lowercases its input, so both were dead entries and the display server
        was not on the effective blocklist at all. Nothing noticed, because a
        test that spells the name the same way the table does passes either
        way. This spells it both ways."""
        for entry in sorted(gamedetect._BLOCKLIST):
            with self.subTest(entry=entry):
                self.assertTrue(gamedetect._blocked(entry, entry), entry)
                self.assertTrue(gamedetect._blocked(entry.lower(), entry.lower()), entry)

    def test_every_block_stem_actually_blocks(self):
        for stem in sorted(gamedetect._BLOCK_STEMS):
            with self.subTest(stem=stem):
                self.assertTrue(gamedetect._blocked(f"{stem}x", f"{stem}x"), stem)

    def test_a_real_game_is_not_blocked(self):
        for name in ("Wow.exe", "hl2_linux", "factorio", "cyberpunk2077.exe"):
            self.assertFalse(gamedetect._blocked(name, name), name)

    def test_either_the_name_or_the_basename_can_trigger_it(self):
        """Under Proton the process name and the exe basename differ."""
        self.assertTrue(gamedetect._blocked("firefox", "Wow.exe"))
        self.assertTrue(gamedetect._blocked("Wow.exe", "firefox"))


class LauncherTags(unittest.TestCase):
    def test_steam_launch_appid_is_recognised(self):
        cmd = "/usr/bin/reaper SteamLaunch AppId=730 -- /path/game"
        self.assertEqual(gamedetect._steam_appid_from_cmd(cmd), "730")

    def test_a_bare_appid_needs_steam_in_the_command_line(self):
        """AppId= appears in plenty of unrelated command lines. Accepting it
        unconditionally is how a non-game gets a five-point launcher score."""
        self.assertIsNone(gamedetect._steam_appid_from_cmd("./tool --AppId=730"))
        self.assertEqual(
            gamedetect._steam_appid_from_cmd("steam-runtime --AppId=730"), "730")

    def test_no_tag_is_no_appid(self):
        self.assertIsNone(gamedetect._steam_appid_from_cmd("/usr/bin/firefox"))

    def test_a_running_lutris_game_is_recognised(self):
        """THE FORM THAT MATTERS. lutris-wrapper calls
        setproctitle("lutris-wrapper: " + title), which replaces the whole
        command line - so this is what a game looks like while it is running.
        The colon is not optional and was not previously matched at all, which
        cost every Lutris game its launcher score."""
        self.assertEqual(
            gamedetect._lutris_name_from_cmd("lutris-wrapper: Deus Ex"), "Deus Ex")
        self.assertEqual(
            gamedetect._lutris_name_from_cmd("lutris-wrapper: Factorio"), "Factorio")

    def test_the_wrappers_own_argv_is_also_recognised(self):
        """Before setproctitle runs: lutris-wrapper <title> <n> <n> <command>."""
        self.assertEqual(
            gamedetect._lutris_name_from_cmd(
                "/usr/share/lutris/bin/lutris-wrapper Deus Ex 0 0 /games/dx.exe"),
            "Deus Ex")

    def test_the_process_counts_are_not_absorbed_into_the_name(self):
        """The two counts follow the title. Allowing a second name word without
        excluding digits would make the name "Factorio 0"."""
        self.assertEqual(
            gamedetect._lutris_name_from_cmd(
                "/usr/share/lutris/bin/lutris-wrapper Factorio 0 0 /games/factorio"),
            "Factorio")

    def test_a_non_lutris_command_yields_nothing(self):
        self.assertIsNone(gamedetect._lutris_name_from_cmd("/usr/bin/game"))
        self.assertIsNone(gamedetect._lutris_name_from_cmd("/usr/bin/firefox"))


class Scoring(unittest.TestCase):
    """The rule that keeps the desktop safe: a process with no launcher tag
    needs corroboration from two independent signals before it is a game."""

    def _score(self, name, exe="", cmd="", *, gpu=0, libs=False, rss_mb=100):
        class _Proc:
            def memory_info(self_inner):
                class _M:
                    rss = rss_mb * 1024 * 1024
                return _M()

        with patch.object(gamedetect, "_gpu_load", lambda _pid: gpu), \
                patch.object(gamedetect, "_links_game_libs", lambda _pid: libs), \
                patch.object(gamedetect.psutil, "Process", lambda _pid: _Proc()), \
                patch.object(gamedetect, "_steam_app_name", lambda _appid: None):
            return gamedetect._score(name, exe or name, cmd, cmd.split(), 1234)

    def test_a_blocked_process_scores_nothing_whatever_it_is_doing(self):
        """Even actively rendering and linking SDL - that is a browser."""
        self.assertIsNone(self._score("firefox", gpu=2, libs=True, rss_mb=4000))

    def test_a_generic_process_that_only_renders_is_not_enough(self):
        """One signal. A video player and a compositor both render."""
        self.assertIsNone(self._score("mystery", gpu=2))

    def test_a_generic_process_needs_two_signals(self):
        """Active rendering plus game libraries plus a big RSS clears the bar;
        this is the path a DRM-free native game takes."""
        result = self._score("mystery", gpu=2, libs=True, rss_mb=1000)
        self.assertIsNotNone(result)
        score, source, _display, _appid = result
        self.assertEqual(source, "generic")
        self.assertGreaterEqual(score, 6)

    def test_holding_a_drm_fd_alone_scores_zero(self):
        """Compositors and Xwayland hold one. Only ACTIVE rendering counts, or
        every desktop session would look like a game."""
        self.assertIsNone(self._score("mystery", gpu=1, libs=True, rss_mb=1000))

    def test_a_steam_tagged_process_clears_the_bar_on_its_own(self):
        result = self._score("game", cmd="reaper SteamLaunch AppId=730 -- ./game")
        self.assertIsNotNone(result)
        score, source, display, appid = result
        self.assertEqual((source, appid), ("steam", "730"))
        self.assertGreaterEqual(score, gamedetect.GAME_SCORE)
        self.assertEqual(display, "Steam app 730")

    def test_a_lutris_tagged_process_clears_the_bar_on_its_own(self):
        result = self._score("dx.exe", cmd="lutris-wrapper: Deus Ex")
        self.assertIsNotNone(result)
        score, source, display, _appid = result
        self.assertEqual((source, display), ("lutris", "Deus Ex"))
        self.assertGreaterEqual(score, gamedetect.GAME_SCORE)

    def test_wine_scaffolding_earns_no_gpu_or_library_points(self):
        """explorer.exe and services.exe run inside every Proton prefix and
        link the same libraries as the game. Scoring them would tune the
        machine for the wrong pid and renice the scaffolding."""
        for infra in ("explorer.exe", "services.exe", "wineserver", "steam.exe"):
            with self.subTest(infra=infra):
                self.assertIsNone(self._score(infra, gpu=2, libs=True, rss_mb=1000))

    def test_a_launcher_tag_survives_the_wine_infra_rule(self):
        """The tagged process may itself be scaffolding-named; the launcher
        signal is independent of the GPU/library ones."""
        result = self._score("start.exe", cmd="reaper SteamLaunch AppId=42 -- x")
        self.assertIsNotNone(result)
        self.assertEqual(result[1], "steam")

    def test_a_missing_process_does_not_raise(self):
        """The pid can exit between listing and scoring - the common case on a
        launcher that spawns and exits."""
        import psutil as _psutil

        def _boom(_pid):
            raise _psutil.NoSuchProcess(1234)

        with patch.object(gamedetect, "_gpu_load", lambda _pid: 2), \
                patch.object(gamedetect, "_links_game_libs", lambda _pid: True), \
                patch.object(gamedetect.psutil, "Process", _boom):
            result = gamedetect._score("mystery", "mystery", "", [], 1234)
        # scored on the signals it could read, without the RSS point
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
