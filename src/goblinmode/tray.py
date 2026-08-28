"""System-tray icon (pystray, AppIndicator/SNI backend).

KDE Plasma shows this natively via StatusNotifierItem. The ``AyatanaAppIndicator3``
GIR is not present on this system but the legacy ``AppIndicator3`` one is, so we
pin pystray to the ``appindicator`` backend before importing it.

The icon runs *detached* - it hooks into the daemon's existing GLib main loop
rather than starting its own.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Callable

os.environ.setdefault("PYSTRAY_BACKEND", "appindicator")

log = logging.getLogger(__name__)

try:
    import pystray  # noqa: E402
    from PIL import Image, ImageDraw  # noqa: E402

    _TRAY_AVAILABLE = True
except Exception as _exc:  # noqa: BLE001 - pystray/Pillow missing or no backend
    pystray = None  # type: ignore[assignment]
    Image = ImageDraw = None  # type: ignore[assignment]
    _TRAY_AVAILABLE = False
    log.warning("system tray unavailable (%s) - running without an icon", _exc)

_SIZE = 64


@dataclass
class TrayCallbacks:
    toggle_master: Callable[[bool], None]
    force_boost: Callable[[bool], None]
    open_gui: Callable[[], None]
    export_incident: Callable[[], None]
    quit: Callable[[], None]


def _draw_icon(boosting: bool) -> Image.Image:
    """A little goblin-green disc; ember-red ring while boosting."""
    img = Image.new("RGBA", (_SIZE, _SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    body = (86, 158, 63, 255) if not boosting else (110, 190, 70, 255)
    d.ellipse((6, 6, _SIZE - 6, _SIZE - 6), fill=body)
    # ears
    d.polygon([(10, 26), (2, 10), (22, 18)], fill=body)
    d.polygon([(_SIZE - 10, 26), (_SIZE - 2, 10), (_SIZE - 22, 18)], fill=body)
    # eyes
    eye = (20, 20, 20, 255)
    d.ellipse((22, 28, 30, 36), fill=eye)
    d.ellipse((34, 28, 42, 36), fill=eye)
    if boosting:
        d.ellipse((3, 3, _SIZE - 3, _SIZE - 3), outline=(220, 70, 40, 255), width=4)
    return img


class Tray:
    def __init__(self, callbacks: TrayCallbacks) -> None:
        self._cb = callbacks
        self._status_text = "Idle"
        self._master_enabled = True
        self._boosting = False
        self._icon = None
        if _TRAY_AVAILABLE:
            self._icon = pystray.Icon(
                "goblin-mode-pro",
                icon=_draw_icon(False),
                title="Goblin Mode Pro",
                menu=self._build_menu(),
            )

    @property
    def available(self) -> bool:
        return self._icon is not None

    # -- lifecycle ------------------------------------------------------
    def start(self) -> None:
        if self._icon is None:
            return
        try:
            self._icon.run_detached()
            log.info("tray icon started (detached)")
        except Exception:  # noqa: BLE001
            log.exception("tray icon failed to start - continuing headless")

    def stop(self) -> None:
        if self._icon is None:
            return
        try:
            self._icon.stop()
        except Exception:  # noqa: BLE001
            pass

    def notify(self, title: str, message: str = "") -> None:
        if self._icon is None:
            log.info("notify: %s - %s", title, message)
            return
        try:
            self._icon.notify(message or title, title)
        except Exception:  # noqa: BLE001
            log.info("notify: %s - %s", title, message)

    # -- updates from the daemon --------------------------------------
    def update(self, status: dict) -> None:
        games = status.get("active_games") or []
        self._master_enabled = status.get("master_enabled", True)
        self._boosting = bool(games) or status.get("forced_boost", False)
        if not self._master_enabled:
            self._status_text = "Disabled"
        elif games:
            self._status_text = "Boosting: " + ", ".join(games)
        elif status.get("forced_boost"):
            self._status_text = "Forced performance"
        else:
            self._status_text = "Idle"
        if status.get("limited_mode"):
            self._status_text += "  (limited - helper down)"
        if self._icon is None:
            return
        try:
            self._icon.icon = _draw_icon(self._boosting)
            self._icon.title = f"Goblin Mode Pro - {self._status_text}"
            self._icon.menu = self._build_menu()
            self._icon.update_menu()
        except Exception:  # noqa: BLE001
            log.debug("tray update skipped (icon not ready)")

    # -- menu ---------------------------------------------------------
    def _build_menu(self) -> "pystray.Menu":
        M = pystray.MenuItem
        return pystray.Menu(
            M(lambda _: self._status_text, None, enabled=False),
            pystray.Menu.SEPARATOR,
            M(
                "Optimizations enabled",
                lambda: self._cb.toggle_master(not self._master_enabled),
                checked=lambda _: self._master_enabled,
            ),
            M(
                "Force performance now",
                lambda: self._cb.force_boost(not self._boosting),
                checked=lambda _: self._boosting,
            ),
            pystray.Menu.SEPARATOR,
            M("Open Goblin Mode Pro", lambda: self._cb.open_gui()),
            M("Export last incident for AI", lambda: self._cb.export_incident()),
            pystray.Menu.SEPARATOR,
            M("Quit", lambda: self._cb.quit()),
        )
