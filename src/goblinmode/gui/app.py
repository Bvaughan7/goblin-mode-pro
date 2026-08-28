"""GTK4 / Libadwaita front-end - launched on demand from the tray or .desktop.

Pure client of the daemon: it reads status/metrics/incidents over the session
bus and writes profile changes back. It performs no privileged or monitoring
work itself, so it can be opened and closed freely with no footprint cost.
"""

from __future__ import annotations

import logging
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from goblinmode import APP_ID
from goblinmode.ipc.daemon_bridge import BridgeClient

log = logging.getLogger("goblinmode.gui")


class GoblinModeApp(Adw.Application):
    def __init__(self) -> None:
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self.bridge = BridgeClient()
        self._window: Gtk.Window | None = None

    def do_activate(self) -> None:  # noqa: N802 (GObject vfunc)
        if self._window is not None:
            self._window.present()
            return

        connected = self.bridge.connect()
        if not connected:
            self._window = _DaemonMissingWindow(self)
        else:
            from goblinmode.gui.window import MainWindow

            self._window = MainWindow(self, self.bridge)
        self._window.present()

    def do_startup(self) -> None:  # noqa: N802
        Adw.Application.do_startup(self)
        Adw.init()


class _DaemonMissingWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application) -> None:
        super().__init__(application=app, title="Goblin Mode Pro")
        self.set_default_size(520, 400)
        self._app = app

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())

        status = Adw.StatusPage(
            icon_name="applications-games-symbolic",
            title="Daemon not running",
            description=(
                "The Goblin Mode Pro background service is not active. "
                "Start it to manage game performance."
            ),
        )
        btn = Gtk.Button(label="Start background service")
        btn.add_css_class("suggested-action")
        btn.add_css_class("pill")
        btn.set_halign(Gtk.Align.CENTER)
        btn.connect("clicked", self._on_start)
        status.set_child(btn)

        toolbar.set_content(status)
        self.set_content(toolbar)

    def _on_start(self, _btn: Gtk.Button) -> None:
        try:
            Gio.Subprocess.new(
                ["systemctl", "--user", "start", "goblin-mode-pro.service"],
                Gio.SubprocessFlags.NONE,
            )
        except GLib.Error as exc:
            log.warning("could not start service: %s", exc)
        GLib.timeout_add_seconds(2, self._retry)

    def _retry(self) -> bool:
        if self._app.bridge.connect():
            self.close()
            self._app._window = None
            self._app.activate()
        return False


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    app = GoblinModeApp()
    return app.run(argv if argv is not None else sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
