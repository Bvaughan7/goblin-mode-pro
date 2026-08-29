"""What this machine can actually do.

Goblin Mode Pro works with any CPU and (for the core tuning) any GPU - the CPU
governor, process priority, compositor tweaks, MangoHud, diagnostics, game
auto-detection, the system check and the log analyser are all vendor-neutral.

A few features are narrower and this module reports which apply here:

* ``rapl_control``  - raising the CPU power limit; Intel (``intel-rapl``) only.
* ``epp_control``   - the energy/performance hint; Intel P-state and AMD P-state
  in EPP mode.
* ``gpu.nvidia``    - the deep GPU snapshot on a frame-rate dip needs
  ``nvidia-smi``; AMD/Intel report a reduced set.

The result is attached to the daemon status so the GUI can label or hide the
parts that don't apply, instead of failing silently.
"""

from __future__ import annotations

import functools
import glob
import os
import shutil
from pathlib import Path

_CPU = Path("/sys/devices/system/cpu")


def _read(path: str | Path) -> str:
    try:
        return Path(path).read_text().strip()
    except OSError:
        return ""


def _cpu_vendor() -> str:
    blob = _read("/proc/cpuinfo").lower()
    if "genuineintel" in blob:
        return "intel"
    if "authenticamd" in blob:
        return "amd"
    return "other"


def _cpu_model() -> str:
    for line in _read("/proc/cpuinfo").splitlines():
        if line.lower().startswith("model name"):
            return line.split(":", 1)[1].strip()[:80]
    return ""


def _cpufreq_driver() -> str:
    return _read(_CPU / "cpu0/cpufreq/scaling_driver") or "none"


def _gpu_vendors() -> list[str]:
    out: set[str] = set()
    for card in glob.glob("/sys/class/drm/card[0-9]"):
        vendor = _read(Path(card) / "device/vendor").lower()
        out.add({
            "0x10de": "nvidia", "0x1002": "amd", "0x8086": "intel",
        }.get(vendor, "other"))
    if shutil.which("nvidia-smi"):
        out.add("nvidia")
    return sorted(v for v in out if v != "other") or ["unknown"]


def _compositor() -> str:
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").upper()
    session = os.environ.get("XDG_SESSION_TYPE", "")
    if "KDE" in desktop:
        return "kwin-wayland" if session == "wayland" else "kwin-x11"
    if "GNOME" in desktop:
        return "mutter-wayland" if session == "wayland" else "mutter-x11"
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        return "hyprland"
    if os.environ.get("SWAYSOCK"):
        return "sway"
    return session or "unknown"


def _package_manager() -> str | None:
    for pm in ("pacman", "apt-get", "dnf", "zypper", "xbps-install", "eopkg", "emerge"):
        if shutil.which(pm):
            return "apt" if pm == "apt-get" else pm
    return None


def _distro_id() -> str:
    for line in _read("/etc/os-release").splitlines():
        if line.startswith("ID="):
            return line.split("=", 1)[1].strip().strip('"')
    return ""


@functools.lru_cache(maxsize=1)
def detect() -> dict:
    driver = _cpufreq_driver()
    epp = bool(glob.glob(str(_CPU / "cpu0/cpufreq/energy_performance_preference")))
    rapl = Path("/sys/class/powercap/intel-rapl/intel-rapl:0/constraint_0_power_limit_uw").exists()
    governor = bool(glob.glob(str(_CPU / "cpu0/cpufreq/scaling_governor")))
    gpus = _gpu_vendors()

    return {
        "cpu_vendor": _cpu_vendor(),
        "cpu_model": _cpu_model(),
        "cpufreq_driver": driver,
        "governor_control": governor,
        "epp_control": epp,
        "rapl_control": rapl,
        "ryzenadj": shutil.which("ryzenadj") is not None,
        "tdp_control": "rapl" if rapl else ("ryzenadj" if shutil.which("ryzenadj") else None),
        "gpu_vendors": gpus,
        "nvidia_smi": shutil.which("nvidia-smi") is not None,
        "gpu_deep_stats": "nvidia" in gpus and shutil.which("nvidia-smi") is not None,
        "gamescope": shutil.which("gamescope") is not None,
        "gamemode": shutil.which("gamemoderun") is not None,
        "mangohud": shutil.which("mangohud") is not None,
        "compositor": _compositor(),
        "distro_id": _distro_id(),
        "package_manager": _package_manager(),
    }
