"""The one piece of tray logic that's testable without a display: the pystray
temp-icon path fix (see goblinmode.tray._patch_pystray_icon_extension)."""

from __future__ import annotations

import unittest

from tests._support import _SRC  # noqa: F401

from goblinmode import tray


class PystrayIconExtensionPatch(unittest.TestCase):
    def test_patch_is_safe_to_call_without_a_gtk_backend(self):
        # On the no-GTK CI runner pystray's SNI backend won't import; the
        # patch must swallow that and no-op rather than raise.
        tray._patch_pystray_icon_extension()
        tray._patch_pystray_icon_extension()  # idempotent

    def test_patched_temp_path_carries_a_png_suffix(self):
        try:
            from pystray._util.gtk import GtkIcon
        except Exception:  # pragma: no cover - needs gir1.2-gtk-3.0
            self.skipTest("pystray SNI backend not importable here")

        tray._patch_pystray_icon_extension()
        self.assertTrue(getattr(GtkIcon, "_gmp_png_suffix", False))

        import os

        class _Fake:
            icon = tray._icon_image(False)

        _Fake._update_fs_icon = GtkIcon._update_fs_icon
        f = _Fake()
        f._update_fs_icon()
        try:
            self.assertTrue(f._icon_path.endswith(".png"),
                            "KDE's QIcon(path) can't load an extensionless file")
        finally:
            os.unlink(f._icon_path)


if __name__ == "__main__":
    unittest.main()
