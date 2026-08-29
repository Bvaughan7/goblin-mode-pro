import subprocess
import unittest
from unittest.mock import patch

from tests._support import _SRC  # noqa: F401

from goblinmode import compositor


def _cp(stdout="", returncode=0):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


_KSCREEN_TWO_OUTPUTS = """\
Output: 1 DP-1
  Vrr: automatic
Output: 2 HDMI-A-1
  Vrr: incapable
Output: 3 DP-2
  Vrr: never
"""


class VrrOutputParsing(unittest.TestCase):
    def test_incapable_outputs_are_excluded(self):
        with patch("goblinmode.compositor._run", return_value=_cp(_KSCREEN_TWO_OUTPUTS)):
            outs = compositor._vrr_outputs()
        self.assertEqual(outs, {"DP-1": "automatic", "DP-2": "never"})


class KdePerOutputVrr(unittest.TestCase):
    def setUp(self):
        self.c = compositor.Compositor()
        self._patches = [
            patch("goblinmode.compositor._is_kde", return_value=True),
            patch("goblinmode.compositor._is_hyprland", return_value=False),
            patch("goblinmode.compositor.shutil.which", return_value="/usr/bin/x"),
            patch("goblinmode.compositor._vrr_outputs",
                  return_value={"DP-1": "never", "DP-2": "never"}),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

    def test_no_outputs_arg_touches_every_capable_output(self):
        with patch("goblinmode.compositor._set_vrr", return_value=True) as set_vrr:
            self.assertTrue(self.c.enable_adaptive_sync())
        self.assertEqual({c.args[0] for c in set_vrr.call_args_list}, {"DP-1", "DP-2"})

    def test_outputs_arg_restricts_to_named_outputs(self):
        with patch("goblinmode.compositor._set_vrr", return_value=True) as set_vrr:
            self.assertTrue(self.c.enable_adaptive_sync(outputs=["DP-1"]))
        self.assertEqual([c.args[0] for c in set_vrr.call_args_list], ["DP-1"])

    def test_outputs_arg_matching_nothing_skips(self):
        with patch("goblinmode.compositor._set_vrr") as set_vrr:
            self.assertFalse(self.c.enable_adaptive_sync(outputs=["DP-99"]))
        set_vrr.assert_not_called()

    def test_restore_only_touches_what_was_changed(self):
        with patch("goblinmode.compositor._set_vrr", return_value=True):
            self.c.enable_adaptive_sync(outputs=["DP-1"])
        with patch("goblinmode.compositor._set_vrr", return_value=True) as set_vrr:
            self.c.restore_adaptive_sync()
        self.assertEqual([c.args[0] for c in set_vrr.call_args_list], ["DP-1"])


class HyprlandBackend(unittest.TestCase):
    def setUp(self):
        self.c = compositor.Compositor()
        self._patches = [
            patch("goblinmode.compositor._is_kde", return_value=False),
            patch("goblinmode.compositor._is_hyprland", return_value=True),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

    def test_tearing_supported_on_hyprland(self):
        self.assertTrue(self.c.tearing_supported)

    def test_adaptive_sync_supported_on_hyprland(self):
        self.assertTrue(self.c.adaptive_sync_supported)

    def test_enable_and_restore_tearing_round_trips_saved_value(self):
        with patch("goblinmode.compositor._hyprctl_get_option", return_value="0"), \
             patch("goblinmode.compositor._hyprctl_set_option", return_value=True) as set_opt:
            self.assertTrue(self.c.enable_tearing())
        set_opt.assert_called_with("general:allow_tearing", "1")

        with patch("goblinmode.compositor._hyprctl_set_option", return_value=True) as set_opt:
            self.assertTrue(self.c.restore_tearing())
        set_opt.assert_called_with("general:allow_tearing", "0")

    def test_enable_and_restore_vrr_uses_misc_vrr_global(self):
        with patch("goblinmode.compositor._hyprctl_get_option", return_value="0"), \
             patch("goblinmode.compositor._hyprctl_set_option", return_value=True) as set_opt:
            self.assertTrue(self.c.enable_adaptive_sync())
        set_opt.assert_called_with("misc:vrr", "1")

        with patch("goblinmode.compositor._hyprctl_set_option", return_value=True) as set_opt:
            self.assertTrue(self.c.restore_adaptive_sync())
        set_opt.assert_called_with("misc:vrr", "0")

    def test_outputs_arg_is_accepted_but_ignored(self):
        # No per-output equivalent exists on Hyprland - must not raise or
        # silently do nothing different from the no-arg call.
        with patch("goblinmode.compositor._hyprctl_get_option", return_value="0"), \
             patch("goblinmode.compositor._hyprctl_set_option", return_value=True) as set_opt:
            self.assertTrue(self.c.enable_adaptive_sync(outputs=["DP-1"]))
        set_opt.assert_called_with("misc:vrr", "1")


class HyprctlOptionParsing(unittest.TestCase):
    def test_get_option_parses_int_field(self):
        with patch("goblinmode.compositor._run", return_value=_cp('{"int": 1}')):
            self.assertEqual(compositor._hyprctl_get_option("misc:vrr"), "1")

    def test_get_option_returns_none_on_bad_json(self):
        with patch("goblinmode.compositor._run", return_value=_cp("not json")):
            self.assertIsNone(compositor._hyprctl_get_option("misc:vrr"))

    def test_get_option_returns_none_when_hyprctl_fails(self):
        with patch("goblinmode.compositor._run", return_value=None):
            self.assertIsNone(compositor._hyprctl_get_option("misc:vrr"))


if __name__ == "__main__":
    unittest.main()
