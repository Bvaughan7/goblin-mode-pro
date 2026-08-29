"""Benchmark comparison and shareable report cards.

Two things, both operating on the plain dicts sessions.SessionTracker.history()
already returns (SessionSummary.as_dict()):

* :func:`diff_sessions` - every numeric metric from two sessions, side by
  side, with the % change. Used for "before/after a Proton bump, a kernel
  change, a tweak" comparisons.
* :func:`render_png` - a small, self-contained report-card image via Cairo
  (no live GTK widget needed, so this also works headlessly). Paired with a
  plain ``json.dumps(session, indent=2)`` for the JSON export - trivial
  enough it doesn't need a function of its own.

Community submissions (per GPU) are a documented PR flow -
see community/benchmarks/README.md - not a live upload endpoint: this
project has no server and isn't standing one up just for this.
"""

from __future__ import annotations

#: (field, human label) for every metric worth comparing, in display order.
_METRICS: tuple[tuple[str, str], ...] = (
    ("fps_avg", "Average FPS"),
    ("fps_median", "Median FPS"),
    ("fps_1low", "1% low FPS"),
    ("fps_01low", "0.1% low FPS"),
    ("fps_p95", "95th %ile FPS"),
    ("fps_min", "Minimum FPS"),
    ("frametime_ms_avg", "Avg frame time (ms)"),
    ("frametime_stutter_pct", "Stutter (%% of frames)"),
    ("cpu_temp_avg", "CPU temp avg (°C)"),
    ("cpu_temp_max", "CPU temp peak (°C)"),
    ("gpu_temp_avg", "GPU temp avg (°C)"),
    ("gpu_temp_max", "GPU temp peak (°C)"),
)

#: metrics where a *lower* number is the improvement (temps, stutter, frame
#: time) - everything else, higher is better.
_LOWER_IS_BETTER = {"frametime_ms_avg", "frametime_stutter_pct",
                    "cpu_temp_avg", "cpu_temp_max", "gpu_temp_avg", "gpu_temp_max"}


def diff_sessions(a: dict, b: dict) -> list[dict]:
    """One row per metric present in *either* session:
    ``{"field", "label", "a", "b", "delta", "delta_pct", "better": "a"|"b"|None}``.
    ``b`` is treated as "after" - a positive delta_pct means b improved on a
    for metrics where higher is better, and vice versa for lower-is-better
    ones, so "better" always points at whichever side actually improved."""
    rows = []
    for field, label in _METRICS:
        va, vb = a.get(field), b.get(field)
        if va is None and vb is None:
            continue
        row = {"field": field, "label": label, "a": va, "b": vb,
              "delta": None, "delta_pct": None, "better": None}
        if va is not None and vb is not None:
            row["delta"] = round(vb - va, 2)
            if va:
                row["delta_pct"] = round((vb - va) / abs(va) * 100, 1)
            improved = (vb < va) if field in _LOWER_IS_BETTER else (vb > va)
            if vb != va:
                row["better"] = "b" if improved else "a"
        rows.append(row)
    return rows


_CARD_W, _CARD_H = 640, 360
_BG = (0.11, 0.11, 0.13)
_FG = (0.94, 0.94, 0.95)
_DIM = (0.62, 0.62, 0.66)
_ACCENT = (0.89, 0.55, 0.20)


def render_png(session: dict, path: str) -> None:
    """A small report-card image for one session/benchmark run - game name,
    date, the headline metrics, peak temps. Raises OSError/cairo.Error on
    failure; callers should catch broadly, this is best-effort UI sugar."""
    import cairo

    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, _CARD_W, _CARD_H)
    cr = cairo.Context(surface)

    cr.set_source_rgb(*_BG)
    cr.paint()

    def text(x, y, s, size=16, rgb=_FG, bold=False):
        cr.select_font_face("sans-serif", cairo.FONT_SLANT_NORMAL,
                            cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL)
        cr.set_font_size(size)
        cr.set_source_rgb(*rgb)
        cr.move_to(x, y)
        cr.show_text(s)

    game = session.get("game") or session.get("exe") or "Unknown game"
    text(24, 40, str(game)[:48], size=22, bold=True)
    text(24, 64, str(session.get("started", ""))[:19], size=13, rgb=_DIM)
    if session.get("benchmark"):
        text(_CARD_W - 130, 40, "BENCHMARK", size=13, rgb=_ACCENT, bold=True)

    rows = [
        ("Average FPS", session.get("fps_avg")),
        ("1% low", session.get("fps_1low")),
        ("0.1% low", session.get("fps_01low")),
        ("95th %ile", session.get("fps_p95")),
        ("Stutter", session.get("frametime_stutter_pct"),  "%"),
        ("CPU peak", session.get("cpu_temp_max"), "°C"),
        ("GPU peak", session.get("gpu_temp_max"), "°C"),
    ]
    y = 110
    for row in rows:
        label, value = row[0], row[1]
        unit = row[2] if len(row) > 2 else " fps"
        text(24, y, label, size=15, rgb=_DIM)
        text(260, y, f"{value:.1f}{unit}" if value is not None else "-", size=15, bold=True)
        y += 30

    text(24, _CARD_H - 20, "Goblin Mode Pro", size=12, rgb=_DIM)

    surface.write_to_png(path)
