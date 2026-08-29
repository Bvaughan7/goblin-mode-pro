#!/usr/bin/env python3
"""GUI smoke test: construct MainWindow (and therefore every page - Dashboard,
Games, System Check, Diagnostics) under a headless X server and confirm none
of it crashes on construction. All bridge calls are stubbed with static data
so this never touches a real daemon or D-Bus session beyond what GTK/Adw
themselves need.

Run via ``xvfb-run`` (and ideally ``dbus-run-session``) - GTK4 needs a real
display connection even off-screen:

    xvfb-run -a dbus-run-session -- python3 tests/gui_smoke.py

Deliberately NOT part of ``unittest discover -s tests`` - the rest of the
suite is GTK-free by design (see CONTRIBUTING.md) so it runs on the bare
system Python; this is the one exception, gated behind its own CI job
(ci.yml's gui-smoke job) since it needs GTK4/libadwaita and a display.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib  # noqa: E402


class _FakeBridge:
    """Every method MainWindow/_refresh_all/PreflightPage.refresh() call at
    construction time, each returning static data synchronously - no real
    D-Bus round trip, so this can't hang or flake on a slow/missing daemon."""

    available = False

    def connect(self) -> bool:
        return True

    def on_signal(self, callback) -> None:
        pass

    def run_preflight_async(self, on_done) -> None:
        on_done([], None)

    def get_status_async(self, on_done) -> None:
        on_done({"profiles": [], "active_games": [], "capabilities": {}}, None)

    def get_metrics_async(self, on_done) -> None:
        on_done([], None)

    def get_incidents_async(self, on_done) -> None:
        on_done([], None)

    def get_sessions_async(self, on_done) -> None:
        on_done([], None)

    def get_health_async(self, on_done) -> None:
        on_done({"score": 8.0, "counts": {}, "worst": []}, None)

    def get_system_info_async(self, on_done) -> None:
        on_done({"controllers": [], "gamemode": {}}, None)

    def get_nvidia_module_state_async(self, on_done) -> None:
        on_done({"present": False, "modeset": None, "gsp_firmware_version": None}, None)

    def get_proton_info_async(self, on_done) -> None:
        on_done({"builds": [], "shader_caches": []}, None)


def main() -> int:
    Adw.init()
    app = Adw.Application(application_id="com.goblinmode.Pro.GuiSmokeTest",
                          flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
    result = {"ok": False, "error": None}

    def on_activate(_app) -> None:
        try:
            from goblinmode.gui.window import MainWindow

            win = MainWindow(app, _FakeBridge())
            win.present()
            result["ok"] = True
            print("MainWindow constructed and presented OK "
                  "(Dashboard, Games, System Check, Diagnostics all built)")
        except Exception as exc:  # noqa: BLE001
            result["error"] = exc
            print(f"GUI smoke test FAILED: {exc!r}", file=sys.stderr)
        finally:
            GLib.timeout_add(200, lambda: (app.quit(), False)[1])

    app.connect("activate", on_activate)
    app.run([])
    if result["error"] is not None:
        raise result["error"]
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
