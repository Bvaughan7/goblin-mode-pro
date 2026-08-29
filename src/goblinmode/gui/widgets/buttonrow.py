"""A clickable row with a leading icon, styled and behaving like
``Adw.ButtonRow`` - which needs libadwaita >= 1.6, not yet available on
every target distro (notably Ubuntu 24.04 LTS's shipped package). This is
a portable ``Adw.ActionRow`` built the way ``Adw.ButtonRow`` itself is
under the hood: ``activatable=True`` makes the row emit the same
"activated" signal on click, so every existing ``row.connect("activated",
...)`` call site works unchanged."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402


def button_row(title: str, icon: str | None = None) -> Adw.ActionRow:
    row = Adw.ActionRow(title=title, activatable=True)
    if icon:
        row.add_prefix(Gtk.Image.new_from_icon_name(icon))
    return row
