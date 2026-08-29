import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tests._support import _SRC  # noqa: F401

from goblinmode import mangohud
from goblinmode.config import GameProfile


class ManagedBlockRoundTrip(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        d = Path(self._tmp.name)
        self._conf = d / "MangoHud.conf"
        mangohud.MANGOHUD_CONF = self._conf
        mangohud.MANGOHUD_DIR = d

    def tearDown(self):
        self._tmp.cleanup()

    def test_apply_adds_a_delimited_block(self):
        mangohud.apply(GameProfile(exe="a", mangohud={"enabled": True, "fps": True}))
        text = self._conf.read_text()
        self.assertIn(mangohud._GMP_BEGIN, text)
        self.assertIn(mangohud._GMP_END, text)
        self.assertIn("fps", text)

    def test_revert_removes_only_the_gmp_block_and_keeps_user_lines(self):
        self._conf.write_text("# my settings\nfont_size=24\ngpu_stats\n")
        prof = GameProfile(exe="a", mangohud={"enabled": True, "fps": True})
        mangohud.apply(prof)
        self.assertIn(mangohud._GMP_BEGIN, self._conf.read_text())
        mangohud.revert(prof)
        out = self._conf.read_text()
        self.assertNotIn(mangohud._GMP_BEGIN, out)
        self.assertIn("font_size=24", out)
        self.assertIn("gpu_stats", out)

    def test_watchdog_enables_csv_logging(self):
        mangohud.apply(GameProfile(exe="a", fps_watchdog=True))
        text = self._conf.read_text()
        self.assertIn("autostart_log=1", text)
        self.assertIn("output_folder=", text)


if __name__ == "__main__":
    unittest.main()
