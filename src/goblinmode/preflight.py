"""Pre-flight system check.

A set of checks that answer *"is this machine game-ready?"* - the kernel knobs
and limits that make Linux games crash on launch, stutter, or leave performance
on the table. Each check reports a status and, where possible, offers a fix:

* a **runtime** fix (a ``sysctl`` the helper applies now), and/or
* a **persistent** snippet (an ``/etc/sysctl.d`` / kernel-cmdline change) shown
  for the user to review and install.

Design: data-driven. Add a :class:`Check` to ``CHECKS`` and it shows up in the
GUI with a Fix button automatically.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

OK, WARN, FAIL, INFO, UNKNOWN = "ok", "warn", "fail", "info", "unknown"

SYSCTL_DROPIN = "/etc/sysctl.d/99-goblin-mode-pro.conf"


@dataclass
class CheckResult:
    status: str
    value: str = ""
    detail: str = ""


@dataclass
class Check:
    id: str
    title: str
    why: str
    _run: "callable"
    sysctl: tuple[str, str] | None = None      # (key, desired) - runtime + drop-in fix
    kernel_param: str | None = None            # persistent boot-param fix
    fix_hint: str = ""                          # free-text remedy when not automatable
    severity: str = WARN                        # status to report on failure

    def run(self) -> CheckResult:
        try:
            return self._run()
        except Exception as exc:  # noqa: BLE001
            return CheckResult(UNKNOWN, "", f"check failed: {exc}")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _read(path: str) -> str | None:
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def _read_int(path: str) -> int | None:
    v = _read(path)
    try:
        return int(v) if v is not None else None
    except ValueError:
        return None


def _kernel_ver() -> tuple[int, int]:
    m = re.match(r"(\d+)\.(\d+)", platform.release())
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def _nofile_hard() -> int | None:
    try:
        import resource

        return resource.getrlimit(resource.RLIMIT_NOFILE)[1]
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------
# individual checks
# --------------------------------------------------------------------------
def _c_max_map_count() -> CheckResult:
    v = _read_int("/proc/sys/vm/max_map_count") or 0
    if v >= 1_048_576:
        return CheckResult(OK, str(v))
    return CheckResult(
        FAIL, str(v),
        "Unreal Engine 4/5 titles, Star Citizen and others crash on launch or "
        "mid-session without a high value. Recommended: 2147483642.",
    )


def _c_nofile() -> CheckResult:
    hard = _nofile_hard()
    if hard is None:
        return CheckResult(UNKNOWN)
    if hard >= 524_288:
        return CheckResult(OK, str(hard))
    return CheckResult(
        WARN, str(hard),
        "Wine/Proton esync opens many file descriptors; a low hard limit causes "
        "'esync: up to N handles' failures and crashes.",
    )


def _c_split_lock() -> CheckResult:
    v = _read("/proc/sys/kernel/split_lock_mitigate")
    if v is None:
        return CheckResult(INFO, "n/a", "This kernel doesn't expose the knob.")
    if v == "0":
        return CheckResult(OK, "off")
    return CheckResult(
        WARN, "on",
        "Split-lock mitigation stalls threads that do unaligned atomics - a known "
        "heavy-stutter source in RDR2, Elden Ring and a few others.",
    )


def _nvidia_driver_major() -> int | None:
    if not shutil.which("nvidia-smi"):
        return None
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=4,
        ).stdout.strip()
        return int(out.split(".")[0]) if out else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _c_nvidia_modeset() -> CheckResult:
    if not Path("/sys/module/nvidia_drm").exists():
        return CheckResult(INFO, "no nvidia_drm", "Not an NVIDIA proprietary setup.")
    v = _read("/sys/module/nvidia_drm/parameters/modeset")
    if v == "Y":
        return CheckResult(OK, "Y")
    if v is None:  # param is root-readable only on this driver
        drv = _nvidia_driver_major()
        if drv and drv >= 560 and os.environ.get("XDG_SESSION_TYPE") == "wayland":
            return CheckResult(
                OK, f"on (driver {drv}, Wayland session)",
                "modeset defaults on since driver 560 and your Wayland session "
                "confirms it.",
            )
        return CheckResult(
            INFO, "unreadable",
            "The parameter is root-only on this driver; add nvidia-drm.modeset=1 "
            "to be certain.",
        )
    return CheckResult(
        WARN, str(v),
        "nvidia-drm modeset must be on for Wayland and for explicit sync - without "
        "it you get flicker, tearing and worse frame pacing.",
    )


def _c_thp() -> CheckResult:
    raw = _read("/sys/kernel/mm/transparent_hugepage/enabled")
    if raw is None:
        return CheckResult(UNKNOWN)
    cur = re.search(r"\[(\w+)\]", raw)
    cur = cur.group(1) if cur else raw
    if cur == "madvise":
        return CheckResult(OK, cur)
    if cur == "always":
        return CheckResult(
            WARN, cur,
            "THP='always' can cause allocation-stall micro-stutter in games; "
            "'madvise' is the usual gaming choice.",
        )
    return CheckResult(INFO, cur, "THP disabled - fine, slightly less throughput.")


def _c_compaction() -> CheckResult:
    v = _read_int("/proc/sys/vm/compaction_proactiveness")
    if v is None:
        return CheckResult(UNKNOWN)
    if v <= 5:
        return CheckResult(OK, str(v))
    return CheckResult(
        WARN, str(v),
        "Proactive memory compaction (default 20) can introduce frame hitches; "
        "gaming kernels set it to 0.",
    )


def _c_swappiness() -> CheckResult:
    v = _read_int("/proc/sys/vm/swappiness")
    if v is None:
        return CheckResult(UNKNOWN)
    if v <= 20:
        return CheckResult(OK, str(v))
    return CheckResult(
        INFO, str(v),
        "High swappiness can page out game memory under pressure; 10 is a common "
        "gaming value.",
    )


def _c_fsync() -> CheckResult:
    maj, minr = _kernel_ver()
    if (maj, minr) >= (5, 16):
        return CheckResult(OK, f"{maj}.{minr}", "futex_waitv present - WINEFSYNC works.")
    return CheckResult(
        WARN, f"{maj}.{minr}",
        "Kernel < 5.16 has no futex_waitv; Proton fsync falls back to esync.",
    )


def _c_gamemode() -> CheckResult:
    if not shutil.which("gamemoded"):
        return CheckResult(
            WARN, "not installed",
            "feralinteractive gamemode gives per-game governor/priority/GPU tuning "
            "and is what most launchers expect.",
        )
    try:
        out = subprocess.run(["gamemoded", "-s"], capture_output=True, text=True, timeout=4).stdout
        active = "is active" in out
        return CheckResult(OK, "active" if active else "installed")
    except (OSError, subprocess.SubprocessError):
        return CheckResult(OK, "installed")


def _c_mangohud() -> CheckResult:
    return (
        CheckResult(OK, "installed") if shutil.which("mangohud")
        else CheckResult(WARN, "missing", "Needed for the FPS overlay and the frame-rate watchdog.")
    )


def _c_vulkan_icd() -> CheckResult:
    d = Path("/usr/share/vulkan/icd.d")
    icds = sorted(p.name for p in d.glob("*.json")) if d.exists() else []
    real = [i for i in icds if "nvidia" in i or "radeon" in i or "intel" in i or "lvp" in i]
    if not real:
        return CheckResult(WARN, "none found", "No Vulkan ICD - games won't start.")
    return CheckResult(OK, ", ".join(real))


def _c_userns() -> CheckResult:
    """Steam's pressure-vessel container and EAC/BattlEye under Proton need
    unprivileged user namespaces. Hardened kernels (some Debian/Ubuntu, a few
    security spins) ship them off, which breaks the Steam Linux Runtime and
    several anti-cheat games."""
    max_ns = _read_int("/proc/sys/user/max_user_namespaces")
    if max_ns is not None and max_ns <= 0:
        return CheckResult(
            FAIL, "0",
            "user.max_user_namespaces is 0 - the Steam Linux Runtime container "
            "and EAC/BattlEye games will fail to start.",
        )
    clone = _read("/proc/sys/kernel/unprivileged_userns_clone")
    if clone == "0":
        return CheckResult(
            FAIL, "disabled",
            "kernel.unprivileged_userns_clone is 0 - the Steam Linux Runtime "
            "and some anti-cheat games can't create their sandbox.",
        )
    if max_ns is not None:
        return CheckResult(OK, str(max_ns))
    return CheckResult(OK, "enabled" if clone in (None, "1") else str(clone))


def _c_anticheat() -> CheckResult:
    """Informational: what the machine needs for EAC / BattlEye games. Both work
    under Proton when the developer ships the Linux module - there is nothing to
    install, but the runtime container (see the user-namespaces check) must work
    and Proton Experimental / a recent Proton is safest."""
    return CheckResult(
        INFO, "Proton-native",
        "Easy Anti-Cheat and BattlEye run on Linux when the game ships the "
        "Linux module (most do now). If an anti-cheat game won't launch: use "
        "Proton Experimental, make sure the user-namespaces check above passes, "
        "and check protondb.com for game-specific notes.",
    )


def _c_swap() -> CheckResult:
    try:
        import psutil

        sm = psutil.swap_memory()
        if sm.total > 0:
            return CheckResult(OK, f"{sm.total // (1024**3)} GB")
    except Exception:  # noqa: BLE001
        pass
    return CheckResult(
        INFO, "none",
        "No swap or zram - a memory-hungry game that spikes can be OOM-killed.",
    )


CHECKS: list[Check] = [
    Check("max_map_count", "vm.max_map_count", "UE5 / Star Citizen crash guard",
          _c_max_map_count, sysctl=("vm.max_map_count", "2147483642"), severity=FAIL),
    Check("nofile", "Open-file limit (esync)", "Wine esync handle ceiling",
          _c_nofile, fix_hint="Raise DefaultLimitNOFILE in /etc/systemd/system.conf.d/ "
          "and hard nofile in /etc/security/limits.d/ to 524288."),
    Check("split_lock", "Split-lock mitigation", "heavy-stutter source in some titles",
          _c_split_lock, sysctl=("kernel.split_lock_mitigate", "0"),
          kernel_param="split_lock_detect=off"),
    Check("nvidia_modeset", "nvidia-drm modeset", "Wayland + explicit sync",
          _c_nvidia_modeset, kernel_param="nvidia-drm.modeset=1",
          fix_hint="Also add 'options nvidia_drm modeset=1 fbdev=1' to /etc/modprobe.d/."),
    Check("thp", "Transparent hugepages", "allocation-stall stutter",
          _c_thp, fix_hint="echo madvise > /sys/kernel/mm/transparent_hugepage/enabled "
          "(persist via a systemd tmpfiles rule or kernel arg)."),
    Check("compaction", "vm.compaction_proactiveness", "frame hitches from memory compaction",
          _c_compaction, sysctl=("vm.compaction_proactiveness", "0")),
    Check("swappiness", "vm.swappiness", "paging out game memory",
          _c_swappiness, sysctl=("vm.swappiness", "10"), severity=INFO),
    Check("fsync", "Kernel fsync support", "WINEFSYNC vs esync fallback",
          _c_fsync, fix_hint="Update to a kernel >= 5.16 (CachyOS ships current)."),
    Check("gamemode", "feralinteractive gamemode", "per-game tuning launchers expect",
          _c_gamemode, fix_hint="Install the 'gamemode' package."),
    Check("mangohud", "MangoHud", "overlay + frame-rate watchdog",
          _c_mangohud, fix_hint="Install the 'mangohud' package."),
    Check("vulkan_icd", "Vulkan driver (ICD)", "no ICD = no game",
          _c_vulkan_icd, fix_hint="Install the vulkan driver for your GPU."),
    Check("userns", "User namespaces", "Steam Runtime container + anti-cheat",
          _c_userns, sysctl=("user.max_user_namespaces", "28633"), severity=FAIL,
          fix_hint="On Debian/Ubuntu also: sysctl kernel.unprivileged_userns_clone=1"),
    Check("anticheat", "Anti-cheat (EAC / BattlEye)", "how anti-cheat games run on Linux",
          _c_anticheat, severity=INFO,
          fix_hint="Nothing to install - set the game to Proton Experimental if it won't launch."),
    Check("swap", "Swap / zram", "OOM protection for RAM spikes",
          _c_swap, severity=INFO,
          fix_hint="Enable zram (e.g. the 'zram-generator' package)."),
]


def run_all() -> list[dict]:
    out = []
    for chk in CHECKS:
        r = chk.run()
        status = r.status
        if status == FAIL and chk.severity == WARN:
            status = WARN
        if status == WARN and chk.severity == INFO:
            status = INFO
        out.append({
            "id": chk.id, "title": chk.title, "why": chk.why,
            "status": status, "value": r.value, "detail": r.detail,
            "sysctl": list(chk.sysctl) if chk.sysctl else None,
            "kernel_param": chk.kernel_param,
            "fix_hint": chk.fix_hint,
        })
    return out


def summary(results: list[dict]) -> dict:
    n = {OK: 0, WARN: 0, FAIL: 0, INFO: 0, UNKNOWN: 0}
    for r in results:
        n[r["status"]] = n.get(r["status"], 0) + 1
    return n


def sysctl_dropin_text(results: list[dict] | None = None) -> str:
    """The /etc/sysctl.d snippet that fixes every sysctl-fixable failing check."""
    results = results or run_all()
    lines = ["# Installed by Goblin Mode Pro - pre-flight fixes", ""]
    for r in results:
        if r["sysctl"] and r["status"] in (WARN, FAIL):
            lines.append(f"{r['sysctl'][0]} = {r['sysctl'][1]}")
    return "\n".join(lines) + "\n"


def pending_sysctls(results: list[dict] | None = None) -> list[tuple[str, str]]:
    results = results or run_all()
    return [tuple(r["sysctl"]) for r in results if r["sysctl"] and r["status"] in (WARN, FAIL)]
