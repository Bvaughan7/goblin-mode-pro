"""Prometheus node_exporter textfile collector.

Writes the same numbers already on the Dashboard as a ``.prom`` file, on a
timer, following the textfile-collector convention (atomic replace so
node_exporter never reads a half-written file): point node_exporter's
``--collector.textfile.directory`` at the directory this writes into, or
symlink the file in. Off by default - set ``Settings.prometheus_textfile``
to a path to enable it.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_HELP = {
    "goblin_mode_pro_master_enabled": ("gauge", "Whether optimizations are enabled (1) or off (0)."),
    "goblin_mode_pro_boosting": ("gauge", "Whether a game is currently boosted (1) or idle (0)."),
    "goblin_mode_pro_forced_boost": ("gauge", "Whether performance mode was forced on manually."),
    "goblin_mode_pro_helper_available": ("gauge", "Whether the privileged helper is reachable."),
    "goblin_mode_pro_limited_mode": ("gauge", "Whether GMP is running in limited mode (helper down)."),
    "goblin_mode_pro_active_games": ("gauge", "Number of games currently detected as running."),
    "goblin_mode_pro_health_score": ("gauge", "Pre-flight system readiness score, 0-10."),
    "goblin_mode_pro_cpu_temp_celsius": ("gauge", "CPU package temperature."),
    "goblin_mode_pro_cpu_load_percent": ("gauge", "Aggregate CPU load."),
    "goblin_mode_pro_package_power_watts": ("gauge", "CPU package power draw."),
    "goblin_mode_pro_gpu_load_percent": ("gauge", "GPU utilisation."),
    "goblin_mode_pro_gpu_temp_celsius": ("gauge", "GPU temperature."),
    "goblin_mode_pro_fps_avg": ("gauge", "Average FPS over the last 60s window (watchdog-enabled games)."),
    "goblin_mode_pro_fps_min": ("gauge", "Minimum FPS over the last 60s window."),
    "goblin_mode_pro_fps_1pct_low": ("gauge", "1% low FPS over the last 60s window."),
}


def _num(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def render(status: dict[str, Any]) -> str:
    """The full ``.prom`` text for one ``daemon.get_status()`` snapshot."""
    games = status.get("active_games") or []
    metrics: dict[str, float | None] = {
        "goblin_mode_pro_master_enabled": float(bool(status.get("master_enabled", True))),
        "goblin_mode_pro_boosting": float(bool(games) or bool(status.get("forced_boost"))),
        "goblin_mode_pro_forced_boost": float(bool(status.get("forced_boost"))),
        "goblin_mode_pro_helper_available": float(bool(status.get("helper_available"))),
        "goblin_mode_pro_limited_mode": float(bool(status.get("limited_mode"))),
        "goblin_mode_pro_active_games": float(len(games)),
        "goblin_mode_pro_health_score": _num((status.get("health") or {}).get("score")),
    }
    sample = status.get("latest_sample") or {}
    metrics["goblin_mode_pro_cpu_temp_celsius"] = _num(sample.get("cpu_temp"))
    metrics["goblin_mode_pro_cpu_load_percent"] = _num(sample.get("cpu_load"))
    metrics["goblin_mode_pro_package_power_watts"] = _num(sample.get("pkg_power_w"))
    metrics["goblin_mode_pro_gpu_load_percent"] = _num(sample.get("gpu_load"))
    metrics["goblin_mode_pro_gpu_temp_celsius"] = _num(sample.get("gpu_temp"))
    fps = status.get("fps") or {}
    metrics["goblin_mode_pro_fps_avg"] = _num(fps.get("fps_avg"))
    metrics["goblin_mode_pro_fps_min"] = _num(fps.get("fps_min"))
    metrics["goblin_mode_pro_fps_1pct_low"] = _num(fps.get("fps_1low"))

    lines = [f"# HELP {name} {_HELP.get(name, ('gauge', ''))[1]}\n"
             f"# TYPE {name} {_HELP.get(name, ('gauge', ''))[0]}\n{name} {v:g}"
             for name, v in metrics.items() if v is not None]
    return "\n".join(lines) + "\n"


def write_textfile(path: str, status: dict[str, Any]) -> None:
    """Atomically (write-then-rename) update the textfile collector output at
    ``path``, so node_exporter never observes a partially written file."""
    try:
        text = render(status)
        p = Path(path)
        tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
        tmp.write_text(text)
        os.replace(tmp, p)
    except OSError as exc:
        log.warning("prometheus textfile export failed: %s", exc)


class Exporter:
    """Rewrites the textfile at most once every ``min_interval`` seconds -
    called from the daemon's existing status-broadcast path, no separate
    timer needed."""

    def __init__(self, path: str, min_interval: float = 5.0) -> None:
        self.path = path
        self.min_interval = min_interval
        self._last: float | None = None  # None = never written yet; time.monotonic()'s
        # reference point isn't guaranteed to be far from 0, so 0.0 isn't a safe sentinel.

    def maybe_write(self, status: dict[str, Any]) -> None:
        now = time.monotonic()
        if self._last is not None and now - self._last < self.min_interval:
            return
        self._last = now
        write_textfile(self.path, status)
