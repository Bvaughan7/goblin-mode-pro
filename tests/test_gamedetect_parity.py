"""The Rust and Python game detection agree.

Built boundary-first, deliberately. The gpu parity corpus was written the
other way round — cases in the middle of each band — and a mutation check
showed it caught nothing. Here every threshold gets inputs either side of it
from the start.

The asymmetry that shapes the whole module: a false negative means the tool
does nothing, a false positive means it renices somebody's browser. The cases
below are weighted towards the second.
"""

from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from tests._support import _SRC  # noqa: F401

from goblinmode import gamedetect

_REPO = Path(__file__).resolve().parent.parent
_MB = 1024 * 1024


def _binary() -> Path | None:
    override = os.environ.get("GMP_GAMEDETECT_BIN")
    if override:
        return Path(override) if Path(override).exists() else None
    for profile in ("debug", "release"):
        candidate = _REPO / "target" / profile / "examples" / "gamedetect"
        if candidate.exists():
            return candidate
    return None


def proc(**over) -> dict:
    return {"name": "mystery", "exe": "mystery", "cmd": "", "gpu_load": 0,
            "links_game_libs": False, "rss_bytes": 100 * _MB,
            "steam_app_name": None} | over


class BothImplementationsAgree(unittest.TestCase):
    def setUp(self):
        self.binary = _binary()
        if self.binary is None:
            if os.environ.get("GMP_REQUIRE_RUST_HELPER") == "1":
                self.fail("GMP_REQUIRE_RUST_HELPER=1 but the gamedetect example is "
                          "not built - run `cargo build -p gmp-core --example gamedetect`")
            self.skipTest("build it with `cargo build -p gmp-core --example gamedetect`")

    def _rust(self, p: dict) -> dict:
        r = subprocess.run([str(self.binary)], input=json.dumps(p),
                           capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def _python(self, p: dict) -> dict:
        class _Mem:
            rss = p["rss_bytes"]

        class _Proc:
            def memory_info(self):
                if p["rss_bytes"] is None:
                    raise gamedetect.psutil.NoSuchProcess(1)
                return _Mem()

        with patch.object(gamedetect, "_gpu_load", lambda _p: p["gpu_load"]), \
                patch.object(gamedetect, "_links_game_libs", lambda _p: p["links_game_libs"]), \
                patch.object(gamedetect.psutil, "Process", lambda _p: _Proc()), \
                patch.object(gamedetect, "_steam_app_name", lambda _a: p["steam_app_name"]):
            scored = gamedetect._score(p["name"], p["exe"], p["cmd"], p["cmd"].split(), 1234)
        base = gamedetect._win_basename(p["exe"])
        return {
            "win_basename": base,
            "blocked": gamedetect._blocked(p["name"], base),
            "steam_appid": gamedetect._steam_appid_from_cmd(p["cmd"]),
            "lutris_name": gamedetect._lutris_name_from_cmd(p["cmd"]),
            "score": list(scored) if scored else None,
        }

    def _same(self, p: dict) -> dict:
        py, rs = self._python(p), self._rust(p)
        self.assertEqual(py, rs)
        return py

    # ---- every threshold, both sides --------------------------------------

    def test_the_generic_corroboration_bar(self):
        """A generic process needs 6. Rendering alone is 3, libraries alone 2,
        a big RSS 1 - so no single signal can reach it, which is the point."""
        for gpu in (0, 1, 2):
            for libs in (False, True):
                for rss in (700 * _MB, 700 * _MB + 1):
                    with self.subTest(gpu=gpu, libs=libs, rss=rss):
                        self._same(proc(gpu_load=gpu, links_game_libs=libs, rss_bytes=rss))

    def test_holding_a_drm_fd_is_not_rendering(self):
        """Level 1 is where a compositor lands. Scoring it would make every
        desktop session look like a game."""
        one = self._same(proc(gpu_load=1, links_game_libs=True, rss_bytes=800 * _MB))
        two = self._same(proc(gpu_load=2, links_game_libs=True, rss_bytes=800 * _MB))
        self.assertIsNone(one["score"])
        self.assertIsNotNone(two["score"])

    def test_the_resident_set_boundary(self):
        for rss in (700 * _MB - 1, 700 * _MB, 700 * _MB + 1, None):
            with self.subTest(rss=rss):
                self._same(proc(gpu_load=2, links_game_libs=True, rss_bytes=rss))

    # ---- the blocklist, which is what protects the desktop -----------------

    def test_every_blocklisted_name_is_refused_however_busy(self):
        for name in sorted(gamedetect._BLOCKLIST):
            with self.subTest(name=name):
                out = self._same(proc(name=name, exe=name, gpu_load=2,
                                      links_game_libs=True, rss_bytes=4000 * _MB))
                self.assertIsNone(out["score"], f"{name} scored")

    def test_every_desktop_stem_is_refused(self):
        for stem in sorted(gamedetect._BLOCK_STEMS):
            with self.subTest(stem=stem):
                out = self._same(proc(name=f"{stem}-something", exe=f"{stem}-something",
                                      gpu_load=2, links_game_libs=True))
                self.assertIsNone(out["score"])

    def test_the_tool_never_detects_itself(self):
        for name in ("goblin-mode-pro", "goblin-mode-pro-daemon"):
            with self.subTest(name=name):
                out = self._same(proc(name=name, exe=name, gpu_load=2,
                                      links_game_libs=True, rss_bytes=900 * _MB))
                self.assertIsNone(out["score"])

    def test_either_the_name_or_the_basename_blocks(self):
        self._same(proc(name="firefox", exe=r"C:\Games\Wow.exe"))
        self._same(proc(name="Wow.exe", exe="/usr/bin/firefox"))

    # ---- wine scaffolding --------------------------------------------------

    def test_scaffolding_earns_no_gpu_or_library_points(self):
        for infra in sorted(gamedetect._WINE_INFRA):
            with self.subTest(infra=infra):
                self._same(proc(name=infra, exe=infra, gpu_load=2,
                                links_game_libs=True, rss_bytes=1000 * _MB))

    def test_a_launcher_tag_still_counts_for_scaffolding(self):
        """The tagged process may itself be named like scaffolding; the
        launcher signal is independent of the GPU and library ones."""
        out = self._same(proc(name="start.exe", exe="start.exe",
                              cmd="reaper SteamLaunch AppId=42 -- x", gpu_load=2))
        self.assertEqual(out["score"][1], "steam")

    # ---- launcher tags -----------------------------------------------------

    def test_steam_command_lines(self):
        for cmd in (
            "/usr/bin/reaper SteamLaunch AppId=730 -- /path/game",
            "./tool --AppId=730",                       # no steam: not a tag
            "steam-runtime --AppId=730",                # steam: is a tag
            "SteamLaunch AppId=0 -- x",
            "no tag here",
        ):
            with self.subTest(cmd=cmd):
                self._same(proc(cmd=cmd))

    def test_the_steam_display_name_falls_back_to_the_appid(self):
        self._same(proc(cmd="SteamLaunch AppId=730 -- x", steam_app_name=None))
        self._same(proc(cmd="SteamLaunch AppId=730 -- x", steam_app_name="Counter-Strike"))

    def test_lutris_command_lines(self):
        for cmd in (
            "lutris-wrapper: Deus Ex",                                  # running
            "lutris-wrapper: Factorio",
            "/usr/share/lutris/bin/lutris-wrapper Deus Ex 0 0 /g/dx",   # pre-exec
            "/usr/share/lutris/bin/lutris-wrapper Factorio 0 0 /g/f",
            "lutris-wrapper:",                                          # empty title
            "not lutris at all",
        ):
            with self.subTest(cmd=cmd):
                self._same(proc(cmd=cmd))

    def test_heroic_command_lines(self):
        for cmd in ("heroic --game x", "legendary -- launch x", "gogdl launch",
                    "/nile run", "hero worship"):
            with self.subTest(cmd=cmd):
                self._same(proc(cmd=cmd))

    # ---- basenames ---------------------------------------------------------

    def test_windows_and_posix_basenames(self):
        for exe in (r"C:\Games\Wow.exe", "/usr/bin/game", '"C:\\x\\Wow.exe"',
                    "'game'", "", "   ", "bare.exe", r"mixed/path\to\thing.exe"):
            with self.subTest(exe=exe):
                self._same(proc(exe=exe))


if __name__ == "__main__":
    unittest.main()
