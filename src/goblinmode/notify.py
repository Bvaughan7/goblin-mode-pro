"""Desktop notifications via the freedesktop Notifications D-Bus interface.

The daemon isn't a ``Gio.Application`` so it can't use ``Gio.Notification``;
this talks to ``org.freedesktop.Notifications`` on the session bus directly.
Best-effort - if there's no notification daemon it just logs.
"""

from __future__ import annotations

import logging

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

log = logging.getLogger(__name__)

_APP = "Goblin Mode Pro"
_ICON = "com.goblinmode.Pro"
_proxy: Gio.DBusProxy | None = None
#: one replace-id per tag, so a routine "boost off" can't clobber a live
#: incident notification and vice versa
_ids: dict[str, int] = {}


def _get_proxy() -> Gio.DBusProxy | None:
    global _proxy
    if _proxy is not None:
        return _proxy
    try:
        _proxy = Gio.DBusProxy.new_for_bus_sync(
            Gio.BusType.SESSION, Gio.DBusProxyFlags.DO_NOT_AUTO_START, None,
            "org.freedesktop.Notifications", "/org/freedesktop/Notifications",
            "org.freedesktop.Notifications", None,
        )
        if _proxy.get_name_owner() is None:
            _proxy = None
    except GLib.Error as exc:
        log.debug("notifications unavailable: %s", exc)
        _proxy = None
    return _proxy


def send(title: str, body: str = "", *, replace: bool = True,
         urgency: int = 1, tag: str = "status") -> None:
    """Show (or replace) a notification. ``urgency``: 0 low, 1 normal, 2 critical.

    ``replace`` collapses repeats *within the same ``tag``* onto one bubble;
    different tags (status / incident / session / …) never overwrite each other.
    """
    proxy = _get_proxy()
    if proxy is None:
        log.info("notify: %s — %s", title, body)
        return
    hints = {"urgency": GLib.Variant("y", max(0, min(2, urgency))),
             "desktop-entry": GLib.Variant("s", _ICON)}
    try:
        res = proxy.call_sync(
            "Notify",
            GLib.Variant("(susssasa{sv}i)", (
                _APP, _ids.get(tag, 0) if replace else 0, _ICON, title, body,
                [], hints, 6000,
            )),
            Gio.DBusCallFlags.NONE, 3000, None,
        )
        _ids[tag] = int(res.unpack()[0])
    except GLib.Error as exc:
        log.debug("notify failed: %s", exc)
