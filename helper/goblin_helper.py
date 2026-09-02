#!/usr/bin/env python3
"""Goblin Mode Pro - privileged helper.

Runs as root under ``goblin-mode-pro-helper.service`` and owns the system D-Bus
name ``com.goblinmode.ProHelper``. It is deliberately small: it performs *only*
the handful of root-only operations the unprivileged daemon cannot do.

Every mutating call is authorised through polkit before it runs -
``com.goblinmode.pro.manage-performance`` for the runtime gaming knobs (governor,
EPP, renice, RAPL limits) and ``com.goblinmode.pro.manage-kernel-tunables`` for
the persistent sysctls set from the pre-flight check.

Design notes
------------
* Standard library + PyGObject (Gio/GLib) only - no third-party imports, so the
  helper keeps working even if the calling Python environment is broken.
* Inputs are constrained at every entry point: the governor must be one the
  kernel advertises; ``renice`` only raises priority and only for a process the
  caller owns; RAPL writes are clamped to the firmware maximum; sysctl keys are
  a fixed allowlist with per-key numeric ranges.
* Before the first mutation the current governor / EPP / RAPL limits are
  snapshotted to ``/run/goblin-mode-pro/state.json`` (tmpfs, root-only), so
  ``RevertAll`` (and ``--revert`` on service stop) restores the machine even
  after a helper restart.
"""

from __future__ import annotations

import contextlib
import glob
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

#: The helper is installed standalone and never imports the goblinmode
#: package, so it cannot read src/goblinmode/__about__.py at runtime. This is a
#: sixth place the release process has to bump, and
#: tests/test_packaging_versions.py fails if it drifts.
HELPER_VERSION = "1.3.2"

#: The frozen D-Bus contract's version. This stays 1 for the whole Python-to-Rust
#: conversion. If it ever needs bumping, the interface freeze has failed and THAT
#: is the thing to fix - a second version would mean callers have to care which
#: implementation answered, which is the exact property the freeze exists to deny.
INTERFACE_VERSION = 1

#: Which implementation is answering. For bug reports ONLY. Nothing may branch on
#: it: the moment behaviour depends on this, the two helpers are no longer
#: interchangeable and the frozen interface is a fiction.
IMPLEMENTATION = "python"

BUS_NAME = "com.goblinmode.ProHelper"
OBJECT_PATH = "/com/goblinmode/ProHelper"
IFACE = "com.goblinmode.ProHelper.Manager"
POLKIT_PERF = "com.goblinmode.pro.manage-performance"
POLKIT_KERNEL = "com.goblinmode.pro.manage-kernel-tunables"
POLKIT_THERMAL = "com.goblinmode.pro.manage-hardware-thermal"

STATE_DIR = Path("/run/goblin-mode-pro")
STATE_FILE = STATE_DIR / "state.json"

CPU_BASE = Path("/sys/devices/system/cpu")
RAPL_BASE = Path("/sys/class/powercap/intel-rapl/intel-rapl:0")

NICE_FLOOR = -10  # never let a caller push a process below this

#: AMD laptop TDP control. ``ryzenadj`` writes the APU's SMU power limits; it is
#: the AMD counterpart to Intel RAPL. Absent on most systems - the methods below
#: no-op when it isn't installed.
RYZENADJ = shutil.which("ryzenadj")
TDP_MIN_W, TDP_MAX_W = 4, 120

#: We never set undervolt offsets ourselves (getting them wrong crashes the
#: machine). We only re-trigger the offsets the user configured in
#: /etc/intel-undervolt.conf - suspend / thermald can reset them mid-session.
INTEL_UNDERVOLT = shutil.which("intel-undervolt")

#: AMD Curve Optimizer, same "we never choose the values" rule as Intel above.
#: ryzenadj has no native config-file concept of its own, so this is a small
#: GMP-specific one the user writes themselves: lines are `coall=<offset>` or
#: `coper<N>=<offset>` (per-core, N = core index), each a negative integer
#: (more negative = more aggressive undervolt). Re-applied on demand because
#: suspend resets the SMU state, same as Intel's offsets.
AMD_UNDERVOLT_CONF = Path("/etc/goblin-mode-pro/amd-undervolt.conf")
_AMD_UV_LINE = re.compile(r"^\s*(coall|coper(\d+))\s*=\s*(-?\d+)\s*$")
_AMD_UV_RANGE = (-30, 0)  # the range every ryzenadj curve-optimizer guide uses

#: nvidia-drm.modeset is a boot-time modprobe option - there is no runtime
#: write path, only a persistent modprobe.d config + a reboot. This file's
#: entire content is one of two fixed strings; nothing else is ever written
#: to it.
NVIDIA_MODESET_CONF = Path("/etc/modprobe.d/goblin-mode-pro-nvidia.conf")

#: Preemptive fan spin-up on launch. Most laptops/handhelds don't expose a
#: writable hwmon pwm control at all (the EC/BIOS owns the fan curve) - this
#: is best-effort and no-ops cleanly wherever that's the case, which is most
#: systems. Where it *is* exposed, hwmon's own convention is what's used:
#: pwmN_enable=1 switches a channel to manual, pwmN is a 0-255 duty cycle.
FAN_STATE_FILE = STATE_DIR / "fans.json"
_HWMON_BASE = Path("/sys/class/hwmon")

#: SpinUpFans only ever spins fans *up* (preemptive cooling on launch). There is
#: no legitimate reason to drive a duty cycle low through it - doing so would
#: switch a channel out of EC/automatic control and *reduce* cooling, which is a
#: hardware-damage vector, not a feature. Anything below this is refused.
MIN_FAN_PERCENT = 40

# sysctl keys the pre-flight check is allowed to set at runtime, each with an
# accepted numeric range. Nothing outside this table can be touched.
SYSCTL_ALLOW: dict[str, tuple[int, int]] = {
    "vm.max_map_count": (65530, 2147483642),
    "vm.swappiness": (0, 200),
    "vm.compaction_proactiveness": (0, 100),
    "kernel.split_lock_mitigate": (0, 1),
    "user.max_user_namespaces": (0, 2147483647),
    # Debian/Ubuntu downstream knob (absent on mainline kernels). The
    # pre-flight check only offers it when /proc/sys/kernel/... exists.
    "kernel.unprivileged_userns_clone": (0, 1),
}

#: Every /proc/sys and /sys subtree the helper needs write access to, paired
#: with what needs it. The unit-file test asserts each parent is covered by a
#: ReadWritePaths= entry in goblin-mode-pro-helper.service - see
#: tests/test_helper_sandbox.py.
#: Every Linux capability the helper needs, paired with what needs it. Being
#: root is not sufficient on its own: the unit drops all capabilities except
#: these, and a missing one fails at the syscall - as EACCES, which looks
#: nothing like a sandbox error and is why this went unnoticed. The unit-file
#: test asserts CapabilityBoundingSet= matches this table exactly - see
#: tests/test_helper_sandbox.py.
HELPER_CAPABILITIES: dict[str, str] = {
    # setpriority() on a process owned by another user
    "CAP_SYS_NICE": "Renice",
    # /proc/sys/user/* writes are gated on CAP_SYS_RESOURCE in the owning user
    # namespace (set_permissions() in kernel/ucount.c drops the write bit from
    # the effective mode without it), so root alone gets EACCES.
    "CAP_SYS_RESOURCE": "SetSysctl(user.max_user_namespaces)",
}

SYSFS_WRITE_ROOTS: tuple[str, ...] = (
    "/sys/devices/system/cpu",   # SetGovernor, SetEPP
    "/sys/class/powercap",       # SetPowerLimits, ResetPowerLimits
    "/sys/class/hwmon",          # SpinUpFans, ResetFans
    "/proc/sys/vm",              # vm.* sysctls
    "/proc/sys/kernel",          # kernel.* sysctls
    "/proc/sys/user",            # user.max_user_namespaces
    "/etc/modprobe.d",           # SetNvidiaModeset
)

logging.basicConfig(
    level=logging.INFO, format="goblin-helper: %(levelname)s %(message)s"
)
log = logging.getLogger("goblin-helper")

INTROSPECTION_XML = f"""
<node>
  <interface name="{IFACE}">
    <method name="GetGovernor">
      <arg type="s" name="governor" direction="out"/>
    </method>
    <method name="SetGovernor">
      <arg type="s" name="governor" direction="in"/>
      <arg type="b" name="ok" direction="out"/>
    </method>
    <method name="SetEPP">
      <arg type="s" name="epp" direction="in"/>
      <arg type="b" name="ok" direction="out"/>
    </method>
    <method name="Renice">
      <arg type="u" name="pid" direction="in"/>
      <arg type="i" name="nice" direction="in"/>
      <arg type="b" name="ok" direction="out"/>
    </method>
    <method name="GetPowerLimits">
      <arg type="t" name="pl1_uw" direction="out"/>
      <arg type="t" name="pl2_uw" direction="out"/>
    </method>
    <method name="SetPowerLimits">
      <arg type="t" name="pl1_uw" direction="in"/>
      <arg type="t" name="pl2_uw" direction="in"/>
      <arg type="b" name="ok" direction="out"/>
    </method>
    <method name="ResetPowerLimits">
      <arg type="b" name="ok" direction="out"/>
    </method>
    <method name="SetTDP">
      <arg type="u" name="watts" direction="in"/>
      <arg type="b" name="ok" direction="out"/>
    </method>
    <method name="ResetTDP">
      <arg type="b" name="ok" direction="out"/>
    </method>
    <method name="HasTDPControl">
      <arg type="b" name="available" direction="out"/>
    </method>
    <method name="RevertAll">
      <arg type="b" name="ok" direction="out"/>
    </method>
    <method name="SetSysctl">
      <arg type="s" name="key" direction="in"/>
      <arg type="s" name="value" direction="in"/>
      <arg type="b" name="ok" direction="out"/>
    </method>
    <method name="RevertSysctl">
      <arg type="s" name="key" direction="in"/>
      <arg type="b" name="ok" direction="out"/>
    </method>
    <method name="ApplyUndervolt">
      <arg type="b" name="ok" direction="out"/>
    </method>
    <method name="ReadUndervolt">
      <arg type="s" name="text" direction="out"/>
    </method>
    <method name="ApplyAmdUndervolt">
      <arg type="b" name="ok" direction="out"/>
    </method>
    <method name="SetNvidiaModeset">
      <arg type="b" name="enabled" direction="in"/>
      <arg type="b" name="ok" direction="out"/>
    </method>
    <method name="SpinUpFans">
      <arg type="u" name="percent" direction="in"/>
      <arg type="b" name="ok" direction="out"/>
    </method>
    <method name="ResetFans">
      <arg type="b" name="ok" direction="out"/>
    </method>
    <property name="Implementation" type="s" access="read"/>
    <property name="InterfaceVersion" type="u" access="read"/>
    <property name="Version" type="s" access="read"/>
  </interface>
</node>
"""

# --------------------------------------------------------------------------
# Authorization routing - which methods change the machine, and what each one
# costs the caller. Deliberately next to the interface XML above: between the
# two, the entire privileged surface of this program - every method that
# exists, every method that mutates, and the polkit action each requires - is
# visible before a single implementation. That is the order somebody auditing
# this file before installing it wants to read it in.
# --------------------------------------------------------------------------
_MUTATING = {
    "SetGovernor",
    "SetEPP",
    "Renice",
    "SetPowerLimits",
    "ResetPowerLimits",
    "SetTDP",
    "ResetTDP",
    "RevertAll",
    "SetSysctl",
    "RevertSysctl",
    "ApplyUndervolt",
    "ApplyAmdUndervolt",
    "SetNvidiaModeset",
    "SpinUpFans",
    "ResetFans",
}


#: methods gated behind the stricter "persistent system config" polkit
#: action - everything else in _MUTATING uses POLKIT_PERF.
_KERNEL_ACTION_METHODS = {"SetSysctl", "RevertSysctl", "SetNvidiaModeset"}

#: Switching a fan channel out of EC control is a thermal-safety operation and
#: persists after the caller dies, so it prompts (its own action rather than
#: the sysctl one - a user may reasonably allow fan spin-up but not sysctl
#: writes, or vice versa). ResetFans deliberately stays on POLKIT_PERF:
#: handing control back to the EC must always be possible without a prompt.
_THERMAL_ACTION_METHODS = {"SpinUpFans"}


def _polkit_action_for(method_name: str) -> str:
    if method_name in _KERNEL_ACTION_METHODS:
        return POLKIT_KERNEL
    if method_name in _THERMAL_ACTION_METHODS:
        return POLKIT_THERMAL
    return POLKIT_PERF



# --------------------------------------------------------------------------
# CPU - governor, EPP, and process priority
# --------------------------------------------------------------------------
def _cpu_governor_paths() -> list[Path]:
    return [
        Path(p)
        for p in sorted(glob.glob(str(CPU_BASE / "cpu[0-9]*" / "cpufreq" / "scaling_governor")))
    ]


def _cpu_epp_paths() -> list[Path]:
    return [
        Path(p)
        for p in sorted(
            glob.glob(str(CPU_BASE / "cpu[0-9]*" / "cpufreq" / "energy_performance_preference"))
        )
    ]


def _read(path: Path) -> str:
    return path.read_text().strip()


def _write(path: Path, value: str) -> None:
    with open(path, "w") as fh:
        fh.write(value)


def _available_governors() -> set[str]:
    p = CPU_BASE / "cpu0" / "cpufreq" / "scaling_available_governors"
    try:
        return set(_read(p).split())
    except OSError:
        return set()


def get_governor() -> str:
    paths = _cpu_governor_paths()
    return _read(paths[0]) if paths else ""


def set_governor(governor: str) -> bool:
    if governor not in _available_governors():
        raise ValueError(f"unsupported governor: {governor!r}")
    _snapshot()
    ok = True
    for path in _cpu_governor_paths():
        try:
            _write(path, governor)
        except OSError as exc:
            log.warning("governor write failed for %s: %s", path, exc)
            ok = False
    return ok


#: EPP values to accept when the kernel doesn't advertise a list (the standard
#: intel_pstate / amd_pstate set).
_EPP_FALLBACK = {
    "default", "performance", "balance_performance", "balance_power", "power",
}


def _available_epps() -> set[str]:
    p = CPU_BASE / "cpu0" / "cpufreq" / "energy_performance_available_preferences"
    try:
        return set(_read(p).split())
    except OSError:
        return set()


def set_epp(epp: str) -> bool:
    """Set the energy/performance preference on every core.

    Return semantics: all-must-succeed - True only if every writable EPP file
    accepted the value, matching ``set_governor``. A partial write (some cores
    took it, others errored) returns False so the caller isn't told the box is
    in a state it isn't.
    """
    if epp not in (_available_epps() or _EPP_FALLBACK):
        raise ValueError(f"unsupported EPP: {epp!r}")
    _snapshot()
    paths = _cpu_epp_paths()
    ok = bool(paths)
    for path in paths:
        try:
            _write(path, epp)
        except OSError as exc:
            log.warning("EPP write failed for %s: %s", path, exc)
            ok = False
    return ok


def renice(pid: int, nice: int, caller_uid: int | None = None) -> bool:
    """Raise a process's scheduling priority.

    Ownership: the target must be owned by ``caller_uid``. ``caller_uid == 0``
    (an explicit root caller) is the *only* thing that skips the check - a
    ``None`` uid means "the bus lookup failed" and is treated as untrusted,
    never as root (fail closed).

    PID-reuse: the pid is pinned with a pidfd, ownership is re-checked *after*
    the pidfd is open, and liveness is confirmed with a null signal before the
    setpriority call, so a pid recycled mid-request can't slip a different
    process past the ownership gate.
    """
    pid = int(pid)
    if pid <= 1:
        raise ValueError(f"no such process: {pid}")
    nice = max(NICE_FLOOR, min(19, int(nice)))
    enforce_owner = caller_uid != 0

    pidfd: int | None = None
    try:
        pidfd = os.pidfd_open(pid)
    except (OSError, AttributeError):
        pidfd = None  # pre-5.3 kernel / unsupported - fall back to the plain path

    try:
        try:
            owner = os.stat(f"/proc/{pid}").st_uid
        except OSError as exc:
            raise ValueError(f"no such process: {pid}") from exc
        if enforce_owner and owner != caller_uid:
            raise PermissionError(
                f"process {pid} is not owned by uid {caller_uid}"
            )
        _pidfd_alive = getattr(signal, "pidfd_send_signal", None)
        if pidfd is not None and _pidfd_alive is not None:
            try:
                _pidfd_alive(pidfd, 0)  # confirm it's the same, live task
            except OSError as exc:
                raise ValueError(f"process {pid} went away: {exc}") from exc

        os.setpriority(os.PRIO_PROCESS, pid, nice)
        try:
            for tid in os.listdir(f"/proc/{pid}/task"):
                with contextlib.suppress(OSError, ValueError):
                    os.setpriority(os.PRIO_PROCESS, int(tid), nice)
        except OSError:
            pass
        return True
    finally:
        if pidfd is not None:
            os.close(pidfd)


# --------------------------------------------------------------------------
# Kernel tunables (sysctl)
# Only keys in SYSCTL_ALLOW, only within their stated range, and every
# write is snapshotted first so RevertSysctl can put it back.
# --------------------------------------------------------------------------

def set_sysctl(key: str, value: str) -> bool:
    rng = SYSCTL_ALLOW.get(key)
    if rng is None:
        raise ValueError(f"sysctl not in allowlist: {key}")
    try:
        num = int(str(value).strip())
    except ValueError:
        raise ValueError(f"non-numeric sysctl value: {value!r}") from None
    if not rng[0] <= num <= rng[1]:
        raise ValueError(f"{key}={num} out of range {rng}")
    path = (Path("/proc/sys") / key.replace(".", "/")).resolve()
    if not str(path).startswith("/proc/sys/") or not path.is_file():
        raise ValueError(f"refusing to write {path}")
    _snapshot_sysctl(key, path)          # remember the pre-change value so it's undoable
    _write(path, str(num))
    log.info("sysctl %s = %s", key, num)
    return True


def _sysctl_state_file() -> Path:
    return STATE_DIR / "sysctls.json"


def _snapshot_sysctl(key: str, path: Path) -> None:
    f = _sysctl_state_file()
    try:
        data = json.loads(f.read_text()) if f.exists() else {}
    except (OSError, json.JSONDecodeError):
        data = {}
    if key in data:
        return
    try:
        data[key] = _read(path)
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(data, indent=2))
    except OSError as exc:
        log.warning("could not snapshot sysctl %s: %s", key, exc)


# --------------------------------------------------------------------------
# Undervolt re-apply (Intel intel-undervolt / AMD ryzenadj)
# Re-runs the user's own configuration. This code NEVER chooses an
# undervolt value - it only re-applies one already written to a config
# file by the user, because suspend and thermald silently drop them.
# --------------------------------------------------------------------------

def apply_undervolt() -> bool:
    """Re-apply the offsets the user has in /etc/intel-undervolt.conf. We never
    choose the values - this just runs `intel-undervolt apply`."""
    if not INTEL_UNDERVOLT:
        return False
    try:
        subprocess.run([INTEL_UNDERVOLT, "apply"], capture_output=True,
                       text=True, timeout=10, check=True)
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("intel-undervolt apply failed: %s", exc)
        return False
    log.info("re-applied intel-undervolt offsets")
    return True


def read_undervolt() -> str:
    if not INTEL_UNDERVOLT:
        return ""
    try:
        return subprocess.run([INTEL_UNDERVOLT, "read"], capture_output=True,
                              text=True, timeout=10).stdout[:2000]
    except (OSError, subprocess.SubprocessError):
        return ""


def _parse_amd_undervolt_conf() -> dict[str, int]:
    """``{"coall": -15, "coper0": -20, ...}`` from AMD_UNDERVOLT_CONF, each
    clamped to _AMD_UV_RANGE. Malformed or out-of-range lines are skipped,
    not fatal - this file is user-edited by hand."""
    try:
        text = AMD_UNDERVOLT_CONF.read_text()
    except OSError:
        return {}
    out: dict[str, int] = {}
    lo, hi = _AMD_UV_RANGE
    for line in text.splitlines():
        line = line.split("#", 1)[0]
        m = _AMD_UV_LINE.match(line)
        if not m:
            continue
        key, offset = m.group(1), int(m.group(3))
        if lo <= offset <= hi:
            out[key] = offset
        else:
            log.warning("amd-undervolt.conf: %s=%d out of range %s, skipping", key, offset, _AMD_UV_RANGE)
    return out


def apply_amd_undervolt() -> bool:
    """Re-apply the Curve Optimizer offsets from AMD_UNDERVOLT_CONF. We never
    choose the values - this only re-runs what the user already wrote there
    (suspend resets the SMU state, same reason the Intel path re-applies)."""
    if not RYZENADJ:
        return False
    offsets = _parse_amd_undervolt_conf()
    if not offsets:
        log.info("apply_amd_undervolt: %s has no valid offsets, nothing to do",
                 AMD_UNDERVOLT_CONF)
        return False
    args = []
    for key, offset in offsets.items():
        if key == "coall":
            args.append(f"--set-coall={offset}")
        else:
            core = key[len("coper"):]
            args.append(f"--set-coper={core},{offset}")
    try:
        _ryzenadj(*args)
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("ryzenadj curve-optimizer apply failed: %s", exc)
        return False
    log.info("re-applied AMD Curve Optimizer offsets: %s", offsets)
    return True


# --------------------------------------------------------------------------
# Thermal - fan control
# SpinUpFans can only ever *raise* cooling: MIN_FAN_PERCENT is a floor,
# not a target. It has its own polkit action because it takes a fan off
# the EC curve and outlives the caller; ResetFans deliberately does not,
# so handing control back is always possible without a prompt.
# --------------------------------------------------------------------------

def _pwm_controls() -> list[Path]:
    """Every hwmon pwmN file that looks controllable - has the standard
    adjacent pwmN_enable (mode switch: 1=manual, usually 2=automatic)."""
    out = []
    try:
        hwmons = sorted(_HWMON_BASE.glob("hwmon*"))
    except OSError:
        return out
    for hwmon in hwmons:
        for pwm in sorted(hwmon.glob("pwm[0-9]*")):
            if re.fullmatch(r"pwm\d+", pwm.name) and (hwmon / f"{pwm.name}_enable").exists():
                out.append(pwm)
    return out


def spin_up_fans(percent: int) -> bool:
    """Best-effort burst: switch every writable pwm control to manual and set
    it to ``percent``% duty, snapshotting the prior enable/duty values first
    (mirrors the TDP snapshot/revert pattern) so ResetFans / RevertAll can
    put it back exactly as found. Returns False (not an error) wherever the
    EC exposes no writable pwm control at all - most systems."""
    percent = int(percent)
    if percent < MIN_FAN_PERCENT:
        raise ValueError(
            f"fan duty {percent}% is below the {MIN_FAN_PERCENT}% floor - "
            f"SpinUpFans only increases cooling, it never drives fans down"
        )
    percent = min(100, percent)
    pwms = _pwm_controls()
    if not pwms:
        return False
    duty = round(percent / 100 * 255)

    if not FAN_STATE_FILE.exists():
        snapshot = {}
        for pwm in pwms:
            try:
                snapshot[str(pwm)] = {
                    "enable": _read(pwm.with_name(f"{pwm.name}_enable")),
                    "pwm": _read(pwm),
                }
            except OSError:
                continue
        try:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            FAN_STATE_FILE.write_text(json.dumps(snapshot, indent=2))
        except OSError as exc:
            log.warning("could not snapshot fan state: %s", exc)

    ok = False
    for pwm in pwms:
        try:
            _write(pwm.with_name(f"{pwm.name}_enable"), "1")
            _write(pwm, str(duty))
            ok = True
        except OSError as exc:
            log.warning("fan spin-up write failed for %s: %s", pwm, exc)
    if ok:
        log.info("fans set to %d%% duty on %d control(s)", percent, len(pwms))
    return ok


def reset_fans() -> bool:
    """Restore every pwm control this session touched to its snapshotted
    enable/duty values (usually: back to automatic/EC control)."""
    if not FAN_STATE_FILE.exists():
        return True
    try:
        snapshot = json.loads(FAN_STATE_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        FAN_STATE_FILE.unlink(missing_ok=True)
        return True
    ok = True
    for path_str, saved in snapshot.items():
        pwm = Path(path_str)
        try:
            if "pwm" in saved:
                _write(pwm, str(saved["pwm"]))
            if "enable" in saved:
                _write(pwm.with_name(f"{pwm.name}_enable"), str(saved["enable"]))
        except OSError as exc:
            log.warning("fan reset failed for %s: %s", pwm, exc)
            ok = False
    FAN_STATE_FILE.unlink(missing_ok=True)
    log.info("fan control reset (ok=%s)", ok)
    return ok


# --------------------------------------------------------------------------
# NVIDIA - nvidia_drm.modeset
# Writes a modprobe.d drop-in. Takes effect on the next boot, never now.
# --------------------------------------------------------------------------

def set_nvidia_modeset(enabled: bool) -> bool:
    """Write /etc/modprobe.d/goblin-mode-pro-nvidia.conf with a fixed
    ``options nvidia_drm modeset=0|1`` line - never arbitrary content, always
    exactly one of these two strings. Takes effect after a reboot (or
    `initramfs` regen + reboot on distros that bake modprobe.d into it) -
    there's no runtime toggle for this parameter."""
    text = f"options nvidia_drm modeset={1 if enabled else 0}\n"
    try:
        NVIDIA_MODESET_CONF.parent.mkdir(parents=True, exist_ok=True)
        _write(NVIDIA_MODESET_CONF, text)
        # The unit runs with UMask=0077, which is right for everything the
        # helper writes into /run - but this is a config file in /etc that
        # initramfs tooling and the user both need to read. Every other file
        # in modprobe.d is 0644; a root-only one here is surprising and
        # invisible to anyone trying to work out why modeset is set.
        NVIDIA_MODESET_CONF.chmod(0o644)
    except OSError as exc:
        log.warning("could not write %s: %s", NVIDIA_MODESET_CONF, exc)
        return False
    log.info("wrote %s: modeset=%d (takes effect after reboot)",
             NVIDIA_MODESET_CONF, 1 if enabled else 0)
    return True


def revert_sysctl(key: str) -> bool:
    """Restore one pre-flight sysctl to the value it had before we changed it."""
    if key not in SYSCTL_ALLOW:
        raise ValueError(f"sysctl not in allowlist: {key}")
    f = _sysctl_state_file()
    try:
        data = json.loads(f.read_text()) if f.exists() else {}
    except (OSError, json.JSONDecodeError):
        data = {}
    if key not in data:
        return True  # never changed it
    original = data[key]
    path = (Path("/proc/sys") / key.replace(".", "/")).resolve()
    if not str(path).startswith("/proc/sys/") or not path.is_file():
        raise ValueError(f"refusing to write {path}")
    _write(path, str(int(original)))
    del data[key]
    with contextlib.suppress(OSError):
        f.write_text(json.dumps(data, indent=2))
    log.info("sysctl %s reverted to %s", key, original)
    return True


# --------------------------------------------------------------------------
# Power - Intel RAPL (PL1/PL2)
# Bounded on both sides: _RAPL_FLOOR_UW stops a silent local DoS, and
# every write is clamped to the zone's firmware maximum.
# --------------------------------------------------------------------------

def _rapl_constraint(idx: int, leaf: str) -> Path:
    return RAPL_BASE / f"constraint_{idx}_{leaf}"


def get_power_limits() -> tuple[int, int]:
    pl1 = int(_read(_rapl_constraint(0, "power_limit_uw")))
    pl2 = int(_read(_rapl_constraint(1, "power_limit_uw")))
    return pl1, pl2


#: absolute upper bound for a RAPL power-limit write (µW), used when the firmware
#: maximum can't be read - no real CPU accepts anywhere near this
_RAPL_CEILING_UW = 1_000_000_000

#: Absolute floor for a RAPL PL1/PL2 write (µW). SetPowerLimits is a "raise the
#: cap" feature; driving PL1 down to a few watts is a silent local DoS (the
#: machine crawls but nothing errors). 6 W sits below the lowest real preset
#: (an Intel handheld's on-battery TDP is ~8 W) while still blocking the
#: "pin it to 4 W" case. A genuinely lower limit has to be set out-of-band
#: (root shell / firmware), not over this bus.
_RAPL_FLOOR_UW = 6_000_000

def set_power_limits(pl1_uw: int, pl2_uw: int) -> bool:
    # Validate everything BEFORE snapshotting. This used to snapshot first and
    # validate inside the write loop, so a request below the floor was
    # correctly refused but still left a root-owned state.json in /run. Two
    # ways that hurts: the machine looks mid-session to anything inspecting
    # /run, and - because _snapshot() early-returns once the file exists - the
    # next legitimate apply never records its own baseline, so RevertAll
    # restores whatever was true at the moment of the rejected call instead.
    # Every sibling here already validates first (set_governor, set_epp,
    # set_sysctl, spin_up_fans); this one was the odd one out. Found by the
    # conformance suite, which noticed /run/goblin-mode-pro/state.json
    # appearing after a call that had just been refused.
    requested = ((0, int(pl1_uw)), (1, int(pl2_uw)))
    for idx, value in requested:
        # <= 0 means "leave this constraint alone", not "set it to zero", so
        # it is not a floor violation.
        if 0 < value < _RAPL_FLOOR_UW:
            raise ValueError(
                f"RAPL constraint {idx} request {value} µW is below the "
                f"{_RAPL_FLOOR_UW} µW floor - this method only raises the limit"
            )
    _snapshot()
    ok = True
    for idx, value in requested:
        if value <= 0:
            continue
        value = min(value, _RAPL_CEILING_UW)
        try:
            cap = int(_read(_rapl_constraint(idx, "max_power_uw")))
            if cap > 0:
                value = min(value, cap)
            _write(_rapl_constraint(idx, "power_limit_uw"), str(value))
        except OSError as exc:
            log.warning("RAPL write failed for constraint %d: %s", idx, exc)
            ok = False
    return ok


# --------------------------------------------------------------------------
# snapshot / restore
# --------------------------------------------------------------------------
def _snapshot() -> None:
    if STATE_FILE.exists():
        return
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    data: dict[str, object] = {}
    with contextlib.suppress(OSError):
        data["governor"] = get_governor()
    epp_paths = _cpu_epp_paths()
    if epp_paths:
        with contextlib.suppress(OSError):
            data["epp"] = _read(epp_paths[0])
    try:
        pl1, pl2 = get_power_limits()
        data["pl1_uw"], data["pl2_uw"] = pl1, pl2
    except OSError:
        pass
    STATE_FILE.write_text(json.dumps(data, indent=2))
    log.info("snapshot saved: %s", data)


def _load_state() -> dict | None:
    if not STATE_FILE.exists():
        return None
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _restore_power_limits(data: dict) -> bool:
    ok = True
    if "pl1_uw" in data:
        for idx, key in ((0, "pl1_uw"), (1, "pl2_uw")):
            try:
                _write(_rapl_constraint(idx, "power_limit_uw"), str(int(data[key])))
            except (OSError, KeyError, ValueError):
                ok = False
    return ok


def reset_power_limits() -> bool:
    """Restore only PL1/PL2 to the snapshot value; keep the state file intact
    (the governor/EPP may still be applied for another running game)."""
    data = _load_state()
    if data is None:
        return True
    ok = _restore_power_limits(data)
    log.info("power limits reset to firmware defaults (ok=%s)", ok)
    return ok


# --------------------------------------------------------------------------
# AMD TDP (ryzenadj)
# --------------------------------------------------------------------------
def _ryzenadj(*args: str) -> str:
    """Run ryzenadj with *args*; return stdout, raise on failure."""
    out = subprocess.run(
        [RYZENADJ, *args], capture_output=True, text=True, timeout=8, check=True
    )
    return out.stdout


#: The three power limits ryzenadj exposes, as (row label in `--info`, the
#: `--<flag>` that writes it). All three are snapshotted and all three are
#: restored: an earlier version recorded only STAPM, so ResetTDP put the fast
#: (burst) limit back to the *sustained* value. On a machine that ships
#: stapm=25 W / fast=30 W that silently cost 5 W of burst headroom after any
#: set/reset cycle, until the next reboot.
_RYZENADJ_LIMITS: tuple[tuple[str, str], ...] = (
    ("STAPM LIMIT", "stapm-limit"),
    ("PPT LIMIT FAST", "fast-limit"),
    ("PPT LIMIT SLOW", "slow-limit"),
)


def _parse_ryzenadj_row(info: str, label: str) -> int | None:
    """Read one `ryzenadj --info` row's value, in mW.

    Rows look like ``| STAPM LIMIT | 25.000 | stapm-limit |``. The value column
    is watts on current ryzenadj but has been milliwatts, so the magnitude
    decides - a real limit is never 1000 W and never 25 mW.
    """
    for line in info.splitlines():
        upper = line.upper()
        if label not in upper:
            continue
        # take the value column specifically, not the parameter name after it
        parts = [c.strip() for c in line.split("|") if c.strip()]
        for cell in parts[1:]:
            m = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)", cell)
            if m:
                val = float(m.group(1))
                if val <= 0:
                    return None
                return int(val if val > 1000 else val * 1000)  # W or mW -> mW
    return None


def _ryzenadj_limits_mw() -> dict[str, int]:
    """Every power limit ryzenadj reports, in mW, keyed by its write flag."""
    try:
        info = _ryzenadj("--info")
    except (OSError, subprocess.SubprocessError):
        return {}
    out: dict[str, int] = {}
    for label, flag in _RYZENADJ_LIMITS:
        value = _parse_ryzenadj_row(info, label)
        if value:
            out[flag] = value
    return out


def _ryzenadj_stapm_mw() -> int | None:
    """Current STAPM (sustained) limit in mW."""
    return _ryzenadj_limits_mw().get("stapm-limit")


def _snapshot_tdp() -> None:
    if not RYZENADJ:
        return
    # Capture the full governor/EPP/RAPL baseline first - _snapshot() early-returns
    # once state.json exists, so it must run before we add our own key or the
    # governor's original value is never recorded and RevertAll can't restore it.
    _snapshot()
    data = _load_state() or {}
    if "ryzenadj_limits_mw" in data:
        return
    limits = _ryzenadj_limits_mw()
    if limits:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        data["ryzenadj_limits_mw"] = limits
        # kept for a helper that was upgraded under a running daemon
        data["ryzenadj_stapm_mw"] = limits.get("stapm-limit")
        STATE_FILE.write_text(json.dumps(data, indent=2))
        log.info("ryzenadj snapshot: %s", limits)


def set_tdp(watts: int) -> bool:
    if not RYZENADJ:
        log.warning("SetTDP: ryzenadj is not installed")
        return False
    watts = max(TDP_MIN_W, min(TDP_MAX_W, int(watts)))
    _snapshot_tdp()
    mw = watts * 1000
    fast = min(TDP_MAX_W, watts + 8) * 1000  # a little short-burst headroom
    try:
        _ryzenadj(
            f"--stapm-limit={mw}", f"--slow-limit={mw}", f"--fast-limit={fast}"
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("ryzenadj SetTDP(%dW) failed: %s", watts, exc)
        return False
    log.info("AMD TDP set to %d W (fast %d W) via ryzenadj", watts, fast // 1000)
    return True


def reset_tdp() -> bool:
    if not RYZENADJ:
        return True
    data = _load_state() or {}
    limits = data.get("ryzenadj_limits_mw") or {}
    if not limits:
        # a snapshot written by an older helper only recorded STAPM
        stapm = data.get("ryzenadj_stapm_mw")
        if stapm:
            limits = {"stapm-limit": int(stapm)}
    if not limits:
        log.info("ResetTDP: no snapshot; leaving current limits (cleared on reboot)")
        return True
    # Restore every limit we recorded, each to *its own* original value - not
    # all of them to STAPM, which would clamp the burst limit down to the
    # sustained one and quietly cost headroom the machine shipped with.
    args = [f"--{flag}={int(value)}" for flag, value in sorted(limits.items())]
    try:
        _ryzenadj(*args)
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("ryzenadj ResetTDP failed: %s", exc)
        return False
    log.info("AMD TDP restored: %s", limits)
    return True


def revert_all() -> bool:
    data = _load_state()
    if data is None:
        log.info("revert_all: nothing to revert")
        return True
    ok = True
    gov = data.get("governor")
    if gov:
        try:
            for path in _cpu_governor_paths():
                _write(path, gov)
        except OSError:
            ok = False
    epp = data.get("epp")
    if epp:
        for path in _cpu_epp_paths():
            try:
                _write(path, epp)
            except OSError:
                ok = False
    if not _restore_power_limits(data):
        ok = False
    if data.get("ryzenadj_stapm_mw") and not reset_tdp():
        ok = False
    if FAN_STATE_FILE.exists() and not reset_fans():
        ok = False
    STATE_FILE.unlink(missing_ok=True)
    log.info("reverted to %s (ok=%s)", data, ok)
    return ok


# --------------------------------------------------------------------------
# polkit
# --------------------------------------------------------------------------
def _check_authorized(sender: str, action_id: str = POLKIT_PERF) -> bool:
    try:
        authority = Gio.DBusProxy.new_for_bus_sync(
            Gio.BusType.SYSTEM,
            Gio.DBusProxyFlags.NONE,
            None,
            "org.freedesktop.PolicyKit1",
            "/org/freedesktop/PolicyKit1/Authority",
            "org.freedesktop.PolicyKit1.Authority",
            None,
        )
        # CheckAuthorization((sa{sv}) subject, s action_id, a{ss} details,
        #                    u flags, s cancellation_id) -> (bba{ss})
        result = authority.call_sync(
            "CheckAuthorization",
            GLib.Variant(
                "((sa{sv})sa{ss}us)",
                (
                    ("system-bus-name", {"name": GLib.Variant("s", sender)}),
                    action_id,
                    {},
                    1,  # AllowUserInteraction
                    "",
                ),
            ),
            Gio.DBusCallFlags.NONE,
            25000,
            None,
        )
        is_authorized, _is_challenge, _details = result.unpack()[0]
        return bool(is_authorized)
    except GLib.Error as exc:
        log.error("polkit check failed: %s", exc)
        return False


def _caller_uid(connection, sender: str) -> int | None:
    """The Unix uid behind a D-Bus sender name."""
    try:
        res = connection.call_sync(
            "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
            "GetConnectionUnixUser", GLib.Variant("(s)", (sender,)),
            GLib.VariantType("(u)"), Gio.DBusCallFlags.NONE, 5000, None,
        )
        return int(res.unpack()[0])
    except GLib.Error as exc:
        log.warning("could not resolve caller uid: %s", exc)
        return None


# --------------------------------------------------------------------------
# D-Bus glue - method dispatch
# --------------------------------------------------------------------------
def _handle_call(
    connection,
    sender,
    object_path,
    interface_name,
    method_name,
    parameters,
    invocation,
):
    try:
        if method_name in _MUTATING:
            action = _polkit_action_for(method_name)
            if not _check_authorized(sender, action):
                invocation.return_dbus_error(
                    f"{IFACE}.NotAuthorized", "polkit authorization denied"
                )
                return

        args = parameters.unpack()
        if method_name == "GetGovernor":
            invocation.return_value(GLib.Variant("(s)", (get_governor(),)))
        elif method_name == "SetGovernor":
            invocation.return_value(GLib.Variant("(b)", (set_governor(args[0]),)))
        elif method_name == "SetEPP":
            invocation.return_value(GLib.Variant("(b)", (set_epp(args[0]),)))
        elif method_name == "Renice":
            uid = _caller_uid(connection, sender)
            if uid is None:
                invocation.return_dbus_error(
                    f"{IFACE}.NotAuthorized",
                    "could not determine the calling user's uid - refusing",
                )
                return
            invocation.return_value(
                GLib.Variant("(b)", (renice(int(args[0]), int(args[1]), uid),))
            )
        elif method_name == "GetPowerLimits":
            pl1, pl2 = get_power_limits()
            invocation.return_value(GLib.Variant("(tt)", (pl1, pl2)))
        elif method_name == "SetPowerLimits":
            invocation.return_value(
                GLib.Variant("(b)", (set_power_limits(int(args[0]), int(args[1])),))
            )
        elif method_name == "ResetPowerLimits":
            invocation.return_value(GLib.Variant("(b)", (reset_power_limits(),)))
        elif method_name == "SetTDP":
            invocation.return_value(GLib.Variant("(b)", (set_tdp(int(args[0])),)))
        elif method_name == "ResetTDP":
            invocation.return_value(GLib.Variant("(b)", (reset_tdp(),)))
        elif method_name == "HasTDPControl":
            invocation.return_value(GLib.Variant("(b)", (RYZENADJ is not None,)))
        elif method_name == "RevertAll":
            invocation.return_value(GLib.Variant("(b)", (revert_all(),)))
        elif method_name == "SetSysctl":
            invocation.return_value(GLib.Variant("(b)", (set_sysctl(args[0], args[1]),)))
        elif method_name == "RevertSysctl":
            invocation.return_value(GLib.Variant("(b)", (revert_sysctl(args[0]),)))
        elif method_name == "ApplyUndervolt":
            invocation.return_value(GLib.Variant("(b)", (apply_undervolt(),)))
        elif method_name == "ReadUndervolt":
            invocation.return_value(GLib.Variant("(s)", (read_undervolt(),)))
        elif method_name == "ApplyAmdUndervolt":
            invocation.return_value(GLib.Variant("(b)", (apply_amd_undervolt(),)))
        elif method_name == "SetNvidiaModeset":
            invocation.return_value(GLib.Variant("(b)", (set_nvidia_modeset(bool(args[0])),)))
        elif method_name == "SpinUpFans":
            invocation.return_value(GLib.Variant("(b)", (spin_up_fans(int(args[0])),)))
        elif method_name == "ResetFans":
            invocation.return_value(GLib.Variant("(b)", (reset_fans(),)))
        else:
            invocation.return_dbus_error(
                "org.freedesktop.DBus.Error.UnknownMethod", method_name
            )
    except Exception as exc:
        log.exception("method %s failed", method_name)
        invocation.return_dbus_error(f"{IFACE}.Failed", str(exc))


def _handle_get_property(connection, sender, path, iface, prop, error, user_data):
    """Read-only identity properties. NOT authorized: they change nothing and
    reveal nothing a caller cannot already see by looking at the unit file."""
    if prop == "Version":
        return GLib.Variant("s", HELPER_VERSION)
    if prop == "InterfaceVersion":
        return GLib.Variant("u", INTERFACE_VERSION)
    if prop == "Implementation":
        return GLib.Variant("s", IMPLEMENTATION)
    return None


def _on_bus_acquired(connection, name):
    node_info = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION_XML)
    connection.register_object(
        OBJECT_PATH,
        node_info.interfaces[0],
        _handle_call,
        _handle_get_property,
        None,
    )
    log.info("registered %s", OBJECT_PATH)


def _on_name_lost(connection, name):
    log.error("lost bus name %s - exiting", name)
    sys.exit(1)


def main() -> int:
    if os.geteuid() != 0:
        print("goblin-helper must run as root", file=sys.stderr)
        return 1

    if len(sys.argv) > 1 and sys.argv[1] == "--revert":
        return 0 if revert_all() else 1

    # Crash recovery: a SIGKILLed / OOM-killed helper never ran ExecStopPost,
    # so a fan channel it switched to manual is still pinned. Hand it back to
    # the EC before we accept any request - leaving a fan under manual control
    # with no daemon watching it is the one state we must never sit in.
    # (governor / EPP / RAPL are left as-is on purpose: they persist in the
    # hardware regardless, state.json still holds the pre-game baseline, and
    # RevertAll on the next game-exit restores them correctly. Reverting them
    # here would instead kill a boost mid-game after a transient Restart=.)
    if FAN_STATE_FILE.exists():
        log.warning("stale fan state from a previous instance - restoring EC control")
        reset_fans()

    loop = GLib.MainLoop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, loop.quit)

    owner_id = Gio.bus_own_name(
        Gio.BusType.SYSTEM,
        BUS_NAME,
        Gio.BusNameOwnerFlags.NONE,
        _on_bus_acquired,
        None,
        _on_name_lost,
    )
    try:
        loop.run()
    finally:
        Gio.bus_unown_name(owner_id)
        # Best-effort: leave the machine as we found it when the service stops.
        revert_all()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
