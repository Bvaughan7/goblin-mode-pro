#!/usr/bin/env python3
"""GUI smoke test: construct MainWindow (and therefore every page - Dashboard,
Games, System Check, Diagnostics) under a headless X server and confirm none
of it crashes on construction. All bridge calls are stubbed with static data
so this never touches a real daemon or D-Bus session beyond what GTK/Adw
themselves need.

Run via ``xvfb-run`` (and ideally ``dbus-run-session``) - GTK4 needs a real
display connection even off-screen:

    xvfb-run -a dbus-run-session -- python3 tests/gui_smoke.py

Deliberately NOT part of ``unittest discover -s tests`` - the rest of the
suite is GTK-free by design (see CONTRIBUTING.md) so it runs on the bare
system Python; this is the one exception, gated behind its own CI job
(ci.yml's gui-smoke job) since it needs GTK4/libadwaita and a display.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib


#: A machine that can do *everything*, so every capability-gated branch of the
#: UI is constructed: power limits, ryzenadj TDP, both undervolt paths, fan
#: control, a hybrid core layout, gamescope, an NVIDIA GPU. No real machine has
#: all of these at once, which is exactly why the smoke test claims them - a
#: widget that only exists on hardware nobody on CI has is still a widget that
#: can crash on construction.
_EVERYTHING_CAPS = {
    "cpu_vendor": "intel",
    "cpu_model": "Smoke Test CPU",
    "cpufreq_driver": "intel_pstate",
    "governor_control": True,
    "epp_control": True,
    "rapl_control": True,
    "ryzenadj": True,
    "tdp_control": "rapl",
    "gpu_vendors": ["nvidia", "amd"],
    "nvidia_smi": True,
    "gpu_deep_stats": True,
    "gamescope": True,
    "gamemode": True,
    "mangohud": True,
    "compositor": "kwin",
    "distro_id": "cachyos",
    "package_manager": "pacman",
    "core_layout": {"online": list(range(12)),
                    "performance": [0, 1, 2, 3, 4, 5],
                    "groups": [[0, 1, 2, 3, 4, 5], [6, 7, 8, 9, 10, 11]]},
    "kernel_release": "7.2.2-smoke",
    "kernel_flavor": "cachyos",
    "handheld": None,
    "undervolt": "intel-undervolt",
    "amd_undervolt": "ryzenadj",
    "fan_control": True,
    "sched_ext": {"kernel": True, "loader": True, "available": True,
                  "schedulers": ["bpfland", "flash", "lavd", "rusty"]},
    "session_recorder": "gpu-screen-recorder",
    "vkbasalt": True,
}


def _profile(exe: str, name: str, **over) -> dict:
    """A fully-populated profile - every optional block switched on, so every
    nested row and expander is built rather than skipped."""
    p = {
        "exe": exe, "display_name": name, "enabled": True, "match_mode": "exact",
        "auto_created": False, "renice_enabled": True, "nice_value": -5,
        "use_gamemode": True, "core_pin": "performance", "tearing_enabled": True,
        "refresh_rate_hz": 144, "adaptive_sync_enabled": True,
        "vrr_outputs": ["eDP-1"], "governor_boost": True, "focus_mode": True,
        "power_limit_enabled": True, "pl1_w": 45, "pl2_w": 60,
        "battery_pl1_w": 15, "battery_pl2_w": 25,
        "undervolt_reapply": True, "amd_undervolt_reapply": True,
        "fan_spinup_enabled": True, "per_game_mangohud": True,
        "mangohud": {"enabled": True, "fps": True, "frametime": True,
                     "cpu_temp": True, "gpu_temp": True, "vram": True},
        "fps_watchdog": True, "fps_dip_floor": 22.0, "fps_dip_ratio": 0.5,
        "clip_on_incident": True,
        "runner_vars": {"dxvk_async": False, "nvapi": True, "fsync": True},
        "gamescope_enabled": True,
        "gamescope": {"width": 2560, "height": 1440, "refresh": 144,
                      "fullscreen": True, "hdr": False, "upscaler": "fsr"},
        "gpu_tuning": {"threaded_optimizations": True, "shader_cache": True},
        "scx_scheduler": "lavd", "scx_mode": "gaming",
        "steam_app_id": "12345", "notes": "smoke test profile",
    }
    p.update(over)
    return p


class _FakeBridge:
    """Every method MainWindow/_refresh_all/PreflightPage.refresh() call at
    construction time, each returning static data synchronously - no real
    D-Bus round trip, so this can't hang or flake on a slow/missing daemon.

    The payloads are deliberately *populated*, not empty. An empty list builds
    no rows, so an empty-payload smoke test proves only that the page frames
    construct - it never touches the per-game expander, a session row or an
    incident, which is where the widget code actually lives.
    """

    available = False

    def connect(self) -> bool:
        return True

    def on_signal(self, callback) -> None:
        pass

    def run_preflight_async(self, on_done) -> None:
        # Shape matches preflight.run_all() exactly - id/title/why/status/
        # value/detail/sysctl/kernel_param/fix_hint - and covers every status
        # the page branches on, including the fixable and kernel-param paths.
        on_done([
            {"id": "max_map_count", "title": "vm.max_map_count",
             "why": "UE5 / Star Citizen crash guard", "status": "ok",
             "value": "1048576", "detail": "",
             "sysctl": ["vm.max_map_count", "2147483642"],
             "kernel_param": None, "fix_hint": ""},
            {"id": "swappiness", "title": "vm.swappiness",
             "why": "Swapping mid-game causes hitches", "status": "warn",
             "value": "150", "detail": "higher than recommended",
             "sysctl": ["vm.swappiness", "10"],
             "kernel_param": None, "fix_hint": ""},
            {"id": "userns", "title": "user.max_user_namespaces",
             "why": "Anti-cheat and the Steam container need user namespaces",
             "status": "fail", "value": "0", "detail": "Steam Runtime needs this",
             "sysctl": ["user.max_user_namespaces", "126452"],
             "kernel_param": None, "fix_hint": ""},
            {"id": "split_lock", "title": "kernel.split_lock_mitigate",
             "why": "Stalls the whole machine on a split lock", "status": "info",
             "value": "1", "detail": "", "sysctl": None,
             "kernel_param": "split_lock_detect=off",
             "fix_hint": "Add split_lock_detect=off to your kernel cmdline"},
        ], None)

    def get_status_async(self, on_done) -> None:
        on_done({
            "profiles": [
                _profile("Wow.exe", "World of Warcraft"),
                _profile("cyberpunk2077.exe", "Cyberpunk 2077",
                         enabled=False, auto_created=True, core_pin="cache0"),
            ],
            "active_games": ["Wow.exe"],
            "capabilities": _EVERYTHING_CAPS,
            "auto_detect": True,
            "forced_boost": False,
            "diagnostics_enabled": True,
            "master_enabled": True,
            "helper_available": True,
            "limited_mode": False,
            "onboarded": True,
            "poll_interval": 7,
            "governor": "performance",
            "ignored_games": ["steamwebhelper.exe"],
            # fps is fpswatch.stats(), not a number
            "fps": {"fps_avg": 118.5, "fps_min": 74.0, "fps_1low": 80.2,
                    "in_dip": False},
            "gpu": {"name": "NVIDIA GeForce RTX 2060", "temp": 68,
                    "util": 94, "vram_used_mb": 5800, "vram_total_mb": 6144,
                    "power_w": 78.5},
            "latest_sample": {"cpu_temp": 92.0, "gpu_temp": 68.0,
                              "cpu_load": 61.0, "gpu_load": 94.0,
                              "throttling": True, "ts": 1756630000.0},
            # worst is a list of titles (strings), not dicts
            "health": {"score": 8.0, "counts": {"ok": 9, "warn": 2, "fail": 1, "info": 0, "unknown": 0},
                       "worst": ["user.max_user_namespaces"],
                       "checked_at": 1756630000.0},
            "tweaks": {"governor": "performance", "epp_boosted": True,
                       "tearing": True, "adaptive_sync": True,
                       "power_limited": False, "focus_mode": True,
                       "reniced": {"Wow.exe": -5}},
            "detected": [{"exe": "eldenring.exe", "display_name": "Elden Ring",
                          "source": "steam"}],
        }, None)

    def get_metrics_async(self, on_done) -> None:
        on_done([
            {"t": i, "cpu_temp": 70 + i % 20, "gpu_temp": 60 + i % 15,
             "cpu_load": 40 + i % 50, "gpu_load": 80 + i % 20,
             "fps": 120 - (i % 30), "throttling": i % 17 == 0}
            for i in range(60)
        ], None)

    def get_incidents_async(self, on_done) -> None:
        on_done([
            {"kind": "fps_dip", "detail": "FPS fell to 14 for 9 s (baseline 118)",
             "game": "Wow.exe", "game_pid": 4242, "ts": "2026-08-31T09:00:00Z",
             "metrics_window": [], "logs_tail": [],
             "active_tweaks": {"governor": "performance", "epp_boosted": True},
             "gpu_state": {"vram_used_mb": 5800, "vram_total_mb": 6144,
                           "pcie_gen": 3, "pcie_width": 16, "pstate": "P0",
                           "clock_gfx_mhz": 1650, "clock_gfx_max_mhz": 1980}},
            {"kind": "thermal_throttle", "detail": "CPU held at 100 C for 30 s",
             "game": "Wow.exe", "game_pid": 4242, "ts": "2026-08-31T08:30:00Z",
             "metrics_window": [], "logs_tail": [], "active_tweaks": {}},
        ], None)

    def get_sessions_async(self, on_done) -> None:
        on_done([
            {"game": "Wow.exe", "display_name": "World of Warcraft",
             "started": "2026-08-31T08:00:00Z", "duration_s": 3600,
             "avg_fps": 118.2, "median_fps": 120.0, "low_1pct": 74.5,
             "p95_fps": 141.0, "stutter_pct": 1.4, "benchmark": True,
             "regression": -12.5, "tweaks": ["governor", "renice", "tearing"]},
            {"game": "Wow.exe", "display_name": "World of Warcraft",
             "started": "2026-08-30T20:00:00Z", "duration_s": 5400,
             "avg_fps": 135.0, "median_fps": 137.0, "low_1pct": 88.0,
             "p95_fps": 150.0, "stutter_pct": 0.6, "benchmark": False,
             "regression": 0.0, "tweaks": ["governor", "renice"]},
        ], None)

    def get_health_async(self, on_done) -> None:
        on_done({"score": 8.0, "counts": {"ok": 9, "warn": 2, "fail": 1, "info": 0, "unknown": 0},
                       "worst": ["user.max_user_namespaces"],
                       "checked_at": 1756630000.0}, None)

    def get_system_info_async(self, on_done) -> None:
        on_done({"controllers": ["Xbox Wireless Controller"],
                 "gamemode": {"available": True, "active": False},
                 "ananicy": True}, None)

    def get_nvidia_module_state_async(self, on_done) -> None:
        on_done({"present": True, "modeset": "Y",
                 "gsp_firmware_version": "570.86.16"}, None)

    # --- called on interaction, not at construction ---------------------
    # The smoke test only builds the window, so these are never reached by it
    # today - but the GUI calls them, and tests/test_fake_bridges.py requires
    # every bridge call to have a stand-in here. That keeps this fake honest
    # as the real surface grows, and lets the smoke test start driving
    # interactions without first discovering it cannot.
    def set_profile(self, profile) -> bool: return True
    def remove_profile(self, exe: str) -> bool: return True
    def set_auto_detect(self, on: bool) -> bool: return True
    def keep_game(self, exe: str) -> bool: return True
    def ignore_game(self, exe: str) -> bool: return True
    def arm_benchmark(self, exe: str) -> bool: return True
    def set_nvidia_modeset(self, on: bool) -> bool: return True
    def analyze_log(self): return []
    def build_report(self, note: str = "") -> str: return "# report"
    def export_last_incident(self) -> str: return ""

    def apply_preflight_fixes_async(self, on_done) -> None:
        on_done({"applied": ["swappiness"], "failed": []}, None)

    def revert_preflight_fix_async(self, key, on_done) -> None:
        on_done({"ok": True, "message": "reverted"}, None)

    def get_session_history_async(self, exe, on_done) -> None:
        self.get_sessions_async(on_done)

    def export_setup_async(self, on_done) -> None:
        on_done("# setup report", None)

    def clear_shader_cache_async(self, path, on_done) -> None:
        on_done({"ok": True, "message": "cleared 2.0 GB"}, None)

    def build_works_for_me_async(self, exe, note, on_done) -> None:
        on_done({"markdown": "# works for me",
                 "url": "https://github.com/example/issues/new"}, None)

    def get_proton_info_async(self, on_done) -> None:
        on_done({
            "builds": [
                {"name": "GE-Proton11-6-x86_64", "kind": "Proton",
                 "path": "/home/u/.steam/steam/compatibilitytools.d/GE-Proton11-6",
                 "mtime": 1787973076.4},
                {"name": "Proton Experimental", "kind": "Proton",
                 "path": "/home/u/.steam/steam/steamapps/common/Proton - Experimental",
                 "mtime": 1787973000.0},
            ],
            "shader_caches": [
                {"label": "Steam shader cache", "bytes": 587146654,
                 "path": "/home/u/.steam/steam/steamapps/shadercache"},
                {"label": "DXVK state cache", "bytes": 12345678,
                 "path": "/home/u/.cache/dxvk"},
            ],
        }, None)


def _check_profile_editor() -> None:
    """The per-game editor must still *save* what you change, not just build.

    The smoke test above proves the rows construct; this proves the wiring
    behind them. A refactor that leaves the widgets intact but drops the
    callback would pass a construction-only test and silently stop persisting
    every per-game setting in the app.
    """
    from goblinmode.gui.widgets.profile_editor import EditorActions, ProfileEditor

    saved: list[dict] = []
    editor = ProfileEditor(
        "Wow.exe", _profile("Wow.exe", "World of Warcraft"), _EVERYTHING_CAPS,
        EditorActions(save=saved.append, keep=lambda e: None,
                      ignore=lambda e: None, remove=lambda b, e: None,
                      export=lambda e: None, share=lambda e: None,
                      enable_toggled=lambda *a: None),
    )
    row = editor.build()

    switches = []

    def walk(w):
        if isinstance(w, Adw.SwitchRow):
            switches.append(w)
        child = w.get_first_child()
        while child is not None:
            walk(child)
            child = child.get_next_sibling()

    walk(row)
    if not switches:
        raise AssertionError("the profile editor built no Adw.SwitchRow at all")

    sw = switches[0]
    sw.set_active(not sw.get_active())
    if not saved:
        raise AssertionError(
            f"toggling '{sw.get_title()}' saved nothing - the editor's save "
            "callback is not wired to its switches")
    if saved[-1].get("exe") != "Wow.exe":
        raise AssertionError(f"saved the wrong profile: {saved[-1].get('exe')}")

    # a nested-dict patch (mangohud / runner_vars / gamescope) takes a
    # different path through _patch_dict, so exercise that too
    editor._patch_dict("Wow.exe", "runner_vars", "nvapi", False)
    if saved[-1]["runner_vars"]["nvapi"] is not False:
        raise AssertionError("nested-dict patch did not reach the save callback")

    # The expander's own enable switch is handled by the *page*, not the
    # editor, so it takes a different route to the same save - and it is the
    # one control every profile has.
    before = len(saved)
    editor.patch(enabled=False)
    if len(saved) == before or saved[-1].get("enabled") is not False:
        raise AssertionError("the page's enable toggle no longer persists")

    print(f"  Profile editor persists edits ({len(switches)} switch rows, "
          f"{len(saved)} saves, incl. the enable toggle)")


def main() -> int:
    Adw.init()
    app = Adw.Application(application_id="com.goblinmode.Pro.GuiSmokeTest",
                          flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
    result = {"ok": False, "error": None}

    def on_activate(_app) -> None:
        try:
            from goblinmode.gui.window import MainWindow

            win = MainWindow(app, _FakeBridge())
            win.present()

            # Constructing isn't enough: with populated payloads the pages
            # build per-game expanders, session rows and incident rows, and a
            # silently-empty page would still "construct fine". Assert the
            # rows actually exist, so a payload the UI quietly ignores is a
            # failure rather than a pass.
            built = len(getattr(win.games, "_rows", {}) or {})
            if built < 2:
                raise AssertionError(
                    f"Games page built {built} profile rows from 2 profiles - "
                    "the per-game editor was skipped")
            print(f"  Games page built {built} per-game profile rows")

            # The dialogs are only reachable through the primary menu, so
            # nothing else covers them. Both are opened for real.
            win._show_about()
            win._show_shortcuts()
            print("  About and Keyboard Shortcuts dialogs opened")

            _check_profile_editor()

            result["ok"] = True
            print("MainWindow constructed and presented OK "
                  "(Dashboard, Games, System Check, Diagnostics all built)")
        except Exception as exc:  # noqa: BLE001
            result["error"] = exc
            print(f"GUI smoke test FAILED: {exc!r}", file=sys.stderr)
        finally:
            GLib.timeout_add(200, lambda: (app.quit(), False)[1])

    app.connect("activate", on_activate)
    app.run([])
    if result["error"] is not None:
        raise result["error"]
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
