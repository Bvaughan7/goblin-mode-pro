#!/usr/bin/env python3
"""Regenerate docs/screenshots/*.png from the running app.

Renders each GUI page off-screen with GTK's own renderer (no compositor, no
screenshot tool) against the live daemon, so the shots show real capability
detection / profiles / pre-flight results.

    systemctl --user start goblin-mode-pro
    python3 docs/make-screenshots.py
"""

from __future__ import annotations

import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from goblinmode.gui.window import MainWindow
from goblinmode.ipc.daemon_bridge import BridgeClient

OUT = os.path.join(os.path.dirname(__file__), "..", "docs", "screenshots")
PAGES = ["dashboard", "games", "system-check", "diagnostics"]


def capture(widget: Gtk.Widget, path: str) -> None:
    w, h = widget.get_width(), widget.get_height()
    paintable = Gtk.WidgetPaintable.new(widget)
    snapshot = Gtk.Snapshot.new()
    paintable.snapshot(snapshot, w, h)
    node = snapshot.to_node()
    texture = widget.get_native().get_renderer().render_texture(
        node, Gdk.Graphene.Rect().init(0, 0, w, h) if hasattr(Gdk, "Graphene") else None
    )
    texture.save_to_png(path)
    print("wrote", path)


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    bridge = BridgeClient()
    if not bridge.connect():
        print("daemon not running: systemctl --user start goblin-mode-pro")
        return 1

    app = Adw.Application(application_id="com.goblinmode.Pro.Screenshots", flags=0)
    state = {"i": 0}

    def on_activate(a: Adw.Application) -> None:
        win = MainWindow(a, bridge)
        win.set_default_size(720, 900)
        win.present()
        a.hold()

        def page(name: str) -> Gtk.Widget:
            return {"dashboard": win.dashboard, "games": win.games,
                    "system-check": win.preflight, "diagnostics": win.diagnostics}[name]

        def expand_first_game() -> bool:
            for row in win.games._rows:
                if isinstance(row, Adw.ExpanderRow):
                    row.set_expanded(True)
                    break
            return False

        def tick() -> bool:
            i = state["i"]
            if i >= len(PAGES):
                a.release()
                a.quit()
                return False
            name = PAGES[i]
            win.set_visible_page(page(name))
            if name == "games":
                GLib.timeout_add(400, expand_first_game)
            GLib.timeout_add(1600, lambda: capture(win, f"{OUT}/{name}.png") or step())
            return False

        def step() -> bool:
            state["i"] += 1
            GLib.timeout_add(500, tick)
            return False

        GLib.timeout_add(2500, tick)

    app.connect("activate", on_activate)
    return app.run([])


if __name__ == "__main__":
    raise SystemExit(main())
