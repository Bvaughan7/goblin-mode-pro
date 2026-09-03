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
from collections.abc import Callable

from goblinmode import cpuset, mangohud
from goblinmode.compositor import Compositor
from goblinmode.config import GameProfile
from goblinmode.focus import FocusMode
from goblinmode.ipc.helper_client import HelperClient, HelperUnavailable
from goblinmode.paths import APPLIED_STATE_FILE, ensure_user_dirs
from goblinmode.scx import ScxManager
from goblinmode.textfmt import name as _name, names as _names

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
    #: sched_ext scheduler we switched to for a game, short name, or None
    scx_scheduler: str | None = None
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
        self._power_values: tuple[int, int] | None = None  # last-applied (as requested)
        self._fan_spinup_applied = False
        self._tearing_applied = False
        self._vrr_applied = False
        self._refresh_cap_applied = False
        self._focus_applied = False
        self.scx = ScxManager()
        self._scx_applied: str | None = None       # scheduler we switched to
        self._scx_previous: str | None = None      # what was running before us

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

        if getattr(profile, "amd_undervolt_reapply", False):
            try:
                if self.helper.apply_amd_undervolt():
                    log.info("re-applied AMD Curve Optimizer offsets for %s", profile.exe)
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
        except Exception as exc:  # noqa: BLE001 - no battery sensor / probe error
            log.debug("power-source probe failed, assuming AC: %s", exc)

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
        want_fan_spinup = any(getattr(p, "fan_spinup_enabled", False)
                              for p in self._active.values())
        want_helper = want_governor or want_power or want_fan_spinup
        want_tearing = any(p.tearing_enabled for p in self._active.values())
        vrr_wanting = [p for p in self._active.values() if p.adaptive_sync_enabled]
        want_vrr = bool(vrr_wanting)
        # Union of every wanting profile's restricted outputs; if any of them
        # wants "all outputs" (an empty list), that wins - it's the broader ask.
        vrr_outputs: list[str] | None = None
        if vrr_wanting and all(p.vrr_outputs for p in vrr_wanting):
            vrr_outputs = sorted({o for p in vrr_wanting for o in p.vrr_outputs})

        power: tuple[str, tuple[int, int]] | None = None
        if want_power:
            if self._tdp_backend() == "ryzenadj":
                watts = max(round(pl1_uw / 1_000_000), round(pl2_uw / 1_000_000))
                power = ("ryzenadj", (watts, 0))
            else:
                power = ("rapl", (pl1_uw, pl2_uw))

        if want_helper:
            # (re)apply on every recompute so a changed profile set is picked up
            self._apply_helper_tweaks(want_governor, power, want_fan_spinup)
        elif self._helper_tweaks_applied:
            self._restore_helper_tweaks()

        if want_tearing and not self._tearing_applied:
            self._tearing_applied = self.compositor.enable_tearing()
        elif not want_tearing and self._tearing_applied:
            self.compositor.restore_tearing()
            self._tearing_applied = False

        if want_vrr and not self._vrr_applied:
            self._vrr_applied = self.compositor.enable_adaptive_sync(outputs=vrr_outputs)
        elif not want_vrr and self._vrr_applied:
            self.compositor.restore_adaptive_sync()
            self._vrr_applied = False

        refresh_wanting = [p.refresh_rate_hz for p in self._active.values() if p.refresh_rate_hz]
        want_refresh_cap = bool(refresh_wanting)
        if want_refresh_cap and not self._refresh_cap_applied:
            self._refresh_cap_applied = self.compositor.enable_refresh_cap(min(refresh_wanting))
        elif not want_refresh_cap and self._refresh_cap_applied:
            self.compositor.restore_refresh_cap()
            self._refresh_cap_applied = False

        self._recompute_scx()

        want_focus = any(p.focus_mode for p in self._active.values())
        if want_focus and not self._focus_applied:
            self.focus.enter()
            self._focus_applied = True
        elif not want_focus and self._focus_applied:
            self.focus.exit()
            self._focus_applied = False

    def _restore_scx(self) -> None:
        if self._scx_applied is None:
            return
        self.scx.restore(self._scx_previous)
        self._scx_applied = None
        self._scx_previous = None

    def _recompute_scx(self) -> None:
        """Switch the kernel scheduler for games that ask for one.

        Refcounted like tearing or the governor: the first active profile that
        names a scheduler wins, and the scheduler is put back only when no
        active profile wants one. If two games disagree the first one sorted
        wins deterministically rather than the pair flapping.
        """
        wanted = sorted(
            (p.scx_scheduler, getattr(p, "scx_mode", "gaming"))
            for p in self._active.values() if p.scx_scheduler
        )
        if not wanted:
            self._restore_scx()
            return
        scheduler, mode = wanted[0]
        if self._scx_applied == scheduler:
            return
        if self._scx_applied is None:
            # Remember what was running before we touched anything, so the
            # revert restores the machine's state rather than a guess.
            self._scx_previous = self.scx.current()
        if self.scx.switch(scheduler, mode):
            self._scx_applied = scheduler
        else:
            self._incident(
                "scx_switch_failed",
                f"could not switch the CPU scheduler to scx_{scheduler} - "
                "the game is running on the kernel's own scheduler",
            )

    def _restore_global(self) -> None:
        self._restore_scx()
        if self._helper_tweaks_applied:
            self._restore_helper_tweaks()
        if self._tearing_applied:
            self.compositor.restore_tearing()
            self._tearing_applied = False
        if self._vrr_applied:
            self.compositor.restore_adaptive_sync()
            self._vrr_applied = False
        if self._refresh_cap_applied:
            self.compositor.restore_refresh_cap()
            self._refresh_cap_applied = False
        if self._focus_applied:
            self.focus.exit()
            self._focus_applied = False

    def _apply_helper_tweaks(
        self, want_governor: bool, power: tuple[str, tuple[int, int]] | None,
        want_fan_spinup: bool = False,
    ) -> None:
        try:
            if want_governor:
                self.helper.set_governor(PERFORMANCE_GOVERNOR)
                self.helper.set_epp(PERFORMANCE_EPP)
                self._governor_applied = True
                log.info("CPU governor -> %s", PERFORMANCE_GOVERNOR)
            # If a power limit is applied but the ask has since changed (a
            # different backend, or dropped entirely because the game that
            # wanted it exited), undo it first - otherwise a raised TDP leaks
            # until the *last* game exits. The helper exposes ResetPowerLimits
            # / ResetTDP for exactly this.
            want = (power[0], tuple(power[1])) if power is not None else None
            have = (self._power_backend, self._power_values)
            if self._power_applied and have != want:
                self._reset_power()
            if power is not None:
                kind, values = power
                if kind == "ryzenadj":
                    if values[0] and self.helper.set_tdp(values[0]):
                        self._power_applied = True
                        self._power_backend = "ryzenadj"
                        self._power_values = tuple(values)
                        log.info("AMD TDP -> %dW (ryzenadj)", values[0])
                else:
                    self.helper.set_power_limits(values[0], values[1])
                    self._power_applied = True
                    self._power_backend = "rapl"
                    self._power_values = tuple(values)
                    log.info("RAPL limits -> PL1=%.0fW PL2=%.0fW",
                             values[0] / 1e6, values[1] / 1e6)
            if want_fan_spinup and not self._fan_spinup_applied:
                # Best-effort: False just means no writable pwm on this EC,
                # the overwhelming common case - not worth its own incident.
                self._fan_spinup_applied = self.helper.spin_up_fans(100)
                if self._fan_spinup_applied:
                    log.info("fan spin-up requested")
            self._helper_tweaks_applied = True
        except HelperUnavailable as exc:
            self._incident(
                "helper_unavailable",
                f"Cannot apply CPU/power tweaks (helper down): {exc}",
            )

    def _reset_power(self) -> None:
        """Undo just the power limit / TDP (leave the governor - another game
        may still want it). Clears the power bookkeeping."""
        try:
            if self._power_backend == "ryzenadj":
                self.helper.reset_tdp()
            else:
                self.helper.reset_power_limits()
            log.info("power limit reset (%s)", self._power_backend)
        except HelperUnavailable as exc:
            log.warning("helper unavailable resetting power limit: %s", exc)
        self._power_applied = False
        self._power_backend = None
        self._power_values = None

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
        self._power_values = None
        self._fan_spinup_applied = False

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
            scx_scheduler=self._scx_applied,
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
                        "power_backend": self._power_backend,
                        "tearing_applied": self._tearing_applied,
                        "adaptive_sync_applied": self._vrr_applied,
                        "refresh_cap_applied": self._refresh_cap_applied,
                        "focus_mode": self._focus_applied,
                        "scx_applied": self._scx_applied,
                        "scx_previous": self._scx_previous,
                        "reniced": self._reniced,
                        # everything the cold --revert path needs to undo the
                        # compositor without the daemon's in-memory state
                        "compositor": self.compositor.restore_state(),
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


# --------------------------------------------------------------------------
# Cold, state-driven revert
# --------------------------------------------------------------------------
# The daemon holds all its apply/revert bookkeeping in memory. That is fine
# for the normal path (a game exits -> revert), but useless for the two cases
# where the process that applied the tweaks is already gone:
#
#   * ``goblin-mode-pro-daemon --revert`` - the systemd ``ExecStop`` /
#     crash-recovery hook;
#   * a daemon that starts and finds a stale ``applied.json`` from a previous
#     instance that was killed (SIGKILL, OOM, power loss) without reverting.
#
# Both are handled here by reading ``applied.json`` and undoing what it
# records. The privileged half needs no such record: the helper keeps its own
# root-owned snapshot in ``/run`` and ``RevertAll`` is idempotent.

#: The compositor tweaks that survive the daemon, and so have to be undone
#: from the state file rather than from memory. Named once because both the
#: "is there anything to undo" check and the revert itself ask for it, and a
#: drift between those two means a revert that reports work it does not do.
_COMPOSITOR_KEYS = ("tearing_active", "vrr_active", "refresh_active", "x11_suspended")


def _read_applied_state() -> dict | None:
    """The recorded state, or None when there is nothing usable to read.

    A file that parses as JSON but is not an object is as unusable as one that
    does not parse at all, and both mean the same thing to every caller here:
    no usable record. Neither is a reason to raise - this is the crash-recovery
    path, and the one thing it must not do is need recovering itself.
    """
    try:
        data = json.loads(APPLIED_STATE_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _compositor_state(data: dict) -> dict:
    """The compositor sub-record, which a hand-edited file may have replaced
    with something that is not a mapping."""
    comp = data.get("compositor")
    return comp if isinstance(comp, dict) else {}


def _compositor_needs_restore(comp: dict) -> bool:
    return any(comp.get(key) for key in _COMPOSITOR_KEYS)


def _clear_applied_state() -> None:
    try:
        APPLIED_STATE_FILE.unlink(missing_ok=True)
    except OSError as exc:
        log.warning("could not clear applied state: %s", exc)


def applied_state_dirty() -> bool:
    """True when ``applied.json`` records anything actually applied - i.e. a
    previous daemon instance exited without reverting. A clean shutdown leaves
    the file present but with everything cleared, which is *not* dirty."""
    data = _read_applied_state()
    if not data:
        return False
    if data.get("active") or data.get("reniced"):
        return True
    if any(data.get(k) for k in (
        "governor_applied", "power_applied", "tearing_applied",
        "adaptive_sync_applied", "refresh_cap_applied", "focus_mode",
        "scx_applied",
    )):
        return True
    return _compositor_needs_restore(_compositor_state(data))


def describe_applied_state() -> list[str]:
    """What ``--revert`` would undo, as plain lines. Reads nothing but the
    state file, changes nothing - so it is safe to run at any time, and it is
    what makes the state-driven revert inspectable in a bug report.

    Deliberately describes ``applied.json`` only. The helper's own root-owned
    snapshot in /run drives an unconditional, idempotent RevertAll that this
    process cannot read, so it is reported as the fixed step it is rather than
    guessed at.
    """
    lines: list[str] = []
    data = _read_applied_state()
    if data is None:
        lines.append(f"no applied state at {APPLIED_STATE_FILE} - nothing recorded")
    elif not applied_state_dirty():
        lines.append(f"{APPLIED_STATE_FILE} is present but clean "
                     "(the last daemon shut down properly) - nothing to undo")
    else:
        if data.get("active"):
            lines.append(f"active games: {', '.join(_names(data['active']))}")
        if data.get("reniced"):
            lines.append("restore priority for pid(s): "
                         + ", ".join(_names(data["reniced"])))
        for key, text in (
            ("governor_applied", "restore the CPU governor / EPP"),
            ("power_applied", "reset the CPU power limits"),
            ("tearing_applied", "turn tearing back off"),
            ("adaptive_sync_applied", "restore adaptive sync / VRR"),
            ("refresh_cap_applied", "restore the panel refresh rate"),
            ("focus_mode", "leave focus mode (indexer, DND, screen blanking)"),
        ):
            if data.get(key):
                lines.append(text)
        if data.get("scx_applied"):
            prev = data.get("scx_previous")
            lines.append(
                f"CPU scheduler: stop scx_{_name(data['scx_applied'])} and "
                + (f"switch back to scx_{_name(prev)}" if prev
                   else "return to the kernel's own scheduler"))
        comp = _compositor_state(data)
        for key, text in (
            ("tearing_active", "compositor: tearing"),
            ("vrr_active", "compositor: VRR"),
            ("refresh_active", "compositor: refresh cap"),
            ("x11_suspended", "compositor: X11 compositing suspended"),
        ):
            if comp.get(key):
                lines.append(f"{text} -> restore recorded value")
        if data.get("power_backend"):
            lines.append(f"power backend in use: {_name(data['power_backend'])}")
    lines.append("always: helper RevertAll (governor/EPP/RAPL/TDP/fans from "
                 "the helper's own /run snapshot - idempotent)")
    return lines


@dataclass(frozen=True)
class RevertPlan:
    """Which cold-restore steps the recorded state calls for.

    Separated from the doing so that the decisions can be checked without a
    compositor, a session bus or a scheduler - this is the path that runs when
    the machine is already in a bad way, and it is the one that has been wrong
    before. The helper's own ``RevertAll`` is not here because it is not a
    decision: it runs unconditionally, off a root-owned snapshot this process
    cannot read, and it is idempotent.
    """

    compositor: bool
    compositor_state: dict
    focus_mode: bool
    scx: bool
    scx_previous: object = None


def revert_plan(data: dict | None) -> RevertPlan:
    data = data or {}
    comp_state = _compositor_state(data)
    return RevertPlan(
        compositor=_compositor_needs_restore(comp_state),
        compositor_state=comp_state,
        focus_mode=bool(data.get("focus_mode")),
        scx=bool(data.get("scx_applied")),
        scx_previous=data.get("scx_previous"),
    )


def revert_from_state(helper: HelperClient | None = None) -> bool:
    """Undo every tweak recorded in ``applied.json`` (compositor, focus mode)
    plus, unconditionally, whatever the helper's own snapshot records
    (governor/EPP/RAPL/TDP/fans). Clears the state file on completion. Safe to
    call with nothing applied."""
    helper = helper or HelperClient()
    ok = True

    try:
        helper.revert_all()
        log.info("helper state reverted")
    except HelperUnavailable as exc:
        log.warning("helper unavailable during --revert: %s", exc)

    plan = revert_plan(_read_applied_state())
    if plan.compositor:
        comp = Compositor()
        comp.load_restore_state(plan.compositor_state)
        for restore in (comp.restore_tearing, comp.restore_adaptive_sync,
                        comp.restore_refresh_cap):
            try:
                restore()
            except Exception as exc:  # noqa: BLE001 - best effort, keep going
                log.warning("compositor cold-restore step failed: %s", exc)
                ok = False

    if plan.focus_mode:
        try:
            FocusMode().force_restore()
        except Exception as exc:  # noqa: BLE001
            log.warning("focus-mode cold-restore failed: %s", exc)
            ok = False

    if plan.scx:
        # A game that died with a sched_ext scheduler loaded leaves the whole
        # machine on it, so this matters more than the rest of the cold
        # restore: it is system-wide and survives the daemon.
        try:
            if not ScxManager().restore(plan.scx_previous):
                ok = False
        except Exception as exc:  # noqa: BLE001
            log.warning("sched_ext cold-restore failed: %s", exc)
            ok = False

    _clear_applied_state()
    return ok
