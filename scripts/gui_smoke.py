import sys
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

Adw.init()


class FakeBridge:
    available = True
    def connect(self): return True
    def on_signal(self, cb): pass
    def get_status(self):
        return {
            "master_enabled": True, "active_games": [], "forced_boost": False,
            "limited_mode": True, "helper_available": False, "governor": "powersave",
            "poll_interval": 7, "diagnostics_enabled": True, "tweaks": {},
            "auto_detect": True, "ignored_games": [], "gpu": {}, "fps": {},
            "latest_sample": {"cpu_temp": 61, "cpu_load": 12, "gpu_load": 3,
                              "gpu_temp": 40, "pl1_w": 45, "pl2_w": 107, "pkg_power_w": 8,
                              "gpu_throttle_reasons": "Not Active"},
            "profiles": [
                {"exe": "Wow.exe", "display_name": "WoW", "enabled": True,
                 "match_mode": "exact", "renice_enabled": True, "nice_value": -5,
                 "tearing_enabled": True, "governor_boost": True, "auto_created": False,
                 "fps_watchdog": False, "fps_dip_floor": 22,
                 "per_game_mangohud": False,
                 "mangohud": {"enabled": False, "fps": True, "cpu_temp": True,
                              "gpu_temp": True, "ram": False, "frame_timing": False},
                 "runner_vars": {"nvapi": True, "fsync": True, "no_esync": False,
                                 "dxvk_async": True}},
            ],
        }
    def get_metrics(self): return [{"cpu_temp": 60, "cpu_load": 10, "gpu_temp": 40}]
    def get_incidents(self):
        return [{"kind": "thermal_throttle", "detail": "hot", "ts": "2026-08-27T00:00:00Z"}]
    def set_profile(self, p): return True
    def remove_profile(self, e): return True
    def set_master_enabled(self, v): return True
    def set_auto_detect(self, v): return True
    def force_boost(self, v): return True
    def keep_game(self, e): return True
    def ignore_game(self, e): return True
    def build_report(self, note=""): return "## report\n"
    def analyze_log(self): return []
    def apply_preflight_fixes(self): return {"applied": [], "failed": []}
    def run_preflight(self):
        return [
            {"id": "max_map_count", "title": "vm.max_map_count", "why": "UE5 crash guard",
             "status": "fail", "value": "65530", "detail": "too low",
             "sysctl": ["vm.max_map_count", "2147483642"], "kernel_param": None, "fix_hint": ""},
            {"id": "thp", "title": "Transparent hugepages", "why": "stutter", "status": "ok",
             "value": "madvise", "detail": "", "sysctl": None, "kernel_param": None, "fix_hint": ""},
        ]
    def export_last_incident(self): return "payload"


from goblinmode.gui.page_dashboard import DashboardPage
from goblinmode.gui.page_games import GamesPage
from goblinmode.gui.page_diagnostics import DiagnosticsPage
from goblinmode.gui.page_preflight import PreflightPage
from goblinmode.gui.window import MainWindow

b = FakeBridge()

d = DashboardPage(b); d.update_status(b.get_status())
print("DashboardPage OK")

g = GamesPage(b); g.load_profiles(b.get_status()["profiles"])
print("GamesPage OK, rows:", len(g._rows))

class W:
    def toast(self, t): pass
dg = DiagnosticsPage(b, W()); dg.load_history(b.get_metrics(), b.get_incidents())
dg.push_sample({"cpu_temp": 70, "cpu_load": 50, "gpu_temp": 55})
dg.add_incident({"kind": "gpu_fault", "detail": "VKD3D lost", "ts": "t"})
print("DiagnosticsPage OK")

pf = PreflightPage(b, W()); pf.refresh()
print("PreflightPage OK, check rows:", len(pf._rows))

app = Adw.Application(application_id="com.goblinmode.Pro.Smoke")
def on_activate(a):
    win = MainWindow(a, b)
    print("MainWindow OK:", win.get_title())
    a.quit()
app.connect("activate", on_activate)
app.run([])
print("ALL GUI SMOKE PASSED")
