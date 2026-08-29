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
from pathlib import Path
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
_ICON_PNG = Path(__file__).with_name("assets") / "goblin-tray.png"


def _icon_image(boosting: bool) -> "Image.Image":
    """The tray icon: the bundled goblin-mark PNG (matches the app icon), with
    an ember ring + a warm tint while boosting. Falls back to the hand-drawn
    version if the asset can't be loaded."""
    try:
        base = Image.open(_ICON_PNG).convert("RGBA").resize((_SIZE, _SIZE), Image.LANCZOS)
    except Exception as exc:  # noqa: BLE001
        log.debug("tray asset unavailable (%s) - drawing the fallback", exc)
        return _draw_icon(boosting)
    if not boosting:
        return base
    from PIL import ImageEnhance

    base = ImageEnhance.Brightness(base).enhance(1.12)
    ring = Image.new("RGBA", (_SIZE, _SIZE), (0, 0, 0, 0))
    ImageDraw.Draw(ring).ellipse((2, 2, _SIZE - 3, _SIZE - 3),
                                 outline=(226, 88, 42, 255), width=4)
    base.alpha_composite(ring)
    return base


@dataclass
class TrayCallbacks:
    toggle_master: Callable[[bool], None]
    force_boost: Callable[[bool], None]
    open_gui: Callable[[], None]
    export_incident: Callable[[], None]
    quit: Callable[[], None]


def _draw_icon(boosting: bool) -> Image.Image:
    """A little headset goblin, matching data/icons/goblin-mark.svg; the eyes
    glow ember-orange and a ring lights up while boosting."""
    # supersample then downscale for smooth edges at 64 px
    ss = 4
    n = _SIZE * ss
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    def box(cx, cy, rx, ry):
        return (int((cx - rx) * ss), int((cy - ry) * ss),
                int((cx + rx) * ss), int((cy + ry) * ss))

    skin = (95, 156, 55, 255) if not boosting else (120, 190, 70, 255)
    skin_dk = (61, 106, 32, 255)
    ink = (23, 17, 5, 255)
    graphite = (52, 56, 62, 255)
    eye_c = (232, 169, 44, 255) if not boosting else (255, 150, 40, 255)

    # ears
    for pts in ([(9, 30), (3, 9), (26, 24)], [(55, 30), (61, 9), (38, 24)]):
        d.polygon([(x * ss, y * ss) for x, y in pts], fill=skin, outline=ink, width=ss)
    # head
    d.ellipse(box(32, 34, 25, 26), fill=skin, outline=ink, width=ss)
    # headset band
    d.arc(box(32, 33, 26, 24), 200, 340, fill=graphite, width=5 * ss)
    # ear cups
    d.rounded_rectangle(box(8, 33, 7, 10), radius=5 * ss, fill=graphite, outline=ink, width=ss)
    d.rounded_rectangle(box(56, 33, 7, 10), radius=5 * ss, fill=graphite, outline=ink, width=ss)
    # brows
    d.line([(16 * ss, 28 * ss), (28 * ss, 25 * ss)], fill=skin_dk, width=3 * ss)
    d.line([(48 * ss, 28 * ss), (36 * ss, 25 * ss)], fill=skin_dk, width=3 * ss)
    # eyes
    for ex in (24, 41):
        d.ellipse(box(ex, 33, 6, 6), fill=(242, 236, 214, 255), outline=ink, width=ss)
        d.ellipse(box(ex, 33, 3.6, 3.6), fill=eye_c)
        d.ellipse(box(ex, 33, 1.7, 1.7), fill=ink)
    # nose
    d.polygon([(32 * ss, 32 * ss), (35 * ss, 42 * ss), (30 * ss, 42 * ss)], fill=skin_dk)
    # grin
    d.chord(box(32, 40, 12, 11), 15, 165, fill=(58, 31, 28, 255), outline=ink, width=ss)
    d.polygon([(26 * ss, 44 * ss), (29 * ss, 49 * ss), (32 * ss, 44 * ss),
               (35 * ss, 49 * ss), (38 * ss, 44 * ss)], fill=(242, 236, 214, 255))
    # boom mic
    d.arc(box(20, 42, 14, 12), 60, 170, fill=graphite, width=3 * ss)
    d.ellipse(box(20, 49, 3, 3), fill=eye_c, outline=ink, width=ss)

    if boosting:
        d.ellipse(box(32, 32, 30, 30), outline=(226, 88, 42, 255), width=3 * ss)

    return img.resize((_SIZE, _SIZE), Image.LANCZOS)


class Tray:
    def __init__(self, callbacks: TrayCallbacks) -> None:
        self._cb = callbacks
        self._status_text = "Idle"
        self._master_enabled = True
        self._boosting = False
        self._health_score: float | None = None
        self._onboarded = True
        self._icon = None
        if _TRAY_AVAILABLE:
            self._icon = pystray.Icon(
                "goblin-mode-pro",
                icon=_icon_image(False),
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
        self._health_score = (status.get("health") or {}).get("score")
        self._onboarded = status.get("onboarded", True)
        if self._icon is None:
            return
        try:
            self._icon.icon = _icon_image(self._boosting)
            self._icon.title = f"Goblin Mode Pro - {self._status_text}"
            self._icon.menu = self._build_menu()
            self._icon.update_menu()
        except Exception:  # noqa: BLE001
            log.debug("tray update skipped (icon not ready)")

    # -- menu ---------------------------------------------------------
    def _score_text(self) -> str:
        if self._health_score is None:
            return "Readiness: checking…"
        return f"Readiness: {self._health_score:g}/10"

    def _build_menu(self) -> "pystray.Menu":
        M = pystray.MenuItem
        items = [
            M(lambda _: self._status_text, None, enabled=False),
            M(lambda _: self._score_text(), lambda: self._cb.open_gui(), enabled=True),
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
        ]
        if not self._onboarded:
            items.append(M("Finish setup (1 min)", lambda: self._cb.open_gui()))
        items.extend([
            M("Open Goblin Mode Pro", lambda: self._cb.open_gui()),
            M("Export last incident for AI", lambda: self._cb.export_incident()),
            pystray.Menu.SEPARATOR,
            M("Quit", lambda: self._cb.quit()),
        ])
        return pystray.Menu(*items)
