"""Compositor / presentation handling.

On X11 the classic move is to suspend the compositor entirely. On the primary
target (KDE Plasma 6 / Wayland) KWin *cannot* disable compositing, so the
Wayland-appropriate equivalents are used instead:

* **Allow Tearing** (immediate presentation) enabled for the duration of the
  game via ``kwriteconfig6`` + a KWin reconfigure, restored on exit.
* **Adaptive Sync / VRR** switched to ``automatic`` on any capable output (or
  a specific one - see ``outputs=`` below) via ``kscreen-doctor``, restored
  on exit.

Other sessions:

* **KDE + X11**  -> real compositor suspend/resume over ``org.kde.KWin`` D-Bus.
* **Hyprland**    -> ``hyprctl keyword`` for tearing (``general:allow_tearing``)
  and VRR (``misc:vrr``). Hyprland's VRR toggle is compositor-wide, not
  per-output like KDE's - there is no equivalent per-monitor runtime knob to
  call, so ``outputs=`` is accepted but has no effect there; that gap is
  intentional, not an oversight.
* **GNOME / other wlroots / unknown** -> no-op with a clear log line (rely on
  ``gamemoderun``, which the launch wrapper already uses). GNOME (Mutter) has
  no equivalent runtime IPC for either toggle as of this writing.

Every operation is best-effort and never raises.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess

log = logging.getLogger(__name__)

_KWRITE = "kwriteconfig6"
_KREAD = "kreadconfig6"
_QDBUS = "qdbus6"
_KSCREEN = "kscreen-doctor"
_HYPRCTL = "hyprctl"

_TEARING_GROUP = "Compositing"
_TEARING_KEY = "AllowTearing"

_VRR_VALUES = {"never", "automatic", "always"}


def _session_type() -> str:
    return os.environ.get("XDG_SESSION_TYPE", "").lower()


def _desktop() -> str:
    return os.environ.get("XDG_CURRENT_DESKTOP", "").upper()


def _is_kde() -> bool:
    return "KDE" in _desktop()


def _is_hyprland() -> bool:
    return bool(os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")) and shutil.which(_HYPRCTL) is not None


def _run(cmd: list[str], timeout: int = 6) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("%s failed: %s", cmd[0], exc)
        return None


# --------------------------------------------------------------------------
# KWin AllowTearing (Wayland)
# --------------------------------------------------------------------------
def _read_allow_tearing() -> str | None:
    cp = _run([_KREAD, "--file", "kwinrc", "--group", _TEARING_GROUP, "--key", _TEARING_KEY])
    return cp.stdout.strip() if cp else None


def _write_allow_tearing(value: str) -> bool:
    if not _run([_KWRITE, "--file", "kwinrc", "--group", _TEARING_GROUP,
                 "--key", _TEARING_KEY, value]):
        return False
    _run([_QDBUS, "org.kde.KWin", "/KWin", "org.kde.KWin.reconfigure"])
    return True


# --------------------------------------------------------------------------
# Adaptive Sync / VRR via kscreen-doctor
# --------------------------------------------------------------------------
def _vrr_outputs() -> dict[str, str]:
    """Return {output_name: current_vrr_policy} for VRR-capable outputs only."""
    cp = _run([_KSCREEN, "-o"])
    if not cp or cp.returncode != 0:
        return {}
    out: dict[str, str] = {}
    name: str | None = None
    for line in cp.stdout.splitlines():
        m = re.match(r"^Output:\s+\d+\s+(\S+)", line.strip())
        if m:
            name = m.group(1)
            continue
        vm = re.search(r"Vrr:\s*(\w+)", line)
        if vm and name:
            state = vm.group(1).lower()
            if state != "incapable":
                out[name] = state
            name = None
    return out


def _set_vrr(output: str, policy: str) -> bool:
    if policy not in _VRR_VALUES:
        return False
    return bool(_run([_KSCREEN, f"output.{output}.vrrpolicy.{policy}"]))


# --------------------------------------------------------------------------
# Per-output refresh-rate cap via kscreen-doctor (mainly for a handheld's
# internal panel: Deck 40/50/60 Hz, Ally 120 Hz, ...)
# --------------------------------------------------------------------------
_MODE_RE = re.compile(r"(\d+):(\d+)x(\d+)@(\d+)(!?)(\*?)")


def _parse_output_modes(stdout: str) -> dict[str, dict]:
    """``{output_name: {"modes": {mode_id: (w, h, hz)}, "current": mode_id}}``
    from ``kscreen-doctor -o``'s ``Modes:`` line, e.g.
    ``Modes: 87:1920x1080@144!*  88:1920x1080@60``  ('!' preferred, '*' active)."""
    out: dict[str, dict] = {}
    name: str | None = None
    for line in stdout.splitlines():
        m = re.match(r"^Output:\s+\d+\s+(\S+)", line.strip())
        if m:
            name = m.group(1)
            out[name] = {"modes": {}, "current": None}
            continue
        if name and "Modes:" in line:
            for mid, w, h, hz, _pref, active in _MODE_RE.findall(line):
                out[name]["modes"][mid] = (int(w), int(h), int(hz))
                if active:
                    out[name]["current"] = mid
    return out


def _internal_panel_output() -> str | None:
    """The laptop/handheld's built-in panel, if any (kscreen-doctor names it
    eDP-* the way every other Linux display stack does)."""
    cp = _run([_KSCREEN, "-o"])
    if not cp or cp.returncode != 0:
        return None
    for name in _parse_output_modes(cp.stdout):
        if name.startswith("eDP"):
            return name
    return None


def _find_mode_id(modes: dict[str, tuple[int, int, int]], w: int, h: int, hz: int) -> str | None:
    for mid, (mw, mh, mhz) in modes.items():
        if mw == w and mh == h and mhz == hz:
            return mid
    return None


def _set_refresh_rate(output: str, hz: int) -> tuple[bool, str | None]:
    """Switch ``output`` to the same resolution at ``hz``, if such a mode
    exists. Returns ``(ok, previous_mode_id)`` so the caller can restore it -
    ``ok=False`` covers "no matching mode" as much as a real failure."""
    cp = _run([_KSCREEN, "-o"])
    if not cp or cp.returncode != 0:
        return False, None
    o = _parse_output_modes(cp.stdout).get(output)
    if not o or o["current"] is None:
        return False, None
    cur_id = o["current"]
    w, h, _ = o["modes"][cur_id]
    target_id = _find_mode_id(o["modes"], w, h, hz)
    if not target_id or target_id == cur_id:
        return False, None
    ok = bool(_run([_KSCREEN, f"output.{output}.mode.{target_id}"]))
    return ok, (cur_id if ok else None)


def _restore_mode(output: str, mode_id: str) -> bool:
    return bool(_run([_KSCREEN, f"output.{output}.mode.{mode_id}"]))


# --------------------------------------------------------------------------
# Hyprland (hyprctl keyword) - compositor-wide, not per-output
# --------------------------------------------------------------------------
def _hyprctl_get_option(name: str) -> str | None:
    cp = _run([_HYPRCTL, "-j", "getoption", name])
    if not cp or cp.returncode != 0:
        return None
    try:
        return str(json.loads(cp.stdout)["int"])
    except (ValueError, KeyError, TypeError):
        return None


def _hyprctl_set_option(name: str, value: str) -> bool:
    cp = _run([_HYPRCTL, "keyword", name, value])
    return bool(cp and cp.returncode == 0)


# --------------------------------------------------------------------------
class Compositor:
    """Stateful presentation toggles with save/restore of prior values."""

    def __init__(self) -> None:
        self._tearing_saved: str | None = None
        self._tearing_active = False
        self._tearing_backend: str | None = None  # "hyprland" or None (KWin)
        self._vrr_saved: dict[str, str] = {}
        self._vrr_saved_hyprland: str | None = None
        self._vrr_active = False
        self._x11_suspended = False
        self._refresh_saved: dict[str, str] = {}  # output -> mode id to restore
        self._refresh_active = False

    # -- capability --------------------------------------------------
    @property
    def tearing_supported(self) -> bool:
        if _session_type() == "wayland" and _is_kde():
            return all(shutil.which(t) for t in (_KWRITE, _KREAD, _QDBUS))
        if _session_type() == "x11" and _is_kde():
            return shutil.which(_QDBUS) is not None
        if _is_hyprland():
            return True
        return False

    @property
    def adaptive_sync_supported(self) -> bool:
        if _is_kde() and shutil.which(_KSCREEN) is not None:
            return True
        return _is_hyprland()

    # -- tearing / compositor suspend ------------------------------
    def enable_tearing(self) -> bool:
        if self._tearing_active:
            return True
        if _session_type() == "x11" and _is_kde():
            return self._suspend_kwin_x11()
        if _is_hyprland():
            return self._enable_tearing_hyprland()
        if not self.tearing_supported:
            log.info("tearing/compositor tweak unsupported on this session - skipping")
            return False
        self._tearing_saved = _read_allow_tearing() or "false"
        if _write_allow_tearing("true"):
            self._tearing_active = True
            log.info("KWin AllowTearing enabled (was %s)", self._tearing_saved)
            return True
        return False

    def restore_tearing(self) -> bool:
        if self._x11_suspended:
            return self._resume_kwin_x11()
        if not self._tearing_active:
            return True
        if self._tearing_backend == "hyprland":
            ok = _hyprctl_set_option("general:allow_tearing", self._tearing_saved or "0")
            log.info("Hyprland allow_tearing restored to %s", self._tearing_saved or "0")
        else:
            ok = _write_allow_tearing(self._tearing_saved or "false")
            log.info("KWin AllowTearing restored to %s", self._tearing_saved or "false")
        self._tearing_active = False
        self._tearing_saved = None
        self._tearing_backend = None
        return ok

    def _enable_tearing_hyprland(self) -> bool:
        self._tearing_saved = _hyprctl_get_option("general:allow_tearing") or "0"
        if _hyprctl_set_option("general:allow_tearing", "1"):
            self._tearing_active = True
            self._tearing_backend = "hyprland"
            log.info("Hyprland allow_tearing enabled (was %s)", self._tearing_saved)
            return True
        return False

    # backwards-compatible alias
    restore = restore_tearing

    def _suspend_kwin_x11(self) -> bool:
        if _run([_QDBUS, "org.kde.KWin", "/Compositor", "suspend"]):
            self._x11_suspended = True
            log.info("KWin compositor suspended (X11)")
            return True
        return False

    def _resume_kwin_x11(self) -> bool:
        ok = bool(_run([_QDBUS, "org.kde.KWin", "/Compositor", "resume"]))
        self._x11_suspended = False
        log.info("KWin compositor resumed (X11)")
        return ok

    # -- adaptive sync -------------------------------------------
    def enable_adaptive_sync(self, policy: str = "automatic",
                             outputs: list[str] | None = None) -> bool:
        """Enable VRR. ``outputs``, when given, restricts the change to just
        those output names (KDE only - see the module docstring for why
        Hyprland can't honor it, and is unaffected by this parameter)."""
        if self._vrr_active:
            return True
        if not self.adaptive_sync_supported:
            log.info("adaptive sync unsupported on this session - skipping")
            return False
        if _is_hyprland():
            return self._enable_adaptive_sync_hyprland()
        available = _vrr_outputs()
        if outputs:
            available = {n: v for n, v in available.items() if n in outputs}
        if not available:
            log.info("no VRR-capable outputs - skipping adaptive sync")
            return False
        changed = False
        for name, current in available.items():
            if current == policy:
                continue
            if _set_vrr(name, policy):
                self._vrr_saved[name] = current
                changed = True
                log.info("adaptive sync on %s: %s -> %s", name, current, policy)
        self._vrr_active = changed or bool(self._vrr_saved)
        return self._vrr_active

    def restore_adaptive_sync(self) -> bool:
        if not self._vrr_active:
            return True
        if self._vrr_saved_hyprland is not None:
            ok = _hyprctl_set_option("misc:vrr", self._vrr_saved_hyprland)
            log.info("Hyprland misc:vrr restored to %s", self._vrr_saved_hyprland)
            self._vrr_saved_hyprland = None
            self._vrr_active = False
            return ok
        ok = True
        for name, prev in self._vrr_saved.items():
            if not _set_vrr(name, prev):
                ok = False
            else:
                log.info("adaptive sync on %s restored to %s", name, prev)
        self._vrr_saved.clear()
        self._vrr_active = False
        return ok

    # -- refresh-rate cap (internal panel) ------------------------
    def enable_refresh_cap(self, hz: int, output: str | None = None) -> bool:
        """Cap ``output`` (default: the internal panel, if any) to ``hz`` -
        KDE/kscreen-doctor only, and only if a mode at the same resolution
        and that exact refresh rate is actually advertised."""
        if self._refresh_active:
            return True
        if not (_is_kde() and shutil.which(_KSCREEN)):
            return False
        target = output or _internal_panel_output()
        if not target:
            return False
        ok, prev_mode = _set_refresh_rate(target, hz)
        if ok and prev_mode:
            self._refresh_saved[target] = prev_mode
            self._refresh_active = True
            log.info("refresh rate on %s capped to %d Hz", target, hz)
        return ok

    def restore_refresh_cap(self) -> bool:
        if not self._refresh_active:
            return True
        ok = True
        for output, mode_id in self._refresh_saved.items():
            if not _restore_mode(output, mode_id):
                ok = False
            else:
                log.info("refresh rate on %s restored", output)
        self._refresh_saved.clear()
        self._refresh_active = False
        return ok

    def _enable_adaptive_sync_hyprland(self) -> bool:
        # misc:vrr is 0=off, 1=on, 2=fullscreen-only - compositor-wide, no
        # per-output equivalent to call here (see module docstring).
        self._vrr_saved_hyprland = _hyprctl_get_option("misc:vrr") or "0"
        if _hyprctl_set_option("misc:vrr", "1"):
            self._vrr_active = True
            log.info("Hyprland misc:vrr enabled (was %s)", self._vrr_saved_hyprland)
            return True
        self._vrr_saved_hyprland = None
        return False
