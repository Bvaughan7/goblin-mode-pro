"""Compositor / presentation handling.

On X11 the classic move is to suspend the compositor entirely. On the primary
target (KDE Plasma 6 / Wayland) KWin *cannot* disable compositing, so the
Wayland-appropriate equivalents are used instead:

* **Allow Tearing** (immediate presentation) enabled for the duration of the
  game via ``kwriteconfig6`` + a KWin reconfigure, restored on exit.
* **Adaptive Sync / VRR** switched to ``automatic`` on any capable output via
  ``kscreen-doctor``, restored on exit.

Other sessions:

* **KDE + X11**  -> real compositor suspend/resume over ``org.kde.KWin`` D-Bus.
* **GNOME / wlroots / unknown** -> no-op with a clear log line (rely on
  ``gamemoderun``, which the launch wrapper already uses).

Every operation is best-effort and never raises.
"""

from __future__ import annotations

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

_TEARING_GROUP = "Compositing"
_TEARING_KEY = "AllowTearing"

_VRR_VALUES = {"never", "automatic", "always"}


def _session_type() -> str:
    return os.environ.get("XDG_SESSION_TYPE", "").lower()


def _desktop() -> str:
    return os.environ.get("XDG_CURRENT_DESKTOP", "").upper()


def _is_kde() -> bool:
    return "KDE" in _desktop()


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
class Compositor:
    """Stateful presentation toggles with save/restore of prior values."""

    def __init__(self) -> None:
        self._tearing_saved: str | None = None
        self._tearing_active = False
        self._vrr_saved: dict[str, str] = {}
        self._vrr_active = False
        self._x11_suspended = False

    # -- capability --------------------------------------------------
    @property
    def tearing_supported(self) -> bool:
        if _session_type() == "wayland" and _is_kde():
            return all(shutil.which(t) for t in (_KWRITE, _KREAD, _QDBUS))
        if _session_type() == "x11" and _is_kde():
            return shutil.which(_QDBUS) is not None
        return False

    @property
    def adaptive_sync_supported(self) -> bool:
        return _is_kde() and shutil.which(_KSCREEN) is not None

    # -- tearing / compositor suspend ------------------------------
    def enable_tearing(self) -> bool:
        if self._tearing_active:
            return True
        if _session_type() == "x11" and _is_kde():
            return self._suspend_kwin_x11()
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
        ok = _write_allow_tearing(self._tearing_saved or "false")
        log.info("KWin AllowTearing restored to %s", self._tearing_saved or "false")
        self._tearing_active = False
        self._tearing_saved = None
        return ok

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
    def enable_adaptive_sync(self, policy: str = "automatic") -> bool:
        if self._vrr_active:
            return True
        if not self.adaptive_sync_supported:
            log.info("adaptive sync unsupported on this session - skipping")
            return False
        outputs = _vrr_outputs()
        if not outputs:
            log.info("no VRR-capable outputs - skipping adaptive sync")
            return False
        changed = False
        for name, current in outputs.items():
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
        ok = True
        for name, prev in self._vrr_saved.items():
            if not _set_vrr(name, prev):
                ok = False
            else:
                log.info("adaptive sync on %s restored to %s", name, prev)
        self._vrr_saved.clear()
        self._vrr_active = False
        return ok
