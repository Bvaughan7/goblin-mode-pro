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
from gi.repository import Gio, GLib  # noqa: E402

BUS_NAME = "com.goblinmode.ProHelper"
OBJECT_PATH = "/com/goblinmode/ProHelper"
IFACE = "com.goblinmode.ProHelper.Manager"
POLKIT_PERF = "com.goblinmode.pro.manage-performance"
POLKIT_KERNEL = "com.goblinmode.pro.manage-kernel-tunables"

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

# sysctl keys the pre-flight check is allowed to set at runtime, each with an
# accepted numeric range. Nothing outside this table can be touched.
SYSCTL_ALLOW: dict[str, tuple[int, int]] = {
    "vm.max_map_count": (65530, 2147483642),
    "vm.swappiness": (0, 200),
    "vm.compaction_proactiveness": (0, 100),
    "kernel.split_lock_mitigate": (0, 1),
    "user.max_user_namespaces": (0, 2147483647),
}

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
  </interface>
</node>
"""


# --------------------------------------------------------------------------
# sysfs helpers
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


def set_epp(epp: str) -> bool:
    _snapshot()
    ok = False
    for path in _cpu_epp_paths():
        try:
            _write(path, epp)
            ok = True
        except OSError as exc:
            log.warning("EPP write failed for %s: %s", path, exc)
    return ok


def renice(pid: int, nice: int, caller_uid: int | None = None) -> bool:
    proc = Path(f"/proc/{int(pid)}")
    if pid <= 1 or not proc.exists():
        raise ValueError(f"no such process: {pid}")
    # only renice a process the caller actually owns (root may renice anything)
    if caller_uid not in (None, 0):
        try:
            owner = proc.stat().st_uid
        except OSError as exc:
            raise ValueError(f"cannot stat process {pid}: {exc}") from exc
        if owner != caller_uid:
            raise PermissionError(f"process {pid} is not owned by uid {caller_uid}")
    nice = max(NICE_FLOOR, min(19, int(nice)))
    os.setpriority(os.PRIO_PROCESS, pid, nice)
    try:
        for tid in os.listdir(proc / "task"):
            try:
                os.setpriority(os.PRIO_PROCESS, int(tid), nice)
            except (OSError, ValueError):
                pass
    except OSError:
        pass
    return True


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
    path = (Path("/proc/sys") / key.replace(".", "/")).resolve()
    if not str(path).startswith("/proc/sys/") or not path.is_file():
        raise ValueError(f"refusing to write {path}")
    _write(path, str(int(data[key])))
    del data[key]
    try:
        f.write_text(json.dumps(data, indent=2))
    except OSError:
        pass
    log.info("sysctl %s reverted to %s", key, data.get(key))
    return True


def _rapl_constraint(idx: int, leaf: str) -> Path:
    return RAPL_BASE / f"constraint_{idx}_{leaf}"


def get_power_limits() -> tuple[int, int]:
    pl1 = int(_read(_rapl_constraint(0, "power_limit_uw")))
    pl2 = int(_read(_rapl_constraint(1, "power_limit_uw")))
    return pl1, pl2


#: absolute upper bound for a RAPL power-limit write (µW), used when the firmware
#: maximum can't be read - no real CPU accepts anywhere near this
_RAPL_CEILING_UW = 1_000_000_000

def set_power_limits(pl1_uw: int, pl2_uw: int) -> bool:
    _snapshot()
    ok = True
    for idx, value in ((0, int(pl1_uw)), (1, int(pl2_uw))):
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
    try:
        data["governor"] = get_governor()
    except OSError:
        pass
    epp_paths = _cpu_epp_paths()
    if epp_paths:
        try:
            data["epp"] = _read(epp_paths[0])
        except OSError:
            pass
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


def _ryzenadj_stapm_mw() -> int | None:
    """Current STAPM (sustained) limit in mW, parsed from `ryzenadj --info`."""
    try:
        info = _ryzenadj("--info")
    except (OSError, subprocess.SubprocessError):
        return None
    for line in info.splitlines():
        # rows look like:  | STAPM LIMIT        |    25000.000 |  stapm-limit |
        if "STAPM LIMIT" in line.upper():
            m = re.search(r"([0-9]+(?:\.[0-9]+)?)", line.split("|", 2)[-1] if "|" in line else line)
            if m:
                val = float(m.group(1))
                return int(val if val > 1000 else val * 1000)  # W or mW -> mW
    return None


def _snapshot_tdp() -> None:
    if not RYZENADJ:
        return
    # Capture the full governor/EPP/RAPL baseline first - _snapshot() early-returns
    # once state.json exists, so it must run before we add our own key or the
    # governor's original value is never recorded and RevertAll can't restore it.
    _snapshot()
    data = _load_state() or {}
    if "ryzenadj_stapm_mw" in data:
        return
    stapm = _ryzenadj_stapm_mw()
    if stapm:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        data["ryzenadj_stapm_mw"] = stapm
        STATE_FILE.write_text(json.dumps(data, indent=2))
        log.info("ryzenadj snapshot: stapm=%d mW", stapm)


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
    stapm = data.get("ryzenadj_stapm_mw")
    if not stapm:
        log.info("ResetTDP: no snapshot; leaving current limits (cleared on reboot)")
        return True
    try:
        _ryzenadj(
            f"--stapm-limit={int(stapm)}",
            f"--slow-limit={int(stapm)}",
            f"--fast-limit={int(stapm)}",
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("ryzenadj ResetTDP failed: %s", exc)
        return False
    log.info("AMD TDP restored to %d W", int(stapm) // 1000)
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
# D-Bus glue
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
}


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
            action = (POLKIT_KERNEL if method_name in ("SetSysctl", "RevertSysctl")
                      else POLKIT_PERF)
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
        else:
            invocation.return_dbus_error(
                "org.freedesktop.DBus.Error.UnknownMethod", method_name
            )
    except Exception as exc:  # noqa: BLE001 - report everything to the caller
        log.exception("method %s failed", method_name)
        invocation.return_dbus_error(f"{IFACE}.Failed", str(exc))


def _on_bus_acquired(connection, name):
    node_info = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION_XML)
    connection.register_object(
        OBJECT_PATH,
        node_info.interfaces[0],
        _handle_call,
        None,
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
