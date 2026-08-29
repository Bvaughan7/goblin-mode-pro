#!/usr/bin/env python3
"""Render the animated demo (docs/demo.gif / docs/demo.mp4).

Drives the real GUI with a scripted fake bridge, renders each frame off-screen
with GTK's own renderer (no compositor, no screen recorder), then hands the PNG
sequence to ffmpeg.

    python3 docs/make-demo.py          # needs ffmpeg on PATH

Narrative: idle -> a game launches and the boost engages -> Diagnostics shows the
temp/load graph and a flagged FPS regression -> the game exits and everything
reverts.
"""

from __future__ import annotations

import math
import os
import shutil
import subprocess
import sys
import tempfile
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from goblinmode.gui.window import MainWindow  # noqa: E402

OUT = os.path.dirname(__file__)
W, H = 760, 820
FPS = 12

CAPS = {
    "cpu_model": "Intel Core i7-10750H", "cpufreq_driver": "intel_pstate",
    "governor_control": True, "epp_control": True, "rapl_control": True,
    "ryzenadj": False, "gpu_vendors": ["intel", "nvidia"], "nvidia_smi": True,
    "gpu_deep_stats": True, "gamescope": True, "gamemode": True, "mangohud": True,
    "compositor": "kwin-wayland", "distro_id": "cachyos", "package_manager": "pacman",
    "tdp_control": "rapl", "core_layout": {"online": list(range(12))},
}
PROFILE = {
    "exe": "Wow.exe", "display_name": "World of Warcraft", "enabled": True,
    "match_mode": "exact", "renice_enabled": True, "nice_value": -5,
    "governor_boost": True, "tearing_enabled": True, "adaptive_sync_enabled": True,
    "focus_mode": True, "core_pin": "off", "power_limit_enabled": False,
    "pl1_w": 0, "pl2_w": 0, "per_game_mangohud": False, "fps_watchdog": True,
    "fps_dip_floor": 30, "mangohud": {"enabled": False, "fps": True, "cpu_temp": True,
    "gpu_temp": True}, "runner_vars": {"nvapi": True, "fsync": True, "no_esync": False,
    "dxvk_async": True}, "gamescope_enabled": False, "gamescope": {},
}
SESSIONS = [
    {"exe": "Wow.exe", "game": "World of Warcraft", "started": "2026-08-24T20:00:00+00:00",
     "ended": "", "duration_s": 7200, "fps_avg": 143.0, "fps_median": 145.0,
     "fps_1low": 84.0, "fps_min": 46.0, "samples": 36000, "cpu_temp_avg": 84.0,
     "gpu_temp_avg": 69.0, "kernel": "7.2.0-cachyos", "tweaks": ["governor", "tearing", "renice"]},
    {"exe": "Wow.exe", "game": "World of Warcraft", "started": "2026-08-26T20:00:00+00:00",
     "ended": "", "duration_s": 7200, "fps_avg": 140.0, "fps_median": 142.0,
     "fps_1low": 81.0, "fps_min": 44.0, "samples": 36000, "cpu_temp_avg": 86.0,
     "gpu_temp_avg": 70.0, "kernel": "7.2.0-cachyos", "tweaks": ["governor", "tearing", "renice"]},
    {"exe": "Wow.exe", "game": "World of Warcraft", "started": "2026-08-27T20:00:00+00:00",
     "ended": "", "duration_s": 7200, "fps_avg": 141.0, "fps_median": 143.0,
     "fps_1low": 83.0, "fps_min": 45.0, "samples": 36000, "cpu_temp_avg": 85.0,
     "gpu_temp_avg": 69.0, "kernel": "7.2.0-cachyos", "tweaks": ["governor", "tearing", "renice"]},
    {"exe": "Wow.exe", "game": "World of Warcraft", "started": "2026-08-28T19:05:00+00:00",
     "ended": "", "duration_s": 5400, "fps_avg": 118.0, "fps_median": 121.0,
     "fps_1low": 58.0, "fps_min": 33.0, "samples": 27000, "cpu_temp_avg": 93.0,
     "gpu_temp_avg": 74.0, "kernel": "7.2.0-cachyos", "tweaks": ["governor", "tearing"]},
]


def _status(boost: bool) -> dict:
    tweaks = {
        "governor": "performance" if boost else None,
        "epp_boosted": boost, "tearing": boost, "adaptive_sync": boost,
        "focus_mode": boost, "power_limited": False, "power_limits_w": [107, 107],
        "reniced": {"Wow.exe": 12843} if boost else {},
        "mangohud_files": ["/home/you/.config/MangoHud/MangoHud.conf"] if boost else [],
        "helper_available": True, "limited_mode": False,
    }
    return {
        "master_enabled": True, "active_games": ["Wow.exe"] if boost else [],
        "forced_boost": False, "limited_mode": False, "helper_available": True,
        "governor": "performance" if boost else "powersave", "poll_interval": 7,
        "diagnostics_enabled": True, "auto_detect": True, "ignored_games": [],
        "tweaks": tweaks, "latest_sample": None, "fps": {}, "gpu": {
            "vram_used_mb": 5100 if boost else 720, "vram_total_mb": 6144,
            "vram_free_mb": 1044 if boost else 5424, "pcie_gen": 3, "pcie_gen_max": 3,
            "pcie_width": 16, "pcie_width_max": 16, "clock_gfx_mhz": 1650 if boost else 300,
            "clock_gfx_max_mhz": 1710,
        },
        "capabilities": CAPS, "profiles": [PROFILE],
    }


class FakeBridge:
    available = True

    def connect(self): return True
    def on_signal(self, cb): pass
    def get_status(self): return _status(False)
    def get_metrics(self): return []
    def get_incidents(self): return []
    def get_sessions(self): return SESSIONS
    def get_session_history(self, e): return SESSIONS
    def get_status_async(self, cb): cb(self.get_status(), None)
    def get_metrics_async(self, cb): cb([], None)
    def get_incidents_async(self, cb): cb([], None)
    def get_sessions_async(self, cb): cb(SESSIONS, None)
    def set_profile(self, p): return True
    def set_master_enabled(self, v): return True
    def set_auto_detect(self, v): return True
    def force_boost(self, v): return True
    def keep_game(self, e): return True
    def ignore_game(self, e): return True
    def remove_profile(self, e): return True
    def write_wrapper(self): return "~/.local/bin/goblin-run"
    def build_report(self, n=""): return "# report"
    def build_report_async(self, n, cb): cb("# report", None)
    def analyze_log(self): return []
    def analyze_log_async(self, cb): cb([], None)
    def run_preflight(self): return []
    def run_preflight_async(self, cb): cb([], None)
    def apply_preflight_fixes(self): return {"applied": [], "failed": []}
    def apply_preflight_fixes_async(self, cb): cb({"applied": [], "failed": []}, None)
    def export_last_incident(self): return ""


def capture(widget: Gtk.Widget, path: str) -> None:
    ctx = GLib.MainContext.default()
    for _ in range(200):                       # let pending draws flush
        if not ctx.pending():
            break
        ctx.iteration(False)
    w, h = widget.get_width(), widget.get_height()
    paintable = Gtk.WidgetPaintable.new(widget)
    node = None
    for _ in range(60):
        snap = Gtk.Snapshot.new()
        paintable.snapshot(snap, w, h)
        node = snap.to_node()
        if node is not None:
            break
        ctx.iteration(True)
    if node is None:
        return
    tex = widget.get_native().get_renderer().render_texture(node, None)
    tex.save_to_png(path)


def _sample(frac: float, dip: bool = False) -> dict:
    """A plausible in-game sample; frac 0..1 ramps the load in."""
    j = math.sin(frac * 37) * 2
    return {
        "cpu_temp": round(56 + 30 * frac + j, 1),
        "gpu_temp": round(50 + 24 * frac + j * 0.6, 1),
        "cpu_load": round(min(99, 34 + 55 * frac + j * 3), 1),
        "gpu_load": round((22 if dip else 78 + 14 * frac) + j, 1),
        "pkg_power_w": round(26 + 28 * frac + j, 1),
        "pl1_w": 107, "pl2_w": 107, "gpu_throttle_reasons": "0x0",
        "cpu_throttled": False,
    }


def main() -> int:
    if not shutil.which("ffmpeg"):
        print("ffmpeg not found on PATH")
        return 1
    tmp = tempfile.mkdtemp(prefix="gmp-demo-")
    app = Adw.Application(application_id="com.goblinmode.Pro.Demo", flags=0)
    state = {"n": 0}

    def frame(widget):
        capture(widget, os.path.join(tmp, f"f{state['n']:04d}.png"))
        state["n"] += 1

    def graph_curve(diag, count: int, dip_at: int | None):
        """Populate the temp/load graph with `count` spread points."""
        pts = []
        base = time.monotonic()
        span = 90.0
        for i in range(count):
            f = i / max(1, count - 1)
            d = dip_at is not None and abs(i - dip_at) < 3
            s = _sample(f, dip=d)
            pts.append({"t": base - span * (1 - f), "cpu_temp": s["cpu_temp"],
                        "gpu_temp": s["gpu_temp"], "load": s["cpu_load"],
                        "throttled": i in (count - 8, count - 7) if dip_at else False})
        diag.graph._points.clear()
        diag.graph._points.extend(pts)
        diag.graph.queue_draw()
        diag.fps_graph._points.clear()
        b = time.monotonic()
        for i in range(count):
            f = i / max(1, count - 1)
            fps = 145 - 6 * math.sin(f * 20)
            if dip_at is not None and dip_at - 4 < i < dip_at + 8:
                fps = 55 + 4 * math.sin(i)
            diag.fps_graph._points.append((b - 90 * (1 - f), round(fps, 1)))
        diag.fps_graph.set_threshold(30)
        diag.fps_graph.queue_draw()

    def on_activate(a):
        w = MainWindow(a, FakeBridge())
        w.set_default_size(W, H)
        w.present()
        a.hold()

        def run():
            # --- A: idle dashboard --------------------------------------
            w.set_visible_page(w.dashboard)
            w.dashboard.update_status(_status(False))
            for _ in range(8):
                frame(w)

            # --- B: a game launches, the boost engages -----------------
            w.dashboard.update_status(_status(True))
            for i in range(30):
                w.dashboard.update_sample(_sample(i / 29))
                frame(w)

            # --- C: Diagnostics - the graph and a flagged regression ---
            w.set_visible_page(w.diagnostics)
            w.diagnostics.update_status(_status(True))
            w.diagnostics.load_sessions(SESSIONS)
            for i in range(42):
                graph_curve(w.diagnostics, 12 + i, dip_at=(30 if i > 22 else None))
                frame(w)
            for _ in range(5):
                frame(w)

            # --- D: the game exits, everything reverts and cools -------
            w.set_visible_page(w.dashboard)
            w.dashboard.update_status(_status(False))
            for i in range(8):                    # temps ramp back down
                w.dashboard.update_sample(_sample(max(0.02, 0.9 - i * 0.13)))
                frame(w)
            for _ in range(12):
                frame(w)

            a.release()
            a.quit()

        GLib.timeout_add(2500, run)

    app.connect("activate", on_activate)
    app.run([])

    n = state["n"]
    print(f"rendered {n} frames -> encoding")
    src = os.path.join(tmp, "f%04d.png")
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", src,
        "-vf", ("fps=10,scale=600:-1:flags=lanczos,split[a][b];"
                "[a]palettegen=max_colors=96:stats_mode=diff[p];"
                "[b][p]paletteuse=dither=bayer:bayer_scale=5"),
        "-loop", "0", os.path.join(OUT, "demo.gif"),
    ], check=True)
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", src,
        "-vf", "scale=760:-2,format=yuv420p", "-c:v", "libx264", "-crf", "23",
        "-movflags", "+faststart", os.path.join(OUT, "demo.mp4"),
    ], check=True)
    shutil.rmtree(tmp, ignore_errors=True)
    for f in ("demo.gif", "demo.mp4"):
        p = os.path.join(OUT, f)
        print(f"  {f}: {os.path.getsize(p) / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
