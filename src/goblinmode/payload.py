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

from goblinmode import mangohud
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
    power_limited: bool = False
    power_limits_w: tuple[int, int] | None = None
    reniced: dict[str, int] | None = None
    mangohud_files: list[str] | None = None
    helper_available: bool = True
    limited_mode: bool = False

    def as_dict(self) -> dict:
        return {
            "governor": self.governor,
            "epp_boosted": self.epp_boosted,
            "tearing": self.tearing,
            "adaptive_sync": self.adaptive_sync,
            "power_limited": self.power_limited,
            "power_limits_w": list(self.power_limits_w) if self.power_limits_w else None,
            "reniced": self.reniced or {},
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
        self._mangohud_files: set[str] = set()
        self._helper_tweaks_applied = False   # governor/EPP and/or power limits
        self._governor_applied = False
        self._power_applied = False
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

    def revert(self, profile: GameProfile) -> None:
        log.info("reverting payload for %s", profile.exe)
        self._active.pop(profile.exe, None)
        self._reniced.pop(profile.exe, None)

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
        # Belt and braces even if _active was already empty.
        self._restore_global()

    # -- global (refcounted) tweaks ------------------------------------
    def _desired_power_limits_uw(self) -> tuple[int, int]:
        """Highest PL1/PL2 (in µW) requested by any active profile; 0 = leave."""
        wanting = [
            p for p in self._active.values()
            if p.power_limit_enabled and (p.pl1_w or p.pl2_w)
        ]
        pl1 = max((p.pl1_w for p in wanting), default=0)
        pl2 = max((p.pl2_w for p in wanting), default=0)
        return pl1 * 1_000_000, pl2 * 1_000_000

    def _recompute_global(self) -> None:
        want_governor = any(p.governor_boost for p in self._active.values())
        pl1_uw, pl2_uw = self._desired_power_limits_uw()
        want_power = bool(pl1_uw or pl2_uw)
        want_helper = want_governor or want_power
        want_tearing = any(p.tearing_enabled for p in self._active.values())
        want_vrr = any(p.adaptive_sync_enabled for p in self._active.values())

        if want_helper:
            # (re)apply on every recompute so a changed profile set is picked up
            self._apply_helper_tweaks(want_governor, (pl1_uw, pl2_uw) if want_power else None)
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
        self, want_governor: bool, power_uw: tuple[int, int] | None
    ) -> None:
        try:
            if want_governor:
                self.helper.set_governor(PERFORMANCE_GOVERNOR)
                self.helper.set_epp(PERFORMANCE_EPP)
                self._governor_applied = True
                log.info("CPU governor -> %s", PERFORMANCE_GOVERNOR)
            if power_uw is not None:
                self.helper.set_power_limits(power_uw[0], power_uw[1])
                self._power_applied = True
                log.info("RAPL limits -> PL1=%.0fW PL2=%.0fW",
                         power_uw[0] / 1e6, power_uw[1] / 1e6)
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

    def _renice(self, profile: GameProfile, pid: int) -> None:
        try:
            if self.helper.renice(pid, profile.nice_value):
                self._reniced[profile.exe] = pid
                log.info("reniced %s (pid %d) to %d", profile.exe, pid, profile.nice_value)
        except HelperUnavailable as exc:
            self._incident(
                "helper_unavailable", f"Cannot renice {profile.exe}: {exc}"
            )

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
            power_limited=self._power_applied,
            power_limits_w=power_limits_w,
            reniced=dict(self._reniced),
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
