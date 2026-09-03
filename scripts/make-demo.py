#!/usr/bin/env python3
"""Render the animated demo (docs/demo.gif / docs/demo.mp4).

Drives the real GUI with a scripted fake bridge, walks every tab, renders each
frame off-screen with GTK's own renderer (no compositor, no screen recorder),
stamps a caption strip on it, then hands the sequence to ffmpeg.

    python3 docs/make-demo.py          # run from the repo root; needs ffmpeg

Story: Games (set it up per game) -> System Check (is the system ready?) -> a
game launches and the boost engages -> Diagnostics catches a temp climb and a
flagged FPS regression -> the game exits and everything reverts and cools.
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
from gi.repository import Adw, GLib, Gtk
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from goblinmode.gui.window import MainWindow

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs")
W, H = 760, 812
CAP_H = 52
FPS = 12
_FONT_DIRS = ("/usr/share/fonts/TTF", "/usr/share/fonts/truetype/dejavu",
              "/usr/share/fonts/dejavu")


def _font(name: str, size: int):
    for d in _FONT_DIRS:
        p = os.path.join(d, name)
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


FB = _font("DejaVuSans-Bold.ttf", 20)
FR = _font("DejaVuSans.ttf", 18)

CAPTIONS = {
    "games":  ("1", "Set it up once per game"),
    "check":  ("2", "Check the system is game-ready"),
    "launch": ("3", "A game launches…"),
    "boost":  ("3", "…the boost engages — governor → performance"),
    "diag":   ("4", "It watches — and flags a −30% FPS regression"),
    "revert": ("5", "The game exits — everything reverts and cools"),
}

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
    dict(exe="Wow.exe", game="World of Warcraft", started=f"2026-08-2{d}T20:00:00+00:00",
         ended="", duration_s=dur, fps_avg=a, fps_median=a + 2, fps_1low=lo,
         fps_min=lo - 12, samples=30000, cpu_temp_avg=ct, gpu_temp_avg=gt,
         kernel="7.2.0-cachyos", tweaks=tw)
    for d, dur, a, lo, ct, gt, tw in [
        (4, 7200, 143.0, 84.0, 84.0, 69.0, ["governor", "tearing", "renice"]),
        (6, 7200, 140.0, 81.0, 86.0, 70.0, ["governor", "tearing", "renice"]),
        (7, 7200, 141.0, 83.0, 85.0, 69.0, ["governor", "tearing", "renice"]),
        (8, 5400, 118.0, 58.0, 93.0, 74.0, ["governor", "tearing"]),
    ]
]
PREFLIGHT = [
    ("max_map_count", "Large-address limit", "UE5 / Star Citizen crash guard", "ok", "2147483642"),
    ("nofile", "Open-file limit (esync)", "Wine esync handle ceiling", "ok", "524288"),
    ("split_lock", "Split-lock mitigation", "heavy-stutter source in some titles", "ok", "off"),
    ("nvidia_modeset", "nvidia-drm modeset", "Wayland + explicit sync", "ok", "on (driver 610)"),
    ("thp", "Transparent hugepages", "allocation-stall stutter", "warn", "always"),
    ("compaction", "vm.compaction_proactiveness", "frame hitches from memory compaction", "ok", "0"),
    ("swappiness", "vm.swappiness", "paging out game memory", "info", "60"),
    ("fsync", "Kernel fsync support", "WINEFSYNC vs esync fallback", "ok", "7.2"),
    ("userns", "User namespaces", "Steam Runtime container + anti-cheat", "fail", "disabled"),
    ("vulkan_icd", "Vulkan driver (ICD)", "no ICD = no game", "ok", "nvidia_icd"),
    ("anticheat", "Anti-cheat (EAC / BattlEye)", "how anti-cheat games run on Linux", "info", "Proton-native"),
    ("gamemode", "feralinteractive gamemode", "per-game tuning launchers expect", "ok", "active"),
    ("mangohud", "MangoHud", "overlay + frame-rate watchdog", "ok", "installed"),
    ("swap", "Swap / zram", "OOM protection for RAM spikes", "ok", "8 GB"),
]
_FIX = {
    "thp": (None, None, "echo madvise > /sys/kernel/mm/transparent_hugepage/enabled"),
    "swappiness": (["vm.swappiness", "10"], None, ""),
    "userns": (["user.max_user_namespaces", "28633"], None,
               "On Debian/Ubuntu also: sysctl kernel.unprivileged_userns_clone=1"),
}


def _preflight_rows() -> list[dict]:
    out = []
    for cid, title, why, status, value in PREFLIGHT:
        sysctl, kparam, hint = _FIX.get(cid, (None, None, ""))
        out.append({"id": cid, "title": title, "why": why, "status": status,
                    "value": value, "detail": "" if status == "ok" else
                    f"{title}: {value} — see the fix below.", "sysctl": sysctl,
                    "kernel_param": kparam, "fix_hint": hint})
    return out


def _status(boost: bool) -> dict:
    tweaks = {
        "governor": "performance" if boost else None, "epp_boosted": boost,
        "tearing": boost, "adaptive_sync": boost, "focus_mode": boost,
        "power_limited": False, "power_limits_w": [107, 107],
        "reniced": {"Wow.exe": 12843} if boost else {},
        "mangohud_files": ["~/.config/MangoHud/MangoHud.conf"] if boost else [],
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
            "pcie_width": 16, "pcie_width_max": 16,
            "clock_gfx_mhz": 1650 if boost else 300, "clock_gfx_max_mhz": 1710,
        },
        "capabilities": CAPS, "profiles": [PROFILE],
    }


class FakeBridge:
    available = True
    _pf_cb = None
    boost = False          # the demo script drives this; keeps the periodic
                           # window refresh from clobbering the scripted state

    def connect(self): return True
    def on_signal(self, cb): pass
    def get_status(self): return _status(self.boost)
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
    def unignore_game(self, e): return True
    def keep_game(self, e): return True
    def ignore_game(self, e): return True
    def remove_profile(self, e): return True
    def write_wrapper(self): return "~/.local/bin/goblin-run"
    def build_report(self, n=""): return "# report"
    def build_report_async(self, n, cb): cb("# report", None)
    def analyze_log(self): return []
    def analyze_log_async(self, cb): cb([], None)
    def run_preflight(self): return _preflight_rows()

    def run_preflight_async(self, cb):
        self._pf_cb = cb                     # deferred - the demo fires it

    def fire_preflight(self):
        if self._pf_cb:
            self._pf_cb(_preflight_rows(), None)
            self._pf_cb = None

    def apply_preflight_fixes(self): return {"applied": [], "failed": []}
    def apply_preflight_fixes_async(self, cb): cb({"applied": [], "failed": []}, None)
    def export_last_incident(self): return ""

    # Everything else MainWindow and the pages call. Kept in step with the GUI
    # by tests/test_fake_bridges.py, which fails the build when a new bridge
    # call has no stub here - this file silently stopped rendering for three
    # releases because get_health_async was added and nothing noticed.
    def get_health_async(self, cb):
        cb({"score": 9.5, "counts": {"ok": 10, "warn": 2, "fail": 0, "info": 0,
                                     "unknown": 0},
            "worst": [], "checked_at": 0.0}, None)

    def get_system_info_async(self, cb):
        cb({"controllers": ["Xbox Wireless Controller"],
            "gamemode": {"available": True, "active": self.boost},
            "ananicy": False}, None)

    def get_nvidia_module_state_async(self, cb):
        cb({"present": True, "modeset": "Y",
            "gsp_firmware_version": "570.86.16"}, None)

    def get_proton_info_async(self, cb):
        cb({"builds": [{"name": "GE-Proton9-20", "kind": "Proton",
                        "path": "~/.steam/compatibilitytools.d/GE-Proton9-20",
                        "mtime": 0.0}],
            "shader_caches": [{"label": "Steam shader cache", "bytes": 2_147_483_648,
                               "path": "~/.steam/steamapps/shadercache"}]}, None)

    def get_session_history_async(self, exe, cb): cb(SESSIONS, None)
    def export_setup_async(self, cb): cb("# setup", None)
    def clear_shader_cache_async(self, path, cb): cb({"ok": True, "message": ""}, None)
    def revert_preflight_fix_async(self, key, cb): cb({"ok": True, "message": ""}, None)
    def build_works_for_me_async(self, exe, note, cb):
        cb({"markdown": "# works for me", "url": "https://example.invalid"}, None)
    def arm_benchmark(self, exe): return True
    def set_nvidia_modeset(self, on): return True


def _render_png(widget: Gtk.Widget) -> Image.Image:
    ctx = GLib.MainContext.default()
    for _ in range(200):
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
    tmp = os.path.join(_render_png.dir, "_scratch.png")
    widget.get_native().get_renderer().render_texture(node, None).save_to_png(tmp)
    return Image.open(tmp).convert("RGB")


def _stamp(img: Image.Image, key: str) -> Image.Image:
    step, text = CAPTIONS[key]
    canvas = Image.new("RGB", (img.width, img.height + CAP_H), (24, 24, 27))
    canvas.paste(img, (0, 0))
    d = ImageDraw.Draw(canvas)
    y = img.height + CAP_H // 2
    d.ellipse((20, y - 13, 46, y + 13), fill=(78, 106, 36))
    d.text((33, y), step, font=FB, fill=(240, 240, 235), anchor="mm")
    d.text((60, y), text, font=FR, fill=(210, 210, 214), anchor="lm")
    return canvas


def _sample(frac: float, dip: bool = False) -> dict:
    j = math.sin(frac * 37) * 2
    return {
        "cpu_temp": round(56 + 30 * frac + j, 1),
        "gpu_temp": round(50 + 24 * frac + j * 0.6, 1),
        "cpu_load": round(min(99, 34 + 55 * frac + j * 3), 1),
        "gpu_load": round((22 if dip else 78 + 14 * frac) + j, 1),
        "pkg_power_w": round(26 + 28 * frac + j, 1),
        "pl1_w": 107, "pl2_w": 107, "gpu_throttle_reasons": "0x0", "cpu_throttled": False,
    }


def main() -> int:
    if not shutil.which("ffmpeg"):
        print("ffmpeg not found on PATH")
        return 1
    tmp = tempfile.mkdtemp(prefix="gmp-demo-")
    _render_png.dir = tmp
    app = Adw.Application(application_id="com.goblinmode.Pro.Demo", flags=0)
    state = {"n": 0}

    def frame(widget, key):
        _stamp(_render_png(widget), key).save(os.path.join(tmp, f"f{state['n']:04d}.png"))
        state["n"] += 1

    def expand_game(w):
        for row in w.games._rows:
            if isinstance(row, Adw.ExpanderRow):
                row.set_expanded(True)
                return

    def graph_curve(diag, count, dip_at):
        base = time.monotonic()
        span = 90.0
        diag.graph._points.clear()
        for i in range(count):
            f = i / max(1, count - 1)
            d = dip_at is not None and abs(i - dip_at) < 3
            s = _sample(f, dip=d)
            diag.graph._points.append({
                "t": base - span * (1 - f), "cpu_temp": s["cpu_temp"],
                "gpu_temp": s["gpu_temp"], "load": s["cpu_load"],
                "throttled": dip_at is not None and i in (count - 8, count - 7)})
        diag.graph.queue_draw()
        diag.fps_graph._points.clear()
        for i in range(count):
            f = i / max(1, count - 1)
            fps = 145 - 6 * math.sin(f * 20)
            if dip_at is not None and dip_at - 4 < i < dip_at + 8:
                fps = 55 + 4 * math.sin(i)
            diag.fps_graph._points.append((base - 90 * (1 - f), round(fps, 1)))
        diag.fps_graph.set_threshold(30)
        diag.fps_graph.queue_draw()

    def on_activate(a):
        w = MainWindow(a, FakeBridge())
        w.set_default_size(W, H)
        w.present()
        a.hold()

        def run():
            b = w.bridge

            # 1 - Games: configure once per game
            w.set_visible_page(w.games)
            w.games.update_status(_status(False))
            expand_game(w)
            for _ in range(22):
                frame(w, "games")

            # 2 - System Check: is the system ready?
            w.set_visible_page(w.preflight)
            for _ in range(5):
                frame(w, "check")
            b.fire_preflight()
            for _ in range(20):
                frame(w, "check")

            # 3 - Dashboard: a game launches, the boost engages
            w.set_visible_page(w.dashboard)
            w.dashboard.update_status(_status(False))
            for _ in range(8):
                frame(w, "launch")
            b.boost = True
            w.dashboard.update_status(_status(True))
            for i in range(26):
                w.dashboard.update_sample(_sample(i / 25))
                frame(w, "boost")

            # 4 - Diagnostics: the graph + a flagged regression
            w.set_visible_page(w.diagnostics)
            w.diagnostics.update_status(_status(True))
            w.diagnostics.load_sessions(SESSIONS)
            for i in range(38):
                graph_curve(w.diagnostics, 12 + i, 30 if i > 20 else None)
                frame(w, "diag")
            for _ in range(5):
                frame(w, "diag")

            # 5 - Dashboard: the game exits, everything reverts and cools
            b.boost = False
            w.set_visible_page(w.dashboard)
            w.dashboard.update_status(_status(False))
            for i in range(8):
                w.dashboard.update_sample(_sample(max(0.02, 0.85 - i * 0.12)))
                frame(w, "revert")
            for _ in range(12):
                frame(w, "revert")

            a.release()
            a.quit()

        GLib.timeout_add(2500, run)

    app.connect("activate", on_activate)
    app.run([])

    print(f"rendered {state['n']} frames -> encoding")
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
        print(f"  {f}: {os.path.getsize(os.path.join(OUT, f)) / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
