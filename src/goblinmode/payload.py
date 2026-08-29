"""The Performance Payload - apply/revert orchestrator.

Called by the Observer when a target game appears or exits. Per-game tweaks
(renice, MangoHud) are applied once per game; global tweaks (CPU governor + EPP,
compositor tearing) are *refcounted* - applied when the first game that wants
them appears and reverted only when the last one exits.

Every step is best-effort: one failure is logged (and optionally surfaced as an
incident) but never blocks the others.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Callable

from goblinmode import cpuset, mangohud
from goblinmode.compositor import Compositor
from goblinmode.config import GameProfile
from goblinmode.focus import FocusMode
from goblinmode.ipc.helper_client import HelperClient, HelperUnavailable
from goblinmode.paths import APPLIED_STATE_FILE, ensure_user_dirs

log = logging.getLogger(__name__)

PERFORMANCE_GOVERNOR = "performance"
PERFORMANCE_EPP = "performance"

# Synthetic profile exe used by the tray's "Force performance now" action.
FORCED_EXE = "__forced__"

IncidentCb = Callable[[str, str], None]


@dataclass
class TweakStatus:
    governor: str | None = None
    epp_boosted: bool = False
    tearing: bool = False
    adaptive_sync: bool = False
    focus_mode: bool = False
    power_limited: bool = False
    power_limits_w: tuple[int, int] | None = None
    reniced: dict[str, int] | None = None
    pinned: dict[str, str] | None = None      # exe -> mode
    mangohud_files: list[str] | None = None
    helper_available: bool = True
    limited_mode: bool = False

    def as_dict(self) -> dict:
        return {
            "governor": self.governor,
            "epp_boosted": self.epp_boosted,
            "tearing": self.tearing,
            "adaptive_sync": self.adaptive_sync,
            "focus_mode": self.focus_mode,
            "power_limited": self.power_limited,
            "power_limits_w": list(self.power_limits_w) if self.power_limits_w else None,
            "reniced": self.reniced or {},
            "pinned": self.pinned or {},
            "mangohud_files": self.mangohud_files or [],
            "helper_available": self.helper_available,
            "limited_mode": self.limited_mode,
        }


class PerformancePayload:
    def __init__(
        self, helper: HelperClient | None = None, on_incident: IncidentCb | None = None
    ) -> None:
        self.helper = helper or HelperClient()
        self.compositor = Compositor()
        self.focus = FocusMode()
        self._on_incident = on_incident
        self._active: dict[str, GameProfile] = {}   # exe -> profile
        self._reniced: dict[str, int] = {}          # exe -> pid
        self._pinned: dict[str, tuple[int, str, list[int]]] = {}  # exe -> (pid, mode, orig cpus)
        self._mangohud_files: set[str] = set()
        self._helper_tweaks_applied = False   # governor/EPP and/or power limits
        self._governor_applied = False
        self._power_applied = False
        self._power_backend: str | None = None   # "rapl" | "ryzenadj"
        self._tearing_applied = False
        self._vrr_applied = False
        self._focus_applied = False

    # -- public API -------------------------------------------------------
    def apply(self, profile: GameProfile, pid: int | None) -> None:
        log.info("applying payload for %s (pid=%s)", profile.exe, pid)
        self._active[profile.exe] = profile

        self._recompute_global()

        if profile.renice_enabled and pid:
            self._renice(profile, pid)

        if profile.core_pin != "off" and pid:
            self._pin_cores(profile, pid)

        if getattr(profile, "undervolt_reapply", False):
            try:
                if self.helper.apply_undervolt():
                    log.info("re-applied intel-undervolt offsets for %s", profile.exe)
            except HelperUnavailable:
                pass

        if profile.exe != FORCED_EXE:
            try:
                path = mangohud.apply(profile)
                self._mangohud_files.add(str(path))
            except OSError as exc:
                self._incident("payload_error", f"MangoHud apply failed: {exc}")

        self._write_applied_state()

    def reapply(self, profile: GameProfile) -> bool:
        """Re-run the *live* tweaks for a game that's already running (called
        after a profile edit in the GUI). Governor / EPP / tearing / VRR / power
        limits all take effect immediately; MangoHud does **not** - it only reads
        its config at launch - so it is intentionally not touched here.
        """
        if profile.exe not in self._active:
            return False
        log.info("re-applying live tweaks for %s after profile edit", profile.exe)
        self._active[profile.exe] = profile
        self._recompute_global()
        self._write_applied_state()
        return True

    def refresh_power_source(self) -> None:
        """Re-run the global recompute without changing which profiles are
        active - e.g. after an AC/battery flip, so a profile's battery TDP
        preset (battery_pl1_w/battery_pl2_w) takes effect immediately instead
        of waiting for the next launch/exit."""
        if self._active:
            self._recompute_global()

    def revert(self, profile: GameProfile) -> None:
        log.info("reverting payload for %s", profile.exe)
        self._active.pop(profile.exe, None)
        self._reniced.pop(profile.exe, None)
        self._unpin_cores(profile.exe)

        if profile.exe != FORCED_EXE:
            try:
                mangohud.revert(profile)
            except OSError as exc:
                log.warning("MangoHud revert failed: %s", exc)

        self._recompute_global()
        self._write_applied_state()

    def revert_all(self) -> None:
        for profile in list(self._active.values()):
            self.revert(profile)
        for exe in list(self._pinned):
            self._unpin_cores(exe)
        # Belt and braces even if _active was already empty.
        self._restore_global()

    # -- global (refcounted) tweaks ------------------------------------
    def _desired_power_limits_uw(self) -> tuple[int, int]:
        """Highest PL1/PL2 (in µW) requested by any active profile; 0 = leave.
        On battery, a profile's battery_pl1_w/battery_pl2_w (if set) is used
        instead of its AC pl1_w/pl2_w - e.g. a handheld's lower on-battery
        TDP preset."""
        on_battery = False
        try:
            from goblinmode import capabilities

            on_battery = capabilities.on_ac_power() is False
        except Exception:  # noqa: BLE001
            pass

        def _pl1(p):
            return p.battery_pl1_w if on_battery and p.battery_pl1_w else p.pl1_w

        def _pl2(p):
            return p.battery_pl2_w if on_battery and p.battery_pl2_w else p.pl2_w

        wanting = [
            p for p in self._active.values()
            if p.power_limit_enabled and (_pl1(p) or _pl2(p))
        ]
        pl1 = max((_pl1(p) for p in wanting), default=0)
        pl2 = max((_pl2(p) for p in wanting), default=0)
        return pl1 * 1_000_000, pl2 * 1_000_000

    def _tdp_backend(self) -> str | None:
        """'rapl' (Intel), 'ryzenadj' (AMD laptop) or None."""
        try:
            from goblinmode import capabilities

            return capabilities.detect().get("tdp_control")
        except Exception:  # noqa: BLE001
            return None

    def _recompute_global(self) -> None:
        want_governor = any(p.governor_boost for p in self._active.values())
        pl1_uw, pl2_uw = self._desired_power_limits_uw()
        want_power = bool(pl1_uw or pl2_uw)
        want_helper = want_governor or want_power
        want_tearing = any(p.tearing_enabled for p in self._active.values())
        want_vrr = any(p.adaptive_sync_enabled for p in self._active.values())

        power: tuple[str, tuple[int, int]] | None = None
        if want_power:
            if self._tdp_backend() == "ryzenadj":
                watts = max(round(pl1_uw / 1_000_000), round(pl2_uw / 1_000_000))
                power = ("ryzenadj", (watts, 0))
            else:
                power = ("rapl", (pl1_uw, pl2_uw))

        if want_helper:
            # (re)apply on every recompute so a changed profile set is picked up
            self._apply_helper_tweaks(want_governor, power)
        elif self._helper_tweaks_applied:
            self._restore_helper_tweaks()

        if want_tearing and not self._tearing_applied:
            self._tearing_applied = self.compositor.enable_tearing()
        elif not want_tearing and self._tearing_applied:
            self.compositor.restore_tearing()
            self._tearing_applied = False

        if want_vrr and not self._vrr_applied:
            self._vrr_applied = self.compositor.enable_adaptive_sync()
        elif not want_vrr and self._vrr_applied:
            self.compositor.restore_adaptive_sync()
            self._vrr_applied = False

        want_focus = any(p.focus_mode for p in self._active.values())
        if want_focus and not self._focus_applied:
            self.focus.enter()
            self._focus_applied = True
        elif not want_focus and self._focus_applied:
            self.focus.exit()
            self._focus_applied = False

    def _restore_global(self) -> None:
        if self._helper_tweaks_applied:
            self._restore_helper_tweaks()
        if self._tearing_applied:
            self.compositor.restore_tearing()
            self._tearing_applied = False
        if self._vrr_applied:
            self.compositor.restore_adaptive_sync()
            self._vrr_applied = False
        if self._focus_applied:
            self.focus.exit()
            self._focus_applied = False

    def _apply_helper_tweaks(
        self, want_governor: bool, power: tuple[str, tuple[int, int]] | None
    ) -> None:
        try:
            if want_governor:
                self.helper.set_governor(PERFORMANCE_GOVERNOR)
                self.helper.set_epp(PERFORMANCE_EPP)
                self._governor_applied = True
                log.info("CPU governor -> %s", PERFORMANCE_GOVERNOR)
            if power is not None:
                kind, values = power
                if kind == "ryzenadj":
                    if values[0] and self.helper.set_tdp(values[0]):
                        self._power_applied = True
                        self._power_backend = "ryzenadj"
                        log.info("AMD TDP -> %dW (ryzenadj)", values[0])
                else:
                    self.helper.set_power_limits(values[0], values[1])
                    self._power_applied = True
                    self._power_backend = "rapl"
                    log.info("RAPL limits -> PL1=%.0fW PL2=%.0fW",
                             values[0] / 1e6, values[1] / 1e6)
            self._helper_tweaks_applied = True
        except HelperUnavailable as exc:
            self._incident(
                "helper_unavailable",
                f"Cannot apply CPU/power tweaks (helper down): {exc}",
            )

    def _restore_helper_tweaks(self) -> None:
        try:
            self.helper.revert_all()
            log.info("CPU governor / EPP / power limits restored via helper")
        except HelperUnavailable as exc:
            log.warning("helper unavailable on restore: %s", exc)
        self._helper_tweaks_applied = False
        self._governor_applied = False
        self._power_applied = False
        self._power_backend = None

    def _renice(self, profile: GameProfile, pid: int) -> None:
        try:
            if self.helper.renice(pid, profile.nice_value):
                self._reniced[profile.exe] = pid
                log.info("reniced %s (pid %d) to %d", profile.exe, pid, profile.nice_value)
        except HelperUnavailable as exc:
            self._incident(
                "helper_unavailable", f"Cannot renice {profile.exe}: {exc}"
            )

    def _pin_cores(self, profile: GameProfile, pid: int) -> None:
        """Pin the game's threads to a CPU subset. No privilege needed - the game
        is our own child - so this works even in limited mode."""
        try:
            from goblinmode import capabilities

            layout = capabilities.detect().get("core_layout", {})
            cpus = cpuset.target_cpus(profile.core_pin, layout)
            if not cpus:
                return
            original = cpuset.current_affinity(pid) or list(layout.get("online", []))
            if cpuset.pin(pid, cpus):
                self._pinned[profile.exe] = (pid, profile.core_pin, original)
        except Exception as exc:  # noqa: BLE001 - never let pinning break a launch
            log.warning("core pinning failed for %s: %s", profile.exe, exc)

    def _unpin_cores(self, exe: str) -> None:
        entry = self._pinned.pop(exe, None)
        if entry is None:
            return
        pid, _mode, original = entry
        cpuset.restore(pid, original)

    # -- status ---------------------------------------------------------
    def status(self) -> TweakStatus:
        helper_ok = self.helper.available()
        governor = None
        power_limits_w = None
        if helper_ok:
            try:
                governor = self.helper.get_governor()
                pl1, pl2 = self.helper.get_power_limits()
                power_limits_w = (round(pl1 / 1e6), round(pl2 / 1e6))
            except HelperUnavailable:
                helper_ok = False
        return TweakStatus(
            governor=governor,
            epp_boosted=self._governor_applied,
            tearing=self._tearing_applied,
            adaptive_sync=self._vrr_applied,
            focus_mode=self._focus_applied,
            power_limited=self._power_applied,
            power_limits_w=power_limits_w,
            reniced=dict(self._reniced),
            pinned={exe: mode for exe, (_p, mode, _o) in self._pinned.items()},
            mangohud_files=sorted(self._mangohud_files),
            helper_available=helper_ok,
            limited_mode=not helper_ok,
        )

    def active_games(self) -> list[str]:
        return sorted(self._active)

    # -- persistence / incidents --------------------------------------
    def _write_applied_state(self) -> None:
        try:
            ensure_user_dirs()
            APPLIED_STATE_FILE.write_text(
                json.dumps(
                    {
                        "active": self.active_games(),
                        "governor_applied": self._governor_applied,
                        "power_applied": self._power_applied,
                        "tearing_applied": self._tearing_applied,
                        "adaptive_sync_applied": self._vrr_applied,
                        "reniced": self._reniced,
                    },
                    indent=2,
                )
            )
        except OSError as exc:
            log.warning("could not write applied state: %s", exc)

    def _incident(self, kind: str, detail: str) -> None:
        log.warning("%s: %s", kind, detail)
        if self._on_incident:
            self._on_incident(kind, detail)
