"""The Rust and Python MangoHud configurators write the same file.

The file belongs to the user. This tool writes one fenced block into it and
takes exactly that block out again, and the promise is that everything outside
the fence comes back unchanged - blank lines, trailing spaces and hand-written
keys included. So the comparison is on the TEXT, not on a parsed config.

The corpus is weighted towards the shapes that decide it: a block already
present, a block someone re-indented, a block that was never closed (a write
that died partway), trailing blank lines that must not push the block further
down on every write, and the line terminators `str.splitlines` breaks on that
`str::lines` does not.
"""

from __future__ import annotations

import itertools
import json
import os
import subprocess
import unittest
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tests._support import _SRC  # noqa: F401

from goblinmode import mangohud
from goblinmode.config import GameProfile

_REPO = Path(__file__).resolve().parent.parent

BEGIN = "### goblin-mode-pro begin"
END = "### goblin-mode-pro end"

EXISTING = [
    "",
    "\n",
    "fps_limit=144\n",
    # No trailing newline at all.
    "fps_limit=144",
    "fps_limit=144\nfont_size=24\n\n\n",
    # Trailing spaces, which `rstrip("\\n")` does not touch.
    "fps_limit=144\n   \n",
    # Our block already there, alone and in company.
    f"{BEGIN}\nno_display=1\n{END}\n",
    f"fps_limit=144\n\n{BEGIN}\nno_display=1\n{END}\n",
    f"{BEGIN}\nno_display=1\n{END}\nafter=1\n",
    # Re-indented markers.
    f"keep=1\n   {BEGIN}\nno_display=1\n  {END}  \n",
    # A block that was never closed.
    f"keep=1\n{BEGIN}\nno_display=1\nhalf=written\n",
    # An end with no beginning.
    f"keep=1\n{END}\nafter=1\n",
    # Two blocks, which should not happen and might.
    f"{BEGIN}\na\n{END}\nmid=1\n{BEGIN}\nb\n{END}\n",
    # Lines that look like ours without being ours.
    f"# {BEGIN} but commented\nkeep=1\n",
    # Terminators `str.splitlines` breaks on and `str::lines` does not.
    "a\x0bb\n",
    "a\x0cb\n",
    "a\x1cb\n",
    "a\x85b\n",
    "a\u2028b\n",
    "a\r\nb\r\n",
    "a\rb\r",
]

TOGGLE_SETS = [(), ("fps",), ("fps", "cpu_temp", "gpu_temp", "ram", "frame_timing")]


def _binary() -> Path | None:
    override = os.environ.get("GMP_MANGOHUD_BIN")
    if override:
        return Path(override) if Path(override).exists() else None
    for profile in ("debug", "release"):
        candidate = _REPO / "target" / profile / "examples" / "mangohud"
        if candidate.exists():
            return candidate
    return None


def _profiles():
    for enabled, watchdog, toggles in itertools.product(
            (False, True), (False, True), TOGGLE_SETS):
        overlay = {"enabled": enabled}
        overlay.update({k: k in toggles for k in
                        ("fps", "cpu_temp", "gpu_temp", "ram", "frame_timing")})
        yield GameProfile(exe="Wow.exe", mangohud=overlay,
                          fps_watchdog=watchdog)


def _python_apply(existing: str, profile: GameProfile, log_dir: str) -> str:
    with TemporaryDirectory() as tmp:
        conf = Path(tmp) / "MangoHud.conf"
        conf.write_text(existing)
        with patch.object(mangohud, "MANGOHUD_CONF", conf), \
                patch.object(mangohud, "MANGOHUD_DIR", Path(tmp)), \
                patch.object(mangohud, "MANGOHUD_LOG_DIR", log_dir):
            mangohud.apply(profile)
        return conf.read_text()


def _python_revert(existing: str) -> str:
    with TemporaryDirectory() as tmp:
        conf = Path(tmp) / "MangoHud.conf"
        conf.write_text(existing)
        profile = GameProfile(exe="Wow.exe")
        with patch.object(mangohud, "MANGOHUD_CONF", conf), \
                patch.object(mangohud, "MANGOHUD_DIR", Path(tmp)):
            mangohud.revert(profile)
        return conf.read_text()


class BothImplementationsWriteTheSameFile(unittest.TestCase):
    def setUp(self):
        self.binary = _binary()
        if self.binary is None:
            if os.environ.get("GMP_REQUIRE_RUST_HELPER") == "1":
                self.fail("GMP_REQUIRE_RUST_HELPER=1 but the mangohud example "
                          "is not built - run `cargo build -p gmp-core "
                          "--example mangohud`")
            self.skipTest("build it with `cargo build -p gmp-core "
                          "--example mangohud`")

    def _rust(self, payload: dict) -> str:
        r = subprocess.run([str(self.binary)], input=json.dumps(payload),
                           capture_output=True, text=True, timeout=60,
                           check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_every_profile_writes_the_same_block(self):
        log_dir = "/home/tester/.local/share/goblin-mode-pro/mangohud"
        for profile in _profiles():
            with self.subTest(mangohud=profile.mangohud,
                              watchdog=profile.fps_watchdog):
                want = _python_apply("fps_limit=144\n", profile, log_dir)
                got = self._rust({"which": "apply", "existing": "fps_limit=144\n",
                                  "log_dir": log_dir,
                                  "profile": asdict(profile)})
                self.assertEqual(got, want)

    def test_every_existing_file_survives_a_write(self):
        profile = GameProfile(exe="Wow.exe",
                              mangohud={"enabled": True, "fps": True},
                              fps_watchdog=True)
        for existing in EXISTING:
            with self.subTest(existing=existing):
                want = _python_apply(existing, profile, "/logs")
                got = self._rust({"which": "apply", "existing": existing,
                                  "log_dir": "/logs",
                                  "profile": asdict(profile)})
                self.assertEqual(got, want)

    def test_every_existing_file_reverts_the_same(self):
        for existing in EXISTING:
            with self.subTest(existing=existing):
                self.assertEqual(
                    self._rust({"which": "revert", "existing": existing}),
                    _python_revert(existing))

    def test_a_second_write_changes_nothing(self):
        """The block must not walk down the file, and the user's keys must not
        be duplicated. This is the operation that happens most often - every
        profile edit and every launch."""
        profile = GameProfile(exe="Wow.exe", mangohud={"enabled": True},
                              fps_watchdog=True)
        once = self._rust({"which": "apply", "existing": "fps_limit=144\n",
                           "log_dir": "/logs", "profile": asdict(profile)})
        twice = self._rust({"which": "apply", "existing": once,
                            "log_dir": "/logs", "profile": asdict(profile)})
        self.assertEqual(once, twice)
        self.assertEqual(_python_apply(once, profile, "/logs"), twice)


if __name__ == "__main__":
    unittest.main()
