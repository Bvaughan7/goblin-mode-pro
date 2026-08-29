"""A one-line shell command with a copy button - used anywhere the UI tells
the user to run something in a terminal."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from goblinmode.i18n import _


def command_row(why: str, command: str) -> Adw.ActionRow:
    """An ActionRow: ``why`` is the plain-language reason (the row title), and
    the row body is the selectable monospace ``command`` with a copy button."""
    row = Adw.ActionRow()

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                  margin_top=8, margin_bottom=8, hexpand=True)
    label = Gtk.Label(label=why, xalign=0, wrap=True)
    code = Gtk.Label(label=command, xalign=0, selectable=True, wrap=True)
    code.add_css_class("monospace")
    code.add_css_class("dim-label")
    box.append(label)
    box.append(code)
    row.set_child(box)

    copy = Gtk.Button(icon_name="edit-copy-symbolic", valign=Gtk.Align.CENTER,
                      tooltip_text=_("Copy command"))
    copy.add_css_class("flat")

    def _do_copy(btn: Gtk.Button) -> None:
        disp = Gdk.Display.get_default()
        if disp is not None:
            disp.get_clipboard().set(command)
        btn.set_icon_name("object-select-symbolic")
        GLib.timeout_add_seconds(
            2, lambda: (btn.set_icon_name("edit-copy-symbolic"), False)[1])

    copy.connect("clicked", _do_copy)
    row.add_suffix(copy)
    return row
