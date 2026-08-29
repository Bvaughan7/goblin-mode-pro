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

        self._metrics: list | None = None
        self._incidents: list | None = None
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
            self.bridge.get_status_async(
                lambda s, _e: s and self.games.load_profiles(s.get("profiles", []))
            )

    def _refresh_all(self) -> None:
        # All reads are async so a busy daemon never freezes the window.
        self.bridge.get_status_async(self._apply_status)
        self._metrics = self._incidents = None
        self.bridge.get_metrics_async(self._got_metrics)
        self.bridge.get_incidents_async(self._got_incidents)
        self.bridge.get_sessions_async(lambda s, e: s is not None
                                       and self.diagnostics.load_sessions(s))
        self.bridge.get_health_async(lambda h, e: h is not None
                                     and self.dashboard.update_health(h))
        self.bridge.get_system_info_async(lambda i, e: i is not None
                                          and self.dashboard.update_system_info(i))
        self.request_proton_refresh()

    def request_proton_refresh(self) -> None:
        self.bridge.get_proton_info_async(lambda p, e: p is not None
                                          and self.diagnostics.load_proton_info(p))

    def _apply_status(self, status, err) -> None:
        if not status:
            if err:
                log.debug("status refresh failed: %s", err)
            return
        self.dashboard.update_status(status)
        self.games.update_status(status)
        self.diagnostics.update_status(status)
        self.games.load_profiles(status.get("profiles", []))

    def _got_metrics(self, metrics, _err) -> None:
        self._metrics = metrics or []
        self._maybe_load_history()

    def _got_incidents(self, incidents, _err) -> None:
        self._incidents = incidents or []
        self._maybe_load_history()

    def _maybe_load_history(self) -> None:
        if self._metrics is not None and self._incidents is not None:
            self.diagnostics.load_history(self._metrics, self._incidents)

    def _periodic_refresh(self) -> bool:
        if self.bridge.available:
            self.bridge.get_status_async(self._apply_periodic)
            self._periodic_n = getattr(self, "_periodic_n", 0) + 1
            if self._periodic_n % 6 == 0:  # ~30 s
                self.bridge.get_health_async(lambda h, e: h is not None
                                             and self.dashboard.update_health(h))
                self.bridge.get_system_info_async(lambda i, e: i is not None
                                                  and self.dashboard.update_system_info(i))
        return True

    def _apply_periodic(self, status, _err) -> None:
        if status:
            self.dashboard.update_status(status)
            self.games.update_status(status)

    def toast(self, text: str) -> None:
        self.add_toast(Adw.Toast(title=text, timeout=3))
