"""Main window - an ``Adw.ApplicationWindow`` with a view switcher over the
Dashboard / Games / System Check / Diagnostics pages, kept live by the daemon's
D-Bus signals.
"""

from __future__ import annotations

import logging
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk

from goblinmode import APP_ID, __version__
from goblinmode.gui.page_dashboard import DashboardPage
from goblinmode.gui.page_diagnostics import DiagnosticsPage
from goblinmode.gui.page_games import GamesPage
from goblinmode.gui.page_preflight import PreflightPage
from goblinmode.i18n import _
from goblinmode.ipc.daemon_bridge import BridgeClient

log = logging.getLogger(__name__)

_PAGES = ("dashboard", "games", "system", "diagnostics")


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application, bridge: BridgeClient) -> None:
        super().__init__(application=app, title="Goblin Mode Pro")
        self.set_default_size(860, 660)
        self.set_size_request(360, 480)
        self.bridge = bridge

        self.dashboard = DashboardPage(bridge)
        self.games = GamesPage(bridge)
        self.preflight = PreflightPage(bridge, self)
        self.diagnostics = DiagnosticsPage(bridge, self)

        self._stack = Adw.ViewStack()
        for page, name in zip(
            (self.dashboard, self.games, self.preflight, self.diagnostics), _PAGES, strict=False
        ):
            self._stack.add_titled_with_icon(
                page, name, page.get_title(),
                page.get_icon_name() or "application-x-executable-symbolic",
            )
        self.preflight.refresh()

        self._switcher = Adw.ViewSwitcher(
            stack=self._stack, policy=Adw.ViewSwitcherPolicy.WIDE
        )
        # Shown in the header only while the switcher is collapsed to the
        # bottom bar - otherwise a narrow window has a completely blank title
        # bar and nothing on screen says which app this is.
        self._title = Adw.WindowTitle(title="Goblin Mode Pro", subtitle="")
        self._title.set_visible(False)
        title_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        title_box.append(self._switcher)
        title_box.append(self._title)
        header = Adw.HeaderBar(title_widget=title_box)
        header.pack_end(self._primary_menu())

        switcher_bar = Adw.ViewSwitcherBar(stack=self._stack)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.add_bottom_bar(switcher_bar)
        toolbar.set_content(self._stack)

        self._toasts = Adw.ToastOverlay()
        self._toasts.set_child(toolbar)
        self.set_content(self._toasts)

        # narrow window: hide the header switcher, reveal the bottom bar
        # 800sp, not 560: the top ViewSwitcher needs room for four labels plus
        # the menu and the window controls, and below ~800 it truncates them
        # ("System Che…") rather than collapsing. The bottom ViewSwitcherBar
        # shows all four comfortably at any width, so hand over earlier.
        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 800sp"))
        bp.add_setter(self._switcher, "visible", False)
        bp.add_setter(self._title, "visible", True)
        bp.add_setter(switcher_bar, "reveal", True)
        self.add_breakpoint(bp)
        # keep the collapsed title's subtitle on the page you are looking at
        self._stack.connect("notify::visible-child-name", self._sync_title)
        self._sync_title()

        self._install_actions(app)

        self._metrics: list | None = None
        self._incidents: list | None = None
        bridge.on_signal(self._on_signal)
        self._refresh_all()
        GLib.timeout_add_seconds(5, self._periodic_refresh)

    # -- chrome -----------------------------------------------------
    def _primary_menu(self) -> Gtk.MenuButton:
        menu = Gio.Menu()
        menu.append(_("Keyboard Shortcuts"), "win.shortcuts")
        menu.append(_("About Goblin Mode Pro"), "win.about")
        return Gtk.MenuButton(
            icon_name="open-menu-symbolic", menu_model=menu, primary=True,
            tooltip_text=_("Main menu"),
        )

    def _install_actions(self, app: Adw.Application) -> None:
        for name, cb in (("about", self._show_about),
                         ("shortcuts", self._show_shortcuts)):
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", cb)
            self.add_action(act)

        page_act = Gio.SimpleAction.new("page", GLib.VariantType.new("s"))
        page_act.connect("activate", self._on_page_action)
        self.add_action(page_act)

        if app is not None:
            app.set_accels_for_action("win.shortcuts", ["<Control>question"])
            app.set_accels_for_action("window.close", ["<Control>w"])
            for i, name in enumerate(_PAGES, start=1):
                app.set_accels_for_action(f"win.page::{name}", [f"<Alt>{i}"])

    def _sync_title(self, *_a) -> None:
        page = self._stack.get_visible_child()
        if page is not None and hasattr(page, "get_title"):
            self._title.set_subtitle(page.get_title() or "")

    def _on_page_action(self, _act, param: GLib.Variant) -> None:
        self._stack.set_visible_child_name(param.get_string())

    def _show_about(self, *_a) -> None:
        # Adw.AboutDialog, not Adw.AboutWindow: the latter is deprecated since
        # libadwaita 1.6 - the same release that deprecated Adw.MessageDialog.
        # AboutDialog has been available since 1.5, which is our floor (see
        # gui/app.py), so this needs no version guard.
        about = Adw.AboutDialog(
            application_name="Goblin Mode Pro",
            application_icon=APP_ID,
            version=__version__,
            developer_name="Bryan Vaughan",
            license_type=Gtk.License.MIT_X11,
            comments=_("One-switch performance helper for Linux gaming: it tunes "
                       "the CPU, compositor and Proton for a game, reverts on "
                       "exit, and turns thermal / frame-rate / Proton-log "
                       "problems into a plain-language report."),
            website="https://github.com/Bvaughan7/goblin-mode-pro",
            issue_url="https://github.com/Bvaughan7/goblin-mode-pro/issues",
        )
        # "Troubleshooting -> Debug Information" in the About dialog, which is
        # the button people are told to press. Same content as
        # `goblin-mode-pro-cli selftest`, so a pasted blob from either says
        # what this machine can actually do. Read-only and best-effort.
        about.set_debug_info(self._debug_info())
        about.set_debug_info_filename("goblin-mode-pro-selftest.txt")
        about.present(self)

    @staticmethod
    def _debug_info() -> str:
        try:
            from goblinmode import selftest
            return selftest.render(selftest.SelfTest().run(), apply=False, color=False)
        except Exception as exc:                    # noqa: BLE001
            return f"selftest failed to run: {type(exc).__name__}: {exc}"

    def _show_shortcuts(self, *_a) -> None:
        groups = [
            (_("General"), [
                ("<Control>question", _("Keyboard shortcuts")),
                ("<Control>w", _("Close window")),
            ]),
            (_("Navigation"), [
                (f"<Alt>{i}", title)
                for i, title in enumerate(
                    (p.get_title() for p in (
                        self.dashboard, self.games, self.preflight, self.diagnostics
                    )), start=1)
            ]),
        ]
        if hasattr(Adw, "ShortcutsDialog"):
            self._present_shortcuts_dialog(groups)
        else:
            self._present_shortcuts_window(groups)

    # Gtk.ShortcutsWindow and its whole widget family were deprecated in GTK
    # 4.18 in favour of Adw.ShortcutsDialog - but that only landed in
    # libadwaita 1.8, and our floor is 1.5 (Ubuntu 24.04 LTS, Debian 13), which
    # the rest of the UI needs anyway for AlertDialog/AboutDialog/Breakpoint.
    # Raising the floor to 1.8 to avoid one deprecation would cost us both of
    # those distros, so we keep both paths: the modern one on new systems, the
    # deprecated one as a fallback. Checked against libadwaita 1.9.3 / GTK
    # 4.22.4; revisit when 1.8 is the oldest libadwaita we care about.
    def _present_shortcuts_dialog(self, groups) -> None:
        dialog = Adw.ShortcutsDialog()
        for title, shortcuts in groups:
            section = Adw.ShortcutsSection(title=title)
            for accel, label in shortcuts:
                section.add(Adw.ShortcutsItem(title=label, accelerator=accel))
            dialog.add(section)
        dialog.present(self)

    def _present_shortcuts_window(self, groups) -> None:
        win = Gtk.ShortcutsWindow(transient_for=self, modal=True)
        section = Gtk.ShortcutsSection(section_name="main", visible=True)
        for title, shortcuts in groups:
            group = Gtk.ShortcutsGroup(title=title)
            for accel, label in shortcuts:
                group.add_shortcut(
                    Gtk.ShortcutsShortcut(accelerator=accel, title=label)
                )
            section.add_group(group)
        win.add_section(section)
        win.present()

    def toast(self, text: str) -> None:
        self._toasts.add_toast(Adw.Toast(title=text, timeout=3))

    def set_visible_page(self, page: Gtk.Widget) -> None:
        """Jump the view switcher to a page widget (kept for the Dashboard's
        'open the check' shortcut and docs/make-screenshots.py)."""
        self._stack.set_visible_child(page)

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
        self.bridge.get_nvidia_module_state_async(lambda n, e: n is not None
                                                  and self.dashboard.update_nvidia_state(n))
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
