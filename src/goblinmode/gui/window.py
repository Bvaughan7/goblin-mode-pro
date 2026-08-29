"""Main window - an ``Adw.PreferencesWindow`` with Dashboard / Games /
Diagnostics pages, kept live by the daemon's D-Bus signals.
"""

from __future__ import annotations

import logging
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from goblinmode.gui.page_dashboard import DashboardPage
from goblinmode.gui.page_diagnostics import DiagnosticsPage
from goblinmode.gui.page_games import GamesPage
from goblinmode.gui.page_preflight import PreflightPage
from goblinmode.ipc.daemon_bridge import BridgeClient

log = logging.getLogger(__name__)


class MainWindow(Adw.PreferencesWindow):
    def __init__(self, app: Adw.Application, bridge: BridgeClient) -> None:
        super().__init__(application=app, title="Goblin Mode Pro")
        self.set_default_size(820, 640)
        self.set_search_enabled(False)
        self.bridge = bridge

        self.dashboard = DashboardPage(bridge)
        self.games = GamesPage(bridge)
        self.preflight = PreflightPage(bridge, self)
        self.diagnostics = DiagnosticsPage(bridge, self)

        for page in (self.dashboard, self.games, self.preflight, self.diagnostics):
            self.add(page)
        self.preflight.refresh()

        bridge.on_signal(self._on_signal)
        self._refresh_all()
        GLib.timeout_add_seconds(5, self._periodic_refresh)

    # -- data flow ----------------------------------------------------
    def _on_signal(self, name: str, payload: Any) -> None:
        if name == "StatusChanged" and payload:
            self.dashboard.update_status(payload)
            self.games.update_status(payload)
            self.diagnostics.update_status(payload)
        elif name == "MetricsUpdated" and payload:
            self.dashboard.update_sample(payload)
            self.diagnostics.push_sample(payload)
        elif name == "IncidentLogged" and payload:
            self.diagnostics.add_incident(payload)
        elif name == "SessionLogged" and payload:
            self.diagnostics.add_session(payload)
        elif name == "GameDetected" and payload:
            self.toast(f"Auto-detected {payload.get('display_name', 'a game')} "
                       f"via {payload.get('source', '?')}")
            try:
                self.games.load_profiles(self.bridge.get_status().get("profiles", []))
            except Exception:  # noqa: BLE001
                pass

    def _refresh_all(self) -> None:
        try:
            status = self.bridge.get_status()
        except Exception as exc:  # noqa: BLE001
            log.warning("status refresh failed: %s", exc)
            return
        self.dashboard.update_status(status)
        self.games.update_status(status)
        self.diagnostics.update_status(status)
        self.games.load_profiles(status.get("profiles", []))
        try:
            self.diagnostics.load_history(
                self.bridge.get_metrics(), self.bridge.get_incidents()
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("history load failed: %s", exc)
        try:
            self.diagnostics.load_sessions(self.bridge.get_sessions())
        except Exception as exc:  # noqa: BLE001
            log.debug("session load failed: %s", exc)

    def _periodic_refresh(self) -> bool:
        if not self.bridge.available:
            return True
        try:
            status = self.bridge.get_status()
            self.dashboard.update_status(status)
            self.games.update_status(status)
        except Exception:  # noqa: BLE001
            pass
        return True

    def toast(self, text: str) -> None:
        self.add_toast(Adw.Toast(title=text, timeout=3))
