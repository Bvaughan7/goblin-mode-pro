"""The Observer daemon - headless, runs under ``goblin-mode-pro.service``.

Wires together the Observer, the Performance Payload, the Diagnostic Engine, the
log watcher, the tray icon and the session-bus bridge on a single GLib main
loop. Near-zero footprint while idle: the diagnostics sampler only ticks while a
game is running.
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import os
import signal
import sys
import time
from typing import Any

import gi

gi.require_version("Gio", "2.0")
from gi.repository import GLib  # noqa: E402

from goblinmode import config, gpu, runner
from goblinmode.diagnostics import DiagnosticEngine
from goblinmode.fpswatch import FpsEvent, FpsWatcher
from goblinmode.incidents import Incident, IncidentLog, build_llm_payload, copy_to_clipboard
from goblinmode.ipc.daemon_bridge import DaemonBridge
from goblinmode.ipc.helper_client import HelperClient
from goblinmode.logwatch import LogWatcher
from goblinmode.observer import GameEvent, Observer
from goblinmode.payload import PerformancePayload
from goblinmode.paths import ensure_user_dirs

log = logging.getLogger("goblinmode.daemon")


class Daemon:
    def __init__(self) -> None:
        self.settings = config.load()
        self.helper = HelperClient()
        self.incidents = IncidentLog()
        self.payload = PerformancePayload(
            self.helper, on_incident=self._on_payload_incident
        )
        self.diag = DiagnosticEngine(self.settings.diagnostics_sample_interval)
        self.logwatch = LogWatcher()
        self.fpswatch = FpsWatcher()
        self.observer = Observer(self.settings, self._on_game_event)
        self.bridge = DaemonBridge(self)

        from goblinmode.tray import Tray, TrayCallbacks

        self.tray = Tray(
            TrayCallbacks(
                toggle_master=lambda on: self._on_main_thread(self.set_master_enabled, on),
                force_boost=lambda on: self._on_main_thread(self.force_boost, on),
                open_gui=lambda: self._on_main_thread(self._launch_gui),
                export_incident=lambda: self._on_main_thread(self._export_and_notify),
                quit=lambda: self._on_main_thread(self.shutdown),
            )
        )

        self._loop = GLib.MainLoop()
        self._forced_boost = False
        self._active_pids: dict[str, int] = {}
        self._diag_source_id: int | None = None
        self._poll_source_id: int | None = None
        self._gpu_state: dict = {}          # refreshed on the diag tick
        self._gpu_state_at = 0.0
        self._fps_dip_seen = False          # any FPS dip this session?
        self._dirty_profiles: set[str] = set()
        self._save_source_id: int | None = None

    # -- lifecycle ------------------------------------------------------
    def run(self) -> int:
        ensure_user_dirs()
        try:
            runner.write_wrapper()
        except OSError as exc:
            log.warning("could not (re)write launch wrapper: %s", exc)

        self.bridge.publish()
        self.tray.start()

        self._poll_source_id = GLib.timeout_add_seconds(
            self.settings.poll_interval, self._poll_tick
        )
        for sig in (signal.SIGINT, signal.SIGTERM):
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, self._on_signal)

        log.info("goblin-mode-pro daemon started (poll=%ds)", self.settings.poll_interval)
        self._broadcast_status()
        self._loop.run()
        return 0

    def _on_signal(self) -> bool:
        log.info("signal received - shutting down")
        self.shutdown()
        return GLib.SOURCE_REMOVE

    def shutdown(self) -> None:
        if self._save_source_id is not None:
            GLib.source_remove(self._save_source_id)
            self._save_source_id = None
            try:
                self._flush_profiles()
            except Exception:  # noqa: BLE001
                log.exception("error flushing profiles on shutdown")
        try:
            self.payload.revert_all()
        except Exception:  # noqa: BLE001
            log.exception("error during revert on shutdown")
        self.tray.stop()
        if self._loop.is_running():
            self._loop.quit()

    def _on_main_thread(self, func, *args) -> None:
        """Marshal a tray-thread callback onto the GLib main loop."""
        GLib.idle_add(lambda: (func(*args), False)[1])

    # -- polling ------------------------------------------------------
    def _poll_tick(self) -> bool:
        try:
            self.observer.poll()
        except Exception:  # noqa: BLE001
            log.exception("observer poll failed")
        return GLib.SOURCE_CONTINUE

    # -- observer events --------------------------------------------
    def _on_game_event(self, event: GameEvent) -> None:
        if event.running:
            profile = event.profile
            if profile is None and event.candidate is not None:
                profile = self._adopt_detected_game(event.candidate)
            if profile is None:
                return
            self._active_pids[profile.exe] = event.pid or 0
            self.payload.apply(profile, event.pid)
            self._ensure_diagnostics_running()
        elif event.profile is not None:
            self._active_pids.pop(event.profile.exe, None)
            self.payload.revert(event.profile)
            if not self.observer.active_exes:
                # Post-mortem a few seconds after exit: did the GPU let go?
                if self._fps_dip_seen and gpu.available():
                    GLib.timeout_add_seconds(5, self._fps_post_mortem)
                if not self._forced_boost:
                    self._stop_diagnostics()
        self._broadcast_status()

    def _adopt_detected_game(self, cand) -> "config.GameProfile | None":
        """Turn an auto-detected game into a (persistent) default profile and tell
        the user."""
        try:
            exe = config.sanitize_exe(cand.exe)
        except ValueError:
            log.warning("ignoring auto-detected game with an odd name: %r", cand.exe)
            return None
        if exe.lower() in {g.lower() for g in self.settings.ignored_games}:
            return None
        existing = self.settings.profile_for_exe(exe)
        if existing is not None:
            return existing if existing.enabled else None
        profile = config.GameProfile(
            exe=exe,
            display_name=cand.display_name or exe,
            auto_created=True,
            match_mode="exact" if exe.lower().endswith(".exe") else "substring",
        )
        self.settings.profiles.append(profile)
        config.save(self.settings)
        self.observer.update_settings(self.settings)
        log.info("adopted auto-detected game %s (%s)", cand.display_name, cand.source)
        self.tray.notify(
            f"Optimizing {cand.display_name}",
            "Auto-detected via " + cand.source + ". Open Goblin Mode Pro to tune or ignore it.",
        )
        self.bridge.emit_detected({
            "exe": exe, "display_name": cand.display_name,
            "source": cand.source, "app_id": cand.app_id,
        })
        return profile

    def ignore_game(self, exe: str) -> bool:
        if exe not in self.settings.ignored_games:
            self.settings.ignored_games.append(exe)
        p = self.settings.profile_for_exe(exe)
        if p is not None and p.auto_created:
            self.settings.profiles.remove(p)
        if exe in self._active_pids:
            self._active_pids.pop(exe, None)
            if p is not None:
                self.payload.revert(p)
        config.save(self.settings)
        self.observer.update_settings(self.settings)
        self._broadcast_status()
        return True

    def keep_game(self, exe: str) -> bool:
        p = self.settings.profile_for_exe(exe)
        if p is not None:
            p.auto_created = False
            config.save(self.settings)
            self.observer.update_settings(self.settings)
            self._broadcast_status()
        return True

    def _fps_post_mortem(self) -> bool:
        self._fps_dip_seen = False
        try:
            idle = gpu.deep_state()
            verdict = gpu.post_mortem(idle)
            if verdict:
                self._raise_incident(verdict[0], verdict[1], gpu_state=idle)
        except Exception:  # noqa: BLE001
            log.exception("fps post-mortem failed")
        return GLib.SOURCE_REMOVE

    # -- diagnostics ------------------------------------------------
    def _ensure_diagnostics_running(self) -> None:
        if self._diag_source_id is not None:
            return
        if not self.settings.diagnostics_enabled:
            return
        interval_ms = max(200, int(self.settings.diagnostics_sample_interval * 1000))
        self._diag_source_id = GLib.timeout_add(interval_ms, self._diag_tick)
        log.info("diagnostics sampler started")

    def _stop_diagnostics(self) -> None:
        if self._diag_source_id is not None:
            GLib.source_remove(self._diag_source_id)
            self._diag_source_id = None
            log.info("diagnostics sampler stopped")

    def _diag_tick(self) -> bool:
        try:
            sample = self.diag.sample()
            sdict = sample.as_dict()
            fps_now = self.fpswatch.current_fps()
            if fps_now is not None:
                sdict["fps"] = fps_now
            self.bridge.emit_metrics(sdict)

            # cheap GPU state refresh ~ every 8 s for the dashboard / post-mortem
            if time.monotonic() - self._gpu_state_at > 8:
                self._gpu_state = gpu.deep_state()
                self._gpu_state_at = time.monotonic()

            assessment = self.diag.assess(sample)
            if assessment:
                self._raise_incident(assessment[0], assessment[1])

            hit = self.logwatch.poll()
            if hit:
                self._raise_incident(
                    "gpu_fault", f"{hit.label}: {hit.line}", log_context=hit.context
                )

            ev = self.fpswatch.poll()
            if ev is not None:
                self._on_fps_event(ev)
        except Exception:  # noqa: BLE001
            log.exception("diagnostics tick failed")
        return GLib.SOURCE_CONTINUE

    def _on_fps_event(self, ev: FpsEvent) -> None:
        if ev.kind == "recovered":
            self._raise_incident(
                "fps_recovered",
                f"Frame rate recovered to {ev.fps:.0f} FPS after a {ev.duration_s:.0f}s dip",
                fps_trace=self.fpswatch.recent_trace(),
            )
            return
        state = gpu.deep_state()

        recent = self.diag.recent(6)
        cpu_load = max((s.cpu_load for s in recent), default=None)
        disk_read = max((s.disk_read_mbps or 0 for s in recent), default=None)
        gpu_busy = (state.get("util_gpu") or 0) >= 25 or (cpu_load or 0) >= 60

        benign = gpu.classify_dip(state, cpu_load, disk_read)
        causes = gpu.assess(state, fps=ev.fps, under_load=gpu_busy)
        state["likely_causes"] = causes
        state["cpu_load_at_dip"] = round(cpu_load, 1) if cpu_load is not None else None
        state["disk_read_mbps_at_dip"] = disk_read

        if benign and not causes:
            detail = f"Frame rate dipped to {ev.fps:.0f} FPS (baseline ~{ev.baseline:.0f}). {benign}"
            state["assessment"] = "benign - not a hardware bottleneck"
        else:
            self._fps_dip_seen = True  # only real dips trigger the post-mortem
            detail = (
                f"Frame rate collapsed to {ev.fps:.0f} FPS (baseline ~{ev.baseline:.0f}). "
                + (causes[0] if causes else (benign or "no obvious cause - see the GPU snapshot"))
            )
        self._raise_incident(
            "fps_dip", detail, gpu_state=state, fps_trace=self.fpswatch.recent_trace()
        )

    def _raise_incident(
        self,
        kind: str,
        detail: str,
        log_context: list[str] | None = None,
        gpu_state: dict | None = None,
        fps_trace: list[dict] | None = None,
    ) -> None:
        game = ", ".join(self.observer.active_exes) or "(none)"
        pid = next(iter(self._active_pids.values()), None)
        window = [s.as_dict() for s in self.diag.recent(90)]
        incident = Incident(
            kind=kind,
            detail=detail,
            game=game,
            game_pid=pid or None,
            metrics_window=_downsample(window, 20),
            logs_tail=log_context or self.logwatch.tail_tail(),
            active_tweaks=self.payload.status().as_dict(),
            gpu_state=gpu_state or {},
            fps_trace=_downsample(fps_trace or [], 30),
        )
        self.incidents.add(incident)
        self.bridge.emit_incident(incident.as_dict())

    def _on_payload_incident(self, kind: str, detail: str) -> None:
        self._raise_incident(kind, detail)

    # -- DaemonHandler protocol (called from the bridge) --------------
    def get_status(self) -> dict[str, Any]:
        tweaks = self.payload.status()
        latest = self.diag.history[-1].as_dict() if self.diag.history else None
        # keep the dashboard's GPU panel populated even when no game is running
        if not self._gpu_state and time.monotonic() - self._gpu_state_at > 15:
            self._gpu_state = gpu.deep_state()
            self._gpu_state_at = time.monotonic()
        return {
            "master_enabled": self.settings.master_enabled,
            "active_games": self.observer.active_exes,
            "forced_boost": self._forced_boost,
            "limited_mode": tweaks.limited_mode,
            "helper_available": tweaks.helper_available,
            "governor": tweaks.governor,
            "poll_interval": self.settings.poll_interval,
            "diagnostics_enabled": self.settings.diagnostics_enabled,
            "auto_detect": self.settings.auto_detect,
            "ignored_games": list(self.settings.ignored_games),
            "tweaks": tweaks.as_dict(),
            "latest_sample": latest,
            "fps": self.fpswatch.stats(),
            "gpu": _gpu_summary(self._gpu_state),
            "profiles": [_profile_dict(p) for p in self.settings.profiles],
        }

    def get_metrics(self) -> list[dict[str, Any]]:
        return [s.as_dict() for s in self.diag.history]

    def get_incidents(self) -> list[dict[str, Any]]:
        live = [i.as_dict() for i in self.incidents.all()]
        if live:
            return live
        return self.incidents.load_history()

    # -- pre-flight / report (roadmap) --------------------------------
    def run_preflight(self) -> list[dict[str, Any]]:
        from goblinmode import preflight

        return preflight.run_all()

    def apply_preflight_fixes(self) -> dict[str, Any]:
        from goblinmode import preflight

        results = preflight.run_all()
        applied, failed = [], []
        for key, value in preflight.pending_sysctls(results):
            try:
                if self.helper.set_sysctl(key, value):
                    applied.append(f"{key}={value}")
                else:
                    failed.append(key)
            except Exception as exc:  # noqa: BLE001
                failed.append(f"{key} ({exc})")
        return {
            "applied": applied,
            "failed": failed,
            "dropin_path": preflight.SYSCTL_DROPIN,
            "dropin_text": preflight.sysctl_dropin_text(results),
            "kernel_params": [r["kernel_param"] for r in results
                              if r["kernel_param"] and r["status"] in ("warn", "fail")],
        }

    def build_report(self, note: str = "") -> str:
        from goblinmode import report

        inc = self.incidents.latest()
        rep = report.build_report(
            incident=inc.as_dict() if inc else None,
            game=", ".join(self.observer.active_exes),
            active_tweaks=self.payload.status().as_dict(),
            user_note=note,
        )
        md = report.as_markdown(rep)
        from goblinmode.incidents import copy_to_clipboard

        copy_to_clipboard(md)
        return md

    def analyze_log(self) -> list[dict[str, Any]]:
        from goblinmode import logrules, runner

        logs = runner.latest_log_files(limit=1)
        if not logs:
            return []
        try:
            text = logs[0].read_text(errors="replace")[-200_000:]
        except OSError:
            return []
        return [f.__dict__ for f in logrules.analyze_text(text)]

    def set_profile(self, profile: dict[str, Any]) -> bool:
        if not isinstance(profile, dict) or not profile.get("exe"):
            return False
        known = {f.name for f in dataclasses.fields(config.GameProfile)}
        try:
            new = config.GameProfile(**{k: v for k, v in profile.items() if k in known})
        except (ValueError, TypeError) as exc:
            log.warning("rejected invalid profile: %s", exc)
            return False
        exe = new.exe
        existing = self.settings.profile_for_exe(exe)
        if existing:
            self.settings.profiles[self.settings.profiles.index(existing)] = new
        else:
            self.settings.profiles.append(new)
        # Coalesce the expensive part - a dragged SpinRow fires this ~10x/second.
        self._dirty_profiles.add(exe)
        if self._save_source_id is None:
            self._save_source_id = GLib.timeout_add(400, self._flush_profiles)
        self._broadcast_status()
        return True

    def _flush_profiles(self) -> bool:
        self._save_source_id = None
        dirty, self._dirty_profiles = self._dirty_profiles, set()
        config.save(self.settings)
        self.observer.update_settings(self.settings)
        from goblinmode import mangohud

        for exe in dirty:
            p = self.settings.profile_for_exe(exe)
            if p is None:
                continue
            self.fpswatch.update(p.fps_dip_floor, p.fps_dip_ratio)
            # keep the MangoHud config file current so the *next* launch is right
            if p.fps_watchdog or p.mangohud.get("enabled"):
                try:
                    mangohud.apply(p)
                except OSError:
                    pass
            if exe in self._active_pids:
                self.payload.reapply(p)  # governor/tearing/PL only - MangoHud can't hot-reload
        return GLib.SOURCE_REMOVE

    def remove_profile(self, exe: str) -> bool:
        p = self.settings.profile_for_exe(exe)
        if not p:
            return False
        self.settings.profiles.remove(p)
        config.save(self.settings)
        self.observer.update_settings(self.settings)
        self._broadcast_status()
        return True

    def set_master_enabled(self, enabled: bool) -> bool:
        self.settings.master_enabled = enabled
        config.save(self.settings)
        self.observer.update_settings(self.settings)
        if not enabled:
            self.payload.revert_all()
            self._active_pids.clear()
            if not self._forced_boost:
                self._stop_diagnostics()
        self._broadcast_status()
        return True

    def set_auto_detect(self, enabled: bool) -> bool:
        self.settings.auto_detect = enabled
        config.save(self.settings)
        self.observer.update_settings(self.settings)
        self._broadcast_status()
        return True

    def force_boost(self, on: bool) -> bool:
        self._forced_boost = on
        if on:
            synthetic = config.GameProfile(
                exe="__forced__", display_name="Forced performance",
                renice_enabled=False, per_game_mangohud=False,
                mangohud={"enabled": False},
            )
            self.payload.apply(synthetic, None)
            self._ensure_diagnostics_running()
        else:
            self.payload.revert(
                config.GameProfile(exe="__forced__", mangohud={"enabled": False})
            )
            if not self.observer.active_exes:
                self._stop_diagnostics()
        self._broadcast_status()
        return True

    def export_last_incident(self) -> str:
        incident = self.incidents.latest()
        if incident is None:
            history = self.incidents.load_history(limit=1)
            if not history:
                return ""
            incident = Incident(
                kind=history[0].get("kind", "unknown"),
                detail=history[0].get("detail", ""),
                game=history[0].get("game", ""),
                game_pid=history[0].get("game_pid"),
                metrics_window=history[0].get("metrics_window", []),
                logs_tail=history[0].get("logs_tail", []),
                active_tweaks=history[0].get("active_tweaks", {}),
            )
        payload = build_llm_payload(incident, self.settings.llm_model_hint)
        copy_to_clipboard(payload)
        return payload

    def write_wrapper(self) -> str:
        return str(runner.write_wrapper())

    # -- helpers ----------------------------------------------------
    def _broadcast_status(self) -> None:
        status = self.get_status()
        self.bridge.emit_status(status)
        self.tray.update(status)

    def _export_and_notify(self) -> None:
        payload = self.export_last_incident()
        if payload:
            log.info("incident payload copied to clipboard (%d chars)", len(payload))

    def _launch_gui(self) -> None:
        import subprocess

        for exe in ("/usr/bin/goblin-mode-pro", "/usr/local/bin/goblin-mode-pro"):
            if os.path.isfile(exe) and os.access(exe, os.X_OK):
                subprocess.Popen([exe])
                return
        subprocess.Popen([sys.executable, "-m", "goblinmode.gui.app"])


def _profile_dict(p: config.GameProfile) -> dict[str, Any]:
    from dataclasses import asdict

    return asdict(p)


def _gpu_summary(state: dict) -> dict:
    if not state:
        return {}
    return {
        "vram_used_mb": state.get("vram_used_mb"),
        "vram_total_mb": state.get("vram_total_mb"),
        "vram_free_mb": state.get("vram_free_mb"),
        "pcie_gen": state.get("pcie_gen"),
        "pcie_gen_max": state.get("pcie_gen_max"),
        "pcie_width": state.get("pcie_width"),
        "pcie_width_max": state.get("pcie_width_max"),
        "pstate": state.get("pstate"),
        "clock_gfx_mhz": state.get("clock_gfx_mhz"),
        "clock_gfx_max_mhz": state.get("clock_gfx_max_mhz"),
    }


def _downsample(rows: list[dict], target: int) -> list[dict]:
    """Evenly thin a list of metric samples, always keeping the last one."""
    if len(rows) <= target:
        return rows
    step = len(rows) / target
    picked = [rows[int(i * step)] for i in range(target)]
    if picked[-1] is not rows[-1]:
        picked[-1] = rows[-1]
    return picked


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="goblin-mode-pro-daemon")
    parser.add_argument(
        "--print-env-for",
        nargs=argparse.REMAINDER,
        metavar="CMD",
        help="print 'export VAR=val' lines for the given launch command (used by goblin-run)",
    )
    parser.add_argument(
        "--write-wrapper", action="store_true", help="(re)generate ~/.local/bin/goblin-run and exit"
    )
    parser.add_argument(
        "--revert", action="store_true", help="revert any applied tweaks and exit"
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.print_env_for is not None:
        argv = args.print_env_for
        if argv and argv[0] == "--":
            argv = argv[1:]
        print(runner.print_env_for(argv, config.load()))
        return 0

    if args.write_wrapper:
        print(runner.write_wrapper())
        return 0

    if args.revert:
        PerformancePayload(HelperClient()).revert_all()
        return 0

    daemon = Daemon()
    try:
        return daemon.run()
    except KeyboardInterrupt:
        daemon.shutdown()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
