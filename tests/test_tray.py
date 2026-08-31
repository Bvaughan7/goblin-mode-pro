"""The testable part of the tray: the pystray icon-handoff fix.

KDE Plasma's SNI host resolves ``IconName`` via ``QIcon::fromTheme`` and has no
reliable file-path fallback, so pystray's temp-file path handoff renders a blank
square. goblinmode.tray patches pystray to pass the installed theme name instead.
"""

from __future__ import annotations

import unittest

from tests._support import _SRC  # noqa: F401

from goblinmode import tray


class PystrayTrayIconPatch(unittest.TestCase):
    def test_patch_is_safe_to_call_and_idempotent(self):
        tray._patch_pystray_tray_icon()
        tray._patch_pystray_tray_icon()

    def test_patched_icon_path_is_the_theme_name_when_installed(self):
        try:
            from pystray._util.gtk import GtkIcon
        except Exception:  # noqa: BLE001 - a missing GI typelib raises many
            # different things (ImportError, ValueError, GLib.Error); this is
            # a skip guard, so the exact type is genuinely not interesting.
            # pragma: no cover - needs gir1.2-gtk-3.0
            self.skipTest("pystray SNI backend not importable here")

        tray._patch_pystray_tray_icon()
        if tray._TRAY_ICON_NAME is None:
            self.skipTest("com.goblinmode.Pro not in the icon theme on this box")

        class _Fake:
            icon = tray._icon_image(False)

        _Fake._update_fs_icon = GtkIcon._update_fs_icon
        f = _Fake()
        f._update_fs_icon()
        # a theme name, not a filesystem path
        self.assertIn(f._icon_path, tray._THEME_ICONS)
        self.assertNotIn("/", f._icon_path)


if __name__ == "__main__":
    unittest.main()
