#!/usr/bin/env python3
"""Goblin Mode Pro - privileged helper.

Runs as root under ``goblin-mode-pro-helper.service`` and owns the system D-Bus
name ``com.goblinmode.ProHelper``. It is deliberately tiny: it performs *only*
the handful of root-only operations the unprivileged daemon cannot do, and every
mutating call is gated by the polkit action ``com.goblinmode.pro.manage-performance``.

Design notes
------------
* Standard library + PyGObject (Gio/GLib) only - no third-party imports, so the
  helper keeps working even if the user's Python env is broken.
* Before the first mutation it snapshots the current governor / EPP / RAPL limits
  to ``/run/goblin-mode-pro/state.json`` (tmpfs, root-only). ``RevertAll`` and a
  fresh ``--revert-on-exit`` restore from there, so a helper restart mid-game
  still reverts cleanly.
"""

from __future__ import annotations

import glob
import json
import logging
import os
import re
import signal
import sys
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

BUS_NAME = "com.goblinmode.ProHelper"
OBJECT_PATH = "/com/goblinmode/ProHelper"
IFACE = "com.goblinmode.ProHelper.Manager"
POLKIT_ACTION = "com.goblinmode.pro.manage-performance"

STATE_DIR = Path("/run/goblin-mode-pro")
STATE_FILE = STATE_DIR / "state.json"

CPU_BASE = Path("/sys/devices/system/cpu")
RAPL_BASE = Path("/sys/class/powercap/intel-rapl/intel-rapl:0")

NICE_FLOOR = -10  # never let a caller push a process below this

# sysctl keys the pre-flight check is allowed to set at runtime
SYSCTL_ALLOW = {
    "vm.max_map_count", "vm.swappiness", "vm.compaction_proactiveness",
    "vm.dirty_ratio", "vm.dirty_background_ratio",
    "kernel.split_lock_mitigate", "kernel.sched_cfs_bandwidth_slice_us",
    "fs.file-max", "fs.inotify.max_user_watches",
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
    <method name="RevertAll">
      <arg type="b" name="ok" direction="out"/>
    </method>
    <method name="SetSysctl">
      <arg type="s" name="key" direction="in"/>
      <arg type="s" name="value" direction="in"/>
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


def renice(pid: int, nice: int) -> bool:
    if pid <= 1 or not Path(f"/proc/{pid}").exists():
        raise ValueError(f"no such process: {pid}")
    nice = max(NICE_FLOOR, min(19, int(nice)))
    # Renice the whole thread group.
    os.setpriority(os.PRIO_PROCESS, pid, nice)
    try:
        for tid in os.listdir(f"/proc/{pid}/task"):
            try:
                os.setpriority(os.PRIO_PROCESS, int(tid), nice)
            except (OSError, ValueError):
                pass
    except OSError:
        pass
    return True


def set_sysctl(key: str, value: str) -> bool:
    if key not in SYSCTL_ALLOW:
        raise ValueError(f"sysctl not in allowlist: {key}")
    if not re.match(r"^-?\d+$", value.strip()):
        raise ValueError(f"non-numeric sysctl value: {value!r}")
    path = Path("/proc/sys") / key.replace(".", "/")
    _write(path, value.strip())
    log.info("sysctl %s = %s", key, value.strip())
    return True


def _rapl_constraint(idx: int, leaf: str) -> Path:
    return RAPL_BASE / f"constraint_{idx}_{leaf}"


def get_power_limits() -> tuple[int, int]:
    pl1 = int(_read(_rapl_constraint(0, "power_limit_uw")))
    pl2 = int(_read(_rapl_constraint(1, "power_limit_uw")))
    return pl1, pl2


def set_power_limits(pl1_uw: int, pl2_uw: int) -> bool:
    _snapshot()
    ok = True
    for idx, value in ((0, pl1_uw), (1, pl2_uw)):
        if value <= 0:
            continue
        try:
            cap = int(_read(_rapl_constraint(idx, "max_power_uw")))
            value = min(int(value), cap) if cap > 0 else int(value)
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
    STATE_FILE.unlink(missing_ok=True)
    log.info("reverted to %s (ok=%s)", data, ok)
    return ok


# --------------------------------------------------------------------------
# polkit
# --------------------------------------------------------------------------
def _check_authorized(sender: str) -> bool:
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
                    POLKIT_ACTION,
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


# --------------------------------------------------------------------------
# D-Bus glue
# --------------------------------------------------------------------------
_MUTATING = {
    "SetGovernor",
    "SetEPP",
    "Renice",
    "SetPowerLimits",
    "ResetPowerLimits",
    "RevertAll",
    "SetSysctl",
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
        if method_name in _MUTATING and not _check_authorized(sender):
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
            invocation.return_value(
                GLib.Variant("(b)", (renice(int(args[0]), int(args[1])),))
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
        elif method_name == "RevertAll":
            invocation.return_value(GLib.Variant("(b)", (revert_all(),)))
        elif method_name == "SetSysctl":
            invocation.return_value(GLib.Variant("(b)", (set_sysctl(args[0], args[1]),)))
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
