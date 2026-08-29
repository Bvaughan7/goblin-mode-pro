"""A small dual-axis Temp-vs-Load correlation plot (Cairo on a DrawingArea).

Left axis: temperature (°C, 30-100). Right axis: load (%, 0-100).
Three series: CPU temp, CPU load, GPU temp.
"""

from __future__ import annotations

import time
from collections import deque

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from goblinmode.i18n import _

_WINDOW_S = 300
_TEMP_MIN, _TEMP_MAX = 30.0, 100.0


class CorrelationGraph(Gtk.DrawingArea):
    def __init__(self) -> None:
        super().__init__()
        self.set_content_height(220)
        self.set_hexpand(True)
        self._points: deque[dict] = deque(maxlen=1200)
        self.set_draw_func(self._draw)

    def push(self, sample: dict) -> None:
        self._points.append(
            {
                "t": time.monotonic(),
                "cpu_temp": sample.get("cpu_temp"),
                "gpu_temp": sample.get("gpu_temp"),
                "load": sample.get("cpu_load"),
                "throttled": sample.get("cpu_throttled"),
            }
        )
        self.queue_draw()

    def load_history(self, samples: list[dict]) -> None:
        self._points.clear()
        base = time.monotonic()
        n = len(samples)
        for i, s in enumerate(samples):
            self._points.append(
                {
                    "t": base - (n - i),
                    "cpu_temp": s.get("cpu_temp"),
                    "gpu_temp": s.get("gpu_temp"),
                    "load": s.get("cpu_load"),
                    "throttled": s.get("cpu_throttled"),
                }
            )
        self.queue_draw()

    # -- drawing --------------------------------------------------
    def _draw(self, _area, cr, width: int, height: int) -> None:
        style = self.get_style_context()
        ok, fg = style.lookup_color("theme_fg_color")
        if not ok:
            fg = None

        pad_l, pad_r, pad_t, pad_b = 38, 38, 12, 22
        plot_w = max(1, width - pad_l - pad_r)
        plot_h = max(1, height - pad_t - pad_b)

        # frame
        cr.set_line_width(1)
        cr.set_source_rgba(0.5, 0.5, 0.5, 0.4)
        cr.rectangle(pad_l, pad_t, plot_w, plot_h)
        cr.stroke()

        # gridlines + axis labels
        cr.set_source_rgba(0.5, 0.5, 0.5, 0.25)
        for frac in (0.25, 0.5, 0.75):
            y = pad_t + plot_h * frac
            cr.move_to(pad_l, y)
            cr.line_to(pad_l + plot_w, y)
            cr.stroke()

        if not self._points:
            cr.set_source_rgba(0.5, 0.5, 0.5, 0.8)
            cr.move_to(pad_l + 8, pad_t + plot_h / 2)
            cr.show_text(_("waiting for samples…"))
            return

        now = time.monotonic()
        t0 = now - _WINDOW_S

        def x_of(t: float) -> float:
            return pad_l + plot_w * max(0.0, min(1.0, (t - t0) / _WINDOW_S))

        def y_temp(v: float) -> float:
            frac = (v - _TEMP_MIN) / (_TEMP_MAX - _TEMP_MIN)
            return pad_t + plot_h * (1 - max(0.0, min(1.0, frac)))

        def y_load(v: float) -> float:
            return pad_t + plot_h * (1 - max(0.0, min(1.0, v / 100.0)))

        def series(key: str, y_fn, rgba) -> None:
            cr.set_source_rgba(*rgba)
            cr.set_line_width(1.6)
            started = False
            for p in self._points:
                v = p.get(key)
                if v is None:
                    started = False
                    continue
                x, y = x_of(p["t"]), y_fn(v)
                if not started:
                    cr.move_to(x, y)
                    started = True
                else:
                    cr.line_to(x, y)
            cr.stroke()

        series("cpu_temp", y_temp, (0.90, 0.30, 0.24, 0.95))   # red
        series("gpu_temp", y_temp, (0.95, 0.60, 0.10, 0.95))   # orange
        series("load", y_load, (0.30, 0.55, 0.90, 0.95))       # blue

        # throttle markers
        cr.set_source_rgba(0.90, 0.10, 0.10, 0.9)
        for p in self._points:
            if p.get("throttled"):
                x = x_of(p["t"])
                cr.move_to(x, pad_t)
                cr.line_to(x, pad_t + plot_h)
                cr.set_line_width(1)
                cr.stroke()

        # axis captions
        cr.set_source_rgba(0.6, 0.6, 0.6, 0.9)
        cr.move_to(4, pad_t + 8)
        cr.show_text("°C")
        cr.move_to(width - pad_r + 6, pad_t + 8)
        cr.show_text("%")


class FpsGraph(Gtk.DrawingArea):
    """Single-series FPS line with a dashed dip-threshold line. Its own y-axis
    (fps) - kept separate from the temp/load chart on purpose (one axis each)."""

    def __init__(self) -> None:
        super().__init__()
        self.set_content_height(130)
        self.set_hexpand(True)
        self._points: deque[tuple[float, float]] = deque(maxlen=1600)
        self._threshold: float | None = 22.0
        self.set_draw_func(self._draw)

    def set_threshold(self, value: float | None) -> None:
        self._threshold = value
        self.queue_draw()

    def push(self, fps: float) -> None:
        self._points.append((time.monotonic(), float(fps)))
        self.queue_draw()

    def load_history(self, trace: list[dict], threshold: float | None = None) -> None:
        self._points.clear()
        base = time.monotonic()
        n = len(trace)
        for i, p in enumerate(trace):
            self._points.append((base - (n - i), float(p.get("fps", 0.0))))
        if threshold:
            self._threshold = threshold
        self.queue_draw()

    def _draw(self, _area, cr, width: int, height: int) -> None:
        pad_l, pad_r, pad_t, pad_b = 36, 12, 12, 20
        pw = max(1, width - pad_l - pad_r)
        ph = max(1, height - pad_t - pad_b)

        cr.set_line_width(1)
        cr.set_source_rgba(0.5, 0.5, 0.5, 0.4)
        cr.rectangle(pad_l, pad_t, pw, ph)
        cr.stroke()

        if not self._points:
            cr.set_source_rgba(0.5, 0.5, 0.5, 0.8)
            cr.move_to(pad_l + 8, pad_t + ph / 2)
            cr.show_text(_("no frame-rate log yet — enable the watchdog for a game"))
            return

        vals = [v for _, v in self._points]
        vmax = max(90.0, max(vals) * 1.1)
        now = time.monotonic()
        t0 = now - _WINDOW_S

        def X(t):
            return pad_l + pw * max(0.0, min(1.0, (t - t0) / _WINDOW_S))

        def Y(v):
            return pad_t + ph * (1 - max(0.0, min(1.0, v / vmax)))

        for frac in (0.25, 0.5, 0.75, 1.0):
            v = vmax * frac
            y = Y(v)
            cr.set_source_rgba(0.5, 0.5, 0.5, 0.2)
            cr.move_to(pad_l, y)
            cr.line_to(pad_l + pw, y)
            cr.stroke()
            cr.set_source_rgba(0.6, 0.6, 0.6, 0.9)
            cr.move_to(4, y + 4)
            cr.show_text(str(int(v)))

        if self._threshold:
            y = Y(self._threshold)
            cr.set_source_rgba(0.90, 0.20, 0.18, 0.85)
            cr.set_dash([4.0, 3.0], 0)
            cr.set_line_width(1.4)
            cr.move_to(pad_l, y)
            cr.line_to(pad_l + pw, y)
            cr.stroke()
            cr.set_dash([], 0)

        cr.set_source_rgba(0.30, 0.55, 0.90, 0.95)
        cr.set_line_width(1.6)
        started = False
        for t, v in self._points:
            x, y = X(t), Y(v)
            if not started:
                cr.move_to(x, y)
                started = True
            else:
                cr.line_to(x, y)
        cr.stroke()
