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
import platform
import re
import shutil
from pathlib import Path

_CPU = Path("/sys/devices/system/cpu")
_DMI = Path("/sys/class/dmi/id")


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


def _parse_cpu_list(spec: str) -> list[int]:
    """Expand a Linux cpu-list ("0-3,8,10-11") into a sorted list of ints."""
    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            try:
                out.update(range(int(a), int(b) + 1))
            except ValueError:
                continue
        else:
            try:
                out.add(int(part))
            except ValueError:
                continue
    return sorted(out)


def _online_cpus() -> list[int]:
    spec = _read(_CPU / "online")
    return _parse_cpu_list(spec) if spec else sorted(
        int(p.name[3:]) for p in _CPU.glob("cpu[0-9]*") if p.name[3:].isdigit()
    )


def _core_layout() -> dict:
    """Describe useful ways to pin a game's threads on this CPU.

    * ``performance`` - the fast cores on a hybrid CPU (Intel P-cores, or a
      big.LITTLE arrangement). ``None`` when every core is the same.
    * ``cache_groups`` - cores that share an L3 slice; on Ryzen that is one
      CCD, so pinning to the first group keeps a game off the cross-CCD
      latency penalty. Omitted when there is only one group.
    """
    online = _online_cpus()
    layout: dict = {"online": online}

    # hybrid: prefer the kernel's own classification, fall back to max-freq.
    core_mask = _read(_CPU / "types/intel_core/cpumap") or _read(_CPU / "types/intel_core/cpus")
    if core_mask:
        pcores = _parse_cpu_list(core_mask)
    else:
        freqs = {}
        for c in online:
            f = _read(_CPU / f"cpu{c}/cpufreq/cpuinfo_max_freq")
            if f and f.isdigit():
                freqs[c] = int(f)
        if freqs and len(set(freqs.values())) > 1:
            top = max(freqs.values())
            pcores = sorted(c for c, f in freqs.items() if f >= top * 0.92)
        else:
            pcores = []
    if pcores and 0 < len(pcores) < len(online):
        layout["performance"] = pcores

    # L3 cache domains (CCDs on Ryzen)
    groups: list[list[int]] = []
    for c in online:
        lst = _read(_CPU / f"cpu{c}/cache/index3/shared_cpu_list")
        if not lst:
            continue
        members = _parse_cpu_list(lst)
        if members and members not in groups:
            groups.append(members)
    if len(groups) > 1:
        layout["cache_groups"] = groups
    return layout


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


def _kernel_flavor() -> str:
    """A rough classification of the running kernel: gaming-oriented builds get
    named, everything else is 'generic'. Used for a gentle upgrade nudge only."""
    rel = platform.release().lower()
    for tag in ("cachyos", "xanmod", "liquorix", "lqx", "zen", "tkg",
                "nobara", "bazzite", "clear", "xero"):
        if tag in rel:
            return "lqx" if tag == "liquorix" else tag
    if "-lts" in rel or rel.endswith("-lts"):
        return "lts"
    return "generic"


def _handheld() -> str | None:
    """Steam Deck / ROG Ally / Legion Go / other known handhelds, from DMI."""
    board = (_read(_DMI / "product_name") + " " + _read(_DMI / "board_name") + " "
             + _read(_DMI / "sys_vendor")).lower()
    if "jupiter" in board or "galileo" in board or "valve" in board and "steam" in board:
        return "steamdeck"
    if "rog ally" in board or "rc71" in board or "rc72" in board:
        return "rog_ally"
    if "83e1" in board or "legion go" in board:
        return "legion_go"
    if "aokzoe" in board or "onexplayer" in board or "aya neo" in board or "ayaneo" in board:
        return "other_handheld"
    return None


def _session_recorder() -> str | None:
    for tool in ("gpu-screen-recorder", "wf-recorder", "obs", "spectacle"):
        if shutil.which(tool):
            return tool
    return None


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
        "core_layout": _core_layout(),
        "kernel_release": platform.release(),
        "kernel_flavor": _kernel_flavor(),
        "handheld": _handheld(),
        "undervolt": "intel-undervolt" if (
            _cpu_vendor() == "intel" and shutil.which("intel-undervolt")) else None,
        "session_recorder": _session_recorder(),
        "vkbasalt": shutil.which("vkBasalt") is not None or Path(
            "/usr/share/vulkan/implicit_layer.d/vkBasalt.json").exists(),
    }


# --------------------------------------------------------------------------
# dynamic probes (not cached - state can change while the daemon runs)
# --------------------------------------------------------------------------
_PAD_RE = re.compile(r"gamepad|controller|x-?box|dualshock|dualsense|joy-?con|"
                     r"joystick|steam ?(deck )?controller|ally|8bitdo", re.I)


def controllers() -> list[str]:
    """Connected game-controller names, from /proc/bus/input/devices."""
    blob = _read("/proc/bus/input/devices")
    if not blob:
        return []
    out: list[str] = []
    for block in blob.split("\n\n"):
        m = re.search(r'N: Name="([^"]+)"', block)
        if not m:
            continue
        name = m.group(1)
        handlers = re.search(r"H: Handlers=([^\n]+)", block)
        # a kernel joystick handler (jsN) is the reliable "this is a pad" signal
        is_js = bool(handlers and re.search(r"\bjs\d", handlers.group(1)))
        if (is_js or _PAD_RE.search(name)) and name not in out:
            out.append(name)
    return out


def gamemode_status() -> dict:
    """What feralinteractive gamemode reports it is doing right now."""
    if not shutil.which("gamemoded"):
        return {"installed": False}
    import subprocess
    try:
        out = subprocess.run(["gamemoded", "-s"], capture_output=True, text=True,
                             timeout=4).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return {"installed": True, "active": None}
    return {"installed": True, "active": "is active" in out, "detail": out[:200]}
