"""Focus mode - quiet the desktop while a game runs.

Best-effort, reversible, no privileges:

* suspend the file indexer (KDE Baloo / GNOME Tracker) - a real background-stutter
  source while a game streams assets;
* inhibit the screensaver / idle via the freedesktop ScreenSaver interface;
* (KDE) turn on Do Not Disturb.

Everything is restored on :meth:`exit`. Unsupported bits no-op with a log line.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

log = logging.getLogger(__name__)


def _run(cmd: list[str]) -> bool:
    try:
        subprocess.run(cmd, check=False, capture_output=True, timeout=6)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


class FocusMode:
    def __init__(self) -> None:
        self._active = False
        self._ss_cookie: int | None = None
        self._ss_proxy: Gio.DBusProxy | None = None
        self._baloo_suspended = False
        self._tracker_paused = False
        self._kde_dnd = False

    # -- lifecycle ---------------------------------------------------
    def enter(self) -> None:
        if self._active:
            return
        self._active = True
        self._suspend_indexer()
        self._inhibit_idle()
        self._set_kde_dnd(True)
        log.info("focus mode on")

    def exit(self) -> None:
        if not self._active:
            return
        self._resume_indexer()
        self._uninhibit_idle()
        self._set_kde_dnd(False)
        self._active = False
        log.info("focus mode off")

    def force_restore(self) -> None:
        """Cold crash-recovery: undo every focus-mode side effect without
        relying on the in-memory flags (gone after a daemon crash). The
        screensaver inhibit is released automatically when the process that
        held it dies, so only the indexer and DND need explicit undoing.
        Idempotent - safe to call when focus mode was never on."""
        for tool in ("balooctl6", "balooctl"):
            if shutil.which(tool):
                _run([tool, "resume"])
                break
        if shutil.which("tracker3"):
            _run(["tracker3", "daemon", "--resume"])
        self._set_kde_dnd(False)
        self._active = False

    # -- indexer ---------------------------------------------------
    def _suspend_indexer(self) -> None:
        for tool in ("balooctl6", "balooctl"):
            if shutil.which(tool) and _run([tool, "suspend"]):
                self._baloo_suspended = True
                return
        if shutil.which("tracker3") and _run(["tracker3", "daemon", "--pause", "goblin-mode-pro"]):
            self._tracker_paused = True

    def _resume_indexer(self) -> None:
        if self._baloo_suspended:
            for tool in ("balooctl6", "balooctl"):
                if shutil.which(tool) and _run([tool, "resume"]):
                    break
            self._baloo_suspended = False
        if self._tracker_paused:
            _run(["tracker3", "daemon", "--resume"])
            self._tracker_paused = False

    # -- idle inhibit --------------------------------------------
    def _inhibit_idle(self) -> None:
        try:
            self._ss_proxy = Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SESSION, Gio.DBusProxyFlags.NONE, None,
                "org.freedesktop.ScreenSaver", "/org/freedesktop/ScreenSaver",
                "org.freedesktop.ScreenSaver", None,
            )
            res = self._ss_proxy.call_sync(
                "Inhibit", GLib.Variant("(ss)", ("Goblin Mode Pro", "gaming")),
                Gio.DBusCallFlags.NONE, 3000, None,
            )
            self._ss_cookie = res.unpack()[0]
        except GLib.Error as exc:
            log.debug("screensaver inhibit failed: %s", exc)
            self._ss_proxy = None

    def _uninhibit_idle(self) -> None:
        if self._ss_proxy is not None and self._ss_cookie is not None:
            try:
                self._ss_proxy.call_sync(
                    "UnInhibit", GLib.Variant("(u)", (self._ss_cookie,)),
                    Gio.DBusCallFlags.NONE, 3000, None,
                )
            except GLib.Error:
                pass
        self._ss_proxy = None
        self._ss_cookie = None

    # -- KDE Do Not Disturb -------------------------------------
    def _set_kde_dnd(self, on: bool) -> None:
        if "KDE" not in os.environ.get("XDG_CURRENT_DESKTOP", "").upper():
            return
        if not shutil.which("kwriteconfig6"):
            return
        # a far-future / cleared "not before" is how Plasma stores DND
        value = "2099-01-01T00:00:00" if on else ""
        if _run(["kwriteconfig6", "--file", "plasmanotifyrc", "--group",
                 "DoNotDisturb", "--key", "Until", value]):
            self._kde_dnd = on
            _run(["qdbus6", "org.freedesktop.Notifications",
                  "/org/freedesktop/Notifications", "org.kde.Notifications.setInhibited",
                  "true" if on else "false"])

    @property
    def active(self) -> bool:
        return self._active
