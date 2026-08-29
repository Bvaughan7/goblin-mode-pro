import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tests._support import _SRC  # noqa: F401

from goblinmode import shadercache


class PrewarmShaderCache(unittest.TestCase):
    def test_no_appid_is_a_clean_noop(self):
        ok, msg = shadercache.prewarm_shader_cache("")
        self.assertFalse(ok)
        self.assertIn("AppID", msg)

    def test_no_steam_install_is_a_clean_noop(self):
        with patch("goblinmode.shadercache._fossilize_replay", return_value=None):
            ok, msg = shadercache.prewarm_shader_cache("123456")
        self.assertFalse(ok)
        self.assertIn("fossilize_replay", msg)

    def test_no_downloaded_archive_is_a_clean_noop(self):
        with patch("goblinmode.shadercache._fossilize_replay", return_value="/x/fossilize_replay"), \
             patch("goblinmode.shadercache._shader_archives", return_value=[]):
            ok, msg = shadercache.prewarm_shader_cache("123456")
        self.assertFalse(ok)
        self.assertIn("no downloaded", msg)

    def test_success_replays_every_archive(self):
        import subprocess

        cp = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with patch("goblinmode.shadercache._fossilize_replay", return_value="/x/fossilize_replay"), \
             patch("goblinmode.shadercache._shader_archives",
                   return_value=[Path("/x/a.foz"), Path("/x/b.foz")]), \
             patch("goblinmode.shadercache.subprocess.run", return_value=cp) as run:
            ok, msg = shadercache.prewarm_shader_cache("123456")
        self.assertTrue(ok)
        self.assertIn("2 archive", msg)
        argv = run.call_args[0][0]
        self.assertEqual(argv[0], "/x/fossilize_replay")
        self.assertIn("/x/a.foz", argv)
        self.assertIn("/x/b.foz", argv)

    def test_nonzero_exit_is_reported_not_raised(self):
        import subprocess

        cp = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="boom")
        with patch("goblinmode.shadercache._fossilize_replay", return_value="/x/fossilize_replay"), \
             patch("goblinmode.shadercache._shader_archives", return_value=[Path("/x/a.foz")]), \
             patch("goblinmode.shadercache.subprocess.run", return_value=cp):
            ok, msg = shadercache.prewarm_shader_cache("123456")
        self.assertFalse(ok)
        self.assertIn("1", msg)


class ShaderArchiveDiscovery(unittest.TestCase):
    def test_finds_foz_files_under_the_appid_dir(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            cache_dir = root / "steamapps" / "shadercache" / "123456"
            cache_dir.mkdir(parents=True)
            (cache_dir / "a.foz").write_bytes(b"")
            (cache_dir / "b.txt").write_bytes(b"")  # not a shader archive
            with patch("goblinmode.shadercache._steam_root", return_value=root):
                found = shadercache._shader_archives("123456")
        self.assertEqual([p.name for p in found], ["a.foz"])

    def test_missing_appid_dir_returns_empty(self):
        with TemporaryDirectory() as d:
            with patch("goblinmode.shadercache._steam_root", return_value=Path(d)):
                self.assertEqual(shadercache._shader_archives("999999"), [])


if __name__ == "__main__":
    unittest.main()
