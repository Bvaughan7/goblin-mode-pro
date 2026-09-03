"""The daemon's D-Bus-facing surface - everything the GUI and CLI can ask for.

This is the whole of :class:`~goblinmode.ipc.daemon_bridge.DaemonHandler` in one
place. :class:`~goblinmode.daemon.Daemon` keeps what it must own - the GLib
loop, the poll tick, the observer callbacks, and the mutable state those
maintain (``_health``, ``_forced_boost``, ``_active_pids``, ``_dirty_profiles``,
the debounced-save source id) - and this class holds the read-and-report
methods, which own no state at all.

The split follows what the methods actually touch, not what the bridge happens
to call:

* **Implemented here** - the methods that only read collaborators (settings,
  incidents, sessions, diagnostics, observer, payload, helper) and hand the
  result back. They are pure functions of daemon state, which is why they can
  move without dragging anything with them.
* **Forwarded to the daemon** - the methods that read or write state the daemon
  owns and mutates from the poll loop. Moving those would not split the object,
  it would just put a second name on the same state, so they stay where the
  state is and this class forwards to them. The forwards are cheap, and having
  the entire bridge surface listed in one file is the point: it's the contract,
  and it should be readable in one screen.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from goblinmode import capabilities, runner
from goblinmode.incidents import Incident, build_llm_payload, copy_to_clipboard
from goblinmode.ipc.helper_client import HelperUnavailable

if TYPE_CHECKING:
    from goblinmode.daemon import Daemon


class DaemonApi:
    """The bridge's handler. See the module docstring for what lives where."""

    def __init__(self, daemon: Daemon) -> None:
        self._d = daemon

    # -- convenience accessors, so the methods below read like the daemon's --
    @property
    def _settings(self):
        return self._d.settings

    @property
    def _incidents(self):
        return self._d.incidents

    # ==================================================================
    # implemented here: read-only, no daemon-owned state
    # ==================================================================

    # -- live readings -------------------------------------------------
    def get_metrics(self) -> list[dict[str, Any]]:
        return [s.as_dict() for s in self._d.diag.history]

    def get_incidents(self) -> list[dict[str, Any]]:
        live = [i.as_dict() for i in self._incidents.all()]
        if live:
            return live
        return self._incidents.load_history()

    def get_sessions(self) -> list[dict[str, Any]]:
        return self._d.sessions.history(limit=60)

    def get_session_history(self, exe: str) -> list[dict[str, Any]]:
        return self._d.sessions.history(exe or None, limit=60)

    def get_system_info(self) -> dict[str, Any]:
        """Dynamic environment info for the dashboard / first-run: connected
        controllers, GameMode status, kernel-flavour nudge."""
        return {
            "controllers": capabilities.controllers(),
            "gamemode": capabilities.gamemode_status(),
            "ananicy": capabilities.ananicy_active(),
        }

    # -- Proton / shader cache ----------------------------------------
    def get_proton_info(self) -> dict[str, Any]:
        from goblinmode import proton
        return {
            "builds": proton.installed_builds(),
            "shader_caches": proton.shader_caches(),
        }

    def clear_shader_cache(self, path: str) -> dict[str, Any]:
        from goblinmode import proton
        ok, msg = proton.clear_cache(path)
        return {"ok": ok, "message": msg}

    # -- NVIDIA module state ------------------------------------------
    def get_nvidia_module_state(self) -> dict[str, Any]:
        from goblinmode import gpu
        return gpu.nvidia_module_state()

    def set_nvidia_modeset(self, enabled: bool) -> bool:
        try:
            return bool(self._d.helper.set_nvidia_modeset(enabled))
        except HelperUnavailable:
            return False

    # -- reports / exports --------------------------------------------
    def export_setup(self) -> str:
        from goblinmode import report
        return report.build_setup_report(self._settings)

    def build_works_for_me(self, exe: str, note: str = "") -> dict[str, Any]:
        from goblinmode import report
        from goblinmode.daemon import _profile_dict

        profile = self._settings.profile_for_exe(exe)
        prof_dict = _profile_dict(profile) if profile else {
            "exe": exe, "display_name": exe}
        rep = report.build_works_for_me(prof_dict, note)
        return {
            "markdown": report.works_for_me_markdown(rep),
            "url": report.works_for_me_issue_url(rep),
        }

    def build_report(self, note: str = "") -> str:
        from goblinmode import report

        inc = self._incidents.latest()
        rep = report.build_report(
            incident=inc.as_dict() if inc else None,
            game=", ".join(self._d.observer.active_exes),
            active_tweaks=self._d.payload.status().as_dict(),
            user_note=note,
        )
        md = report.as_markdown(rep)
        copy_to_clipboard(md)
        return md

    def export_last_incident(self) -> str:
        incident = self._incidents.latest()
        if incident is None:
            history = self._incidents.load_history(limit=1)
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
        payload = build_llm_payload(incident, self._settings.llm_model_hint)
        copy_to_clipboard(payload)
        return payload

    def analyze_log(self) -> list[dict[str, Any]]:
        from goblinmode import logrules

        logs = runner.latest_log_files(limit=1)
        if not logs:
            return []
        try:
            with open(logs[0], errors="replace") as fh:
                fh.seek(0, 2)
                fh.seek(max(0, fh.tell() - 200_000))
                text = fh.read()
        except OSError:
            return []
        appid = ""
        for exe in self._d.observer.active_exes:
            p = self._settings.profile_for_exe(exe)
            if p and p.steam_app_id:
                appid = p.steam_app_id
                break
        return [f.__dict__ for f in logrules.analyze_text(text, appid=appid)]

    def write_wrapper(self) -> str:
        return str(runner.write_wrapper())

    # ==================================================================
    # forwarded: the daemon owns the state these read or write
    # ==================================================================
    # `_health` and its cache, set by run_preflight and read by get_health:
    def run_preflight(self) -> list[dict[str, Any]]:
        return self._d.run_preflight()

    def apply_preflight_fixes(self) -> dict[str, Any]:
        return self._d.apply_preflight_fixes()

    def revert_preflight_fix(self, key: str) -> dict[str, Any]:
        return self._d.revert_preflight_fix(key)

    def get_health(self) -> dict[str, Any]:
        return self._d.get_health()

    # `_dirty_profiles` / `_save_source_id` - the debounced profile save:
    def set_profile(self, profile: dict[str, Any]) -> bool:
        return self._d.set_profile(profile)

    def remove_profile(self, exe: str) -> bool:
        return self._d.remove_profile(exe)

    # `_forced_boost`, `_active_pids` and the payload, driven by the poll loop:
    def get_status(self) -> dict[str, Any]:
        return self._d.get_status()

    def force_boost(self, on: bool) -> bool:
        return self._d.force_boost(on)

    def set_master_enabled(self, enabled: bool) -> bool:
        return self._d.set_master_enabled(enabled)

    def set_auto_detect(self, enabled: bool) -> bool:
        return self._d.set_auto_detect(enabled)

    def arm_benchmark(self, exe: str) -> bool:
        return self._d.arm_benchmark(exe)

    # the auto-detect adopt/ignore decision, which mutates the settings file:
    def ignore_game(self, exe: str) -> bool:
        return self._d.ignore_game(exe)

    def unignore_game(self, exe: str) -> bool:
        return self._d.unignore_game(exe)

    def keep_game(self, exe: str) -> bool:
        return self._d.keep_game(exe)
