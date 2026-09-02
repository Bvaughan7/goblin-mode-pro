#!/usr/bin/env python3
"""Conformance suite for the privileged helper's D-Bus interface.

This exercises the helper from *outside*, over the system bus, against
whatever implementation happens to be answering. It is the test that proves a
Rust helper behaves identically to the Python one it replaces, so it must
never import the helper's source or know which language wrote it.

    python3 tests/conformance/helper.py                 # read-only + rejections
    python3 tests/conformance/helper.py --apply         # + snapshot/revert
    sudo python3 tests/conformance/helper.py --polkit-routing
    python3 tests/conformance/helper.py --json          # machine-readable

Run it as *your own user, in your desktop session* - not under sudo. The
helper authorizes every mutating call through polkit, and the policy grants
`manage-performance` with `allow_active=yes`: free for an active local
session, `auth_admin` for anything else. A root shell started from sudo is
not an active session, so under sudo every mutating method is denied by
policy and the suite would measure nothing but that denial.

The one exception is `--polkit-routing`, which needs to eavesdrop the system
bus to see which polkit action each method demands, and eavesdropping needs
root. That mode expects its calls to be denied - it reads the action id out
of the helper's CheckAuthorization call, which happens before the verdict, so
the denial is irrelevant and conveniently means nothing gets mutated.

Two prompts are expected on a first run: `SetSysctl` and `SpinUpFans` sit on
`auth_admin_keep` actions. Answer them; the rest of the run is quiet.

The rule the output follows, borrowed from `selftest`: **never SKIP silently.**
A capability this machine does not have is a SKIP that names the capability
and why it is missing. A SKIP is never evidence that anything works.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO / "tests"))
from _dbusxml import canonicalize

# --------------------------------------------------------------------------
# The contract. Hard-coded rather than imported from the helper: a suite that
# reads its expectations out of the implementation it is testing proves only
# that the implementation agrees with itself.
# --------------------------------------------------------------------------
BUS_NAME = "com.goblinmode.ProHelper"
OBJECT_PATH = "/com/goblinmode/ProHelper"
IFACE = "com.goblinmode.ProHelper.Manager"

ERR_NOT_AUTHORIZED = f"{IFACE}.NotAuthorized"
ERR_FAILED = f"{IFACE}.Failed"
ERR_UNKNOWN_METHOD = "org.freedesktop.DBus.Error.UnknownMethod"

POLKIT_PERF = "com.goblinmode.pro.manage-performance"
POLKIT_KERNEL = "com.goblinmode.pro.manage-kernel-tunables"
POLKIT_THERMAL = "com.goblinmode.pro.manage-hardware-thermal"

#: the action every mutating method must demand. This table is the check that
#: would have caught SpinUpFans landing on the permissive action.
EXPECTED_ACTION = {
    "SetGovernor": POLKIT_PERF,
    "SetEPP": POLKIT_PERF,
    "Renice": POLKIT_PERF,
    "SetPowerLimits": POLKIT_PERF,
    "ResetPowerLimits": POLKIT_PERF,
    "SetTDP": POLKIT_PERF,
    "ResetTDP": POLKIT_PERF,
    "RevertAll": POLKIT_PERF,
    "ApplyUndervolt": POLKIT_PERF,
    "ApplyAmdUndervolt": POLKIT_PERF,
    # handing the fans back to the EC must never need a prompt
    "ResetFans": POLKIT_PERF,
    "SetSysctl": POLKIT_KERNEL,
    "RevertSysctl": POLKIT_KERNEL,
    "SetNvidiaModeset": POLKIT_KERNEL,
    "SpinUpFans": POLKIT_THERMAL,
}

#: methods that need no authorization at all
READ_ONLY = ("GetGovernor", "GetPowerLimits", "HasTDPControl", "ReadUndervolt")

#: polkit actions the policy puts behind auth_admin_keep. Calling a method on
#: one of these raises an authentication dialog on the user's desktop, and the
#: client cannot prevent it: the HELPER passes AllowUserInteraction=1 in its
#: own CheckAuthorization, so the prompt is raised before any answer this
#: suite could give. Three dismissed or mistyped dialogs trip pam_faillock
#: (deny=3 by default) and lock the account out of sudo for ten minutes, which
#: is a genuinely hostile thing for a test to do to somebody who is not
#: watching their screen. So every check that touches one is opt-in.
PROMPTING_ACTIONS = frozenset({POLKIT_KERNEL, POLKIT_THERMAL})

NVIDIA_MODESET_CONF = Path("/etc/modprobe.d/goblin-mode-pro-nvidia.conf")


def _prompts(method: str) -> bool:
    return EXPECTED_ACTION.get(method) in PROMPTING_ACTIONS

MIN_FAN_PERCENT = 40
RAPL_FLOOR_UW = 6_000_000
SYSCTL_RANGES = {
    "vm.max_map_count": (65530, 2147483642),
    "vm.swappiness": (0, 200),
    "vm.compaction_proactiveness": (0, 100),
    "kernel.split_lock_mitigate": (0, 1),
    "user.max_user_namespaces": (0, 2147483647),
    "kernel.unprivileged_userns_clone": (0, 1),
}

STATE_DIR = Path("/run/goblin-mode-pro")
STATE_FILE = STATE_DIR / "state.json"
FROZEN_XML = _REPO / "docs" / "dbus-interface-v1.xml"

CPU_BASE = Path("/sys/devices/system/cpu")
HWMON_BASE = Path("/sys/class/hwmon")

PASS, FAIL, SKIP, INFO = "PASS", "FAIL", "SKIP", "INFO"


class _NotProbeable(Exception):
    """This method cannot be probed without changing the machine."""


@dataclass
class Result:
    name: str
    title: str
    status: str
    detail: str
    section: str = "General"
    observed: dict = field(default_factory=dict)


# --------------------------------------------------------------------------
# bus plumbing
# --------------------------------------------------------------------------
def _dbus_error_name(exc: GLib.Error) -> str:
    return Gio.dbus_error_get_remote_error(exc) or ""


def _dbus_error_message(exc: GLib.Error) -> str:
    stripped = GLib.Error.copy(exc)
    Gio.dbus_error_strip_remote_error(stripped)
    return stripped.message


class Helper:
    """A thin, deliberately dumb D-Bus client. No retries, no cleverness."""

    def __init__(self, timeout_ms: int = 30000):
        self.bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        self.timeout_ms = timeout_ms

    def call(self, method: str, params: GLib.Variant | None = None,
             timeout_ms: int | None = None) -> GLib.Variant:
        """Return the raw reply variant, so the caller can check its type."""
        return self.bus.call_sync(
            BUS_NAME, OBJECT_PATH, IFACE, method, params, None,
            Gio.DBusCallFlags.NONE,
            self.timeout_ms if timeout_ms is None else timeout_ms, None,
        )

    def introspect(self) -> str:
        reply = self.bus.call_sync(
            BUS_NAME, OBJECT_PATH, "org.freedesktop.DBus.Introspectable",
            "Introspect", None, GLib.VariantType("(s)"),
            Gio.DBusCallFlags.NONE, 10000, None,
        )
        return reply.unpack()[0]


# --------------------------------------------------------------------------
# machine facts, so a SKIP can say what is missing
# --------------------------------------------------------------------------
def _read(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except OSError:
        return None


#: keys the v1 snapshot may contain. An unrecognised key is reported as INFO,
#: not FAIL: a newer helper adding one is exactly the forward-compatibility
#: the hybrid plan requires, and it must not read as a contract violation.
_SNAPSHOT_KEYS = {"governor", "epp", "pl1_uw", "pl2_uw",
                  "ryzenadj_limits_mw", "ryzenadj_stapm_mw"}

_STATE_DIR_REASON = (
    f"{STATE_DIR} is 0700 and root-owned by design, so this caller cannot see "
    "the snapshot at all. Note that Path.exists() also returns False for an "
    "unreadable directory, which would make a 'the file is gone' assertion "
    "pass unconditionally - so it is skipped rather than faked. To check the "
    "schema, run as root on a system whose polkit rules also authorize root."
)


def _state_dir_readable() -> bool:
    """Can this caller actually observe the helper's state directory?

    Everything that inspects STATE_FILE has to ask first. `Path.exists()`
    returns False both for "no such file" and for "you may not look", and a
    check that cannot tell those apart reports a confident PASS for a
    condition it never tested.
    """
    return os.access(STATE_DIR, os.R_OK | os.X_OK)


def _governor_paths() -> list[Path]:
    return sorted(CPU_BASE.glob("cpu[0-9]*/cpufreq/scaling_governor"))


def _epp_paths() -> list[Path]:
    return sorted(CPU_BASE.glob("cpu[0-9]*/cpufreq/energy_performance_preference"))


def _pwm_controls() -> list[Path]:
    out = []
    for hwmon in sorted(HWMON_BASE.glob("hwmon*")):
        for pwm in sorted(hwmon.glob("pwm[0-9]*")):
            if re.fullmatch(r"pwm\d+", pwm.name) and (
                hwmon / f"{pwm.name}_enable"
            ).exists():
                out.append(pwm)
    return out


def _session_is_active() -> bool | None:
    """Is this caller in an active local session? True / False / None=unknown.

    polkit's `allow_active` hinges on this. It is deliberately tri-state:
    `loginctl show-session self` fails outright for a process that belongs to
    no session ("Caller does not belong to any known session"), which is
    common for a shell started by a tool or a service - and polkit may STILL
    authorize it, because polkit resolves the subject from the bus name's pid
    rather than from loginctl. Reporting that unknown case as "inactive" would
    print a confident explanation for a failure that has a different cause.
    """
    try:
        proc = subprocess.run(
            ["loginctl", "show-session", "self", "-p", "Active"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return "Active=yes" in proc.stdout


def _helper_exec_start() -> str:
    """Which implementation is actually behind the bus name.

    Until the interface grows an `Implementation` property, this is the only
    way a bug report can say whether the Python or the Rust helper answered.
    Read from argv rather than from `path=`: the Python helper is executed as
    `/usr/bin/python3 /usr/lib/.../goblin_helper.py`, so `path=` reports the
    interpreter and tells you nothing about which helper is installed.
    """
    try:
        out = subprocess.run(
            ["systemctl", "show", "goblin-mode-pro-helper.service",
             "-p", "ExecStart", "--value"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        m = re.search(r"argv\[\]=(.*?) ;", out)
        argv = m.group(1).strip() if m else out
        # the last argument is the script or binary; resolve it through any
        # symlink, which is how the hybrid period selects an implementation
        target = argv.split()[-1] if argv else ""
        resolved = os.path.realpath(target) if target else ""
        return f"{argv} [{resolved}]" if resolved and resolved != target else argv
    except (OSError, subprocess.SubprocessError, IndexError):
        return ""


def capabilities() -> dict:
    govs = _governor_paths()
    return {
        "cpufreq_governors": len(govs),
        "available_governors": (
            _read(CPU_BASE / "cpu0/cpufreq/scaling_available_governors") or ""
        ).split(),
        "epp_files": len(_epp_paths()),
        "available_epps": (
            _read(CPU_BASE / "cpu0/cpufreq/energy_performance_available_preferences")
            or ""
        ).split(),
        "rapl_zones": len(list(Path("/sys/class/powercap").glob("intel-rapl:*"))),
        "writable_pwm_channels": len(_pwm_controls()),
        "sysctl_keys_present": [
            k for k in SYSCTL_RANGES
            if Path("/proc/sys", k.replace(".", "/")).exists()
        ],
        "pidfd_open": hasattr(os, "pidfd_open"),
        "session_active": _session_is_active(),
        "euid": os.geteuid(),
        "helper_exec_start": _helper_exec_start(),
    }


# --------------------------------------------------------------------------
# the suite
# --------------------------------------------------------------------------
class Conformance:
    def __init__(self, apply: bool = False, allow_prompts: bool = False):
        self.apply = apply
        self.allow_prompts = allow_prompts
        self.results: list[Result] = []
        #: polkit actions this caller was refused, judged together at the end
        self.denied_actions: set[str] = set()
        self.caps = capabilities()
        self.helper = Helper()

    def _add(self, name, title, status, detail, section="General", **observed):
        r = Result(name, title, status, detail, section, observed)
        self.results.append(r)
        return r

    # -- helpers ---------------------------------------------------------
    def _denied(self, name, title, method, message, section):
        """polkit refused this call, so the check could not run.

        This is a SKIP, not a FAIL: it says nothing about whether the helper
        obeys its contract. It is recorded per-check rather than aborting the
        run, because the two `auth_admin_keep` actions legitimately need an
        interactive prompt - a non-interactive run should still complete every
        check that does not need one, instead of stopping at the first.
        """
        action = EXPECTED_ACTION.get(method, POLKIT_PERF)
        self.denied_actions.add(action)
        return self._add(
            name, title, SKIP,
            f"polkit refused {method} ({action}): {message}. This check needs "
            "authorization it did not get - it is not evidence either way.",
            section, polkit_action=action)

    def _expect_error(self, name, title, method, params, want_name,
                      want_message, section, unchanged=None):
        """Call a method that must be refused, and prove it was refused.

        Asserts three things, because any one alone is too weak: the D-Bus
        error *name* (not merely that something threw), a distinguishing
        fragment of the message, and - the one that actually matters - that
        the machine is unchanged afterwards.
        """
        if _prompts(method) and not self.allow_prompts:
            return self._add(
                name, title, SKIP,
                f"{method} is gated by {EXPECTED_ACTION[method]}, which is "
                "auth_admin_keep: calling it raises a password dialog on the "
                "desktop even though the call is going to be refused on its "
                "arguments. Pass --prompts, while watching the screen, to "
                "include it.", section)
        before = unchanged() if unchanged else None
        # A refused call must leave NO state behind - not just leave the value
        # it was asked to change alone. set_power_limits snapshotted before it
        # validated, so a below-floor request was correctly refused and still
        # wrote a root-owned state.json, which made the machine look
        # mid-session and stopped the next real apply recording its baseline.
        # Only observable when this caller can read the state dir; see
        # _state_dir_readable.
        state_before = STATE_FILE.exists() if _state_dir_readable() else None
        try:
            self.helper.call(method, params)
        except GLib.Error as exc:
            got_name = _dbus_error_name(exc)
            got_msg = _dbus_error_message(exc)
            if got_name == ERR_NOT_AUTHORIZED:
                return self._denied(name, title, method, got_msg, section)
            if got_name != want_name:
                return self._add(name, title, FAIL,
                                 f"expected error {want_name}, got {got_name}: {got_msg}",
                                 section, error_name=got_name, message=got_msg)
            if want_message.lower() not in got_msg.lower():
                return self._add(name, title, FAIL,
                                 f"error name is right but the message does not mention "
                                 f"{want_message!r}: {got_msg}",
                                 section, error_name=got_name, message=got_msg)
            after = unchanged() if unchanged else None
            if unchanged and before != after:
                return self._add(name, title, FAIL,
                                 f"rejected correctly but the machine changed anyway: "
                                 f"{before!r} -> {after!r}",
                                 section, before=before, after=after)
            if state_before is False and STATE_FILE.exists():
                return self._add(
                    name, title, FAIL,
                    f"rejected correctly, but the call created {STATE_FILE}. A "
                    "refused call must leave no state behind: once that file "
                    "exists the helper's _snapshot() early-returns, so the next "
                    "real apply never records its own baseline and RevertAll "
                    "restores the wrong values.", section)
            return self._add(name, title, PASS,
                             f"{got_name}: {got_msg}", section,
                             error_name=got_name, message=got_msg,
                             unchanged=before)
        return self._add(name, title, FAIL,
                         "the call was ACCEPTED - this input must be refused",
                         section)

    # -- sections --------------------------------------------------------
    def check_interface(self):
        sec = "Interface"
        try:
            served = self.helper.introspect()
        except GLib.Error as exc:
            return self._add("bus", "Helper is on the system bus", FAIL,
                             f"cannot reach {BUS_NAME}: {_dbus_error_message(exc)} "
                             "- every check below will SKIP without it", sec)
        self._add("bus", "Helper is on the system bus", PASS,
                  self.caps["helper_exec_start"] or BUS_NAME, sec)

        if not FROZEN_XML.exists():
            return self._add("frozen_interface", "Serves the frozen v1 interface",
                             SKIP, f"{FROZEN_XML} is missing", sec)
        frozen = re.sub(r"^<!--.*?-->\n", "", FROZEN_XML.read_text(), flags=re.S)
        try:
            got = canonicalize(served, IFACE)
        except LookupError as exc:
            return self._add("frozen_interface", "Serves the frozen v1 interface",
                             FAIL, str(exc), sec)
        if got == frozen:
            self._add("frozen_interface", "Serves the frozen v1 interface", PASS,
                      f"{len(re.findall(r'<method ', got))} methods, byte-identical "
                      "to docs/dbus-interface-v1.xml", sec)
        else:
            import difflib
            diff = "\n".join(list(difflib.unified_diff(
                frozen.splitlines(), got.splitlines(),
                "frozen", "served", lineterm=""))[:20])
            self._add("frozen_interface", "Serves the frozen v1 interface", FAIL,
                      "the live helper does not serve the frozen interface. This is "
                      "the check that makes mixed Python/Rust installs safe - do not "
                      "regenerate the frozen file to silence it.\n" + diff, sec)

    def check_read_only(self):
        sec = "Read-only methods"
        expect = {"GetGovernor": "(s)", "GetPowerLimits": "(tt)",
                  "HasTDPControl": "(b)", "ReadUndervolt": "(s)"}
        for method in READ_ONLY:
            try:
                reply = self.helper.call(method, timeout_ms=10000)
            except GLib.Error as exc:
                self._add(f"ro_{method}", method, FAIL,
                          f"{_dbus_error_name(exc)}: {_dbus_error_message(exc)}", sec)
                continue
            sig = reply.get_type_string()
            if sig != expect[method]:
                self._add(f"ro_{method}", method, FAIL,
                          f"reply signature {sig}, contract says {expect[method]}",
                          sec, signature=sig)
                continue
            self._add(f"ro_{method}", method, PASS,
                      f"{sig} = {reply.unpack()}", sec,
                      signature=sig, value=str(reply.unpack()))

    def check_rejections(self):
        sec = "Rejections"
        def gov():
            paths = _governor_paths()
            return _read(paths[0]) if paths else None

        if not self.caps["available_governors"]:
            self._add("reject_governor", "SetGovernor rejects an unknown governor",
                      SKIP, "no scaling_available_governors - this kernel exposes "
                      "no cpufreq governor list to validate against", sec)
        else:
            self._expect_error(
                "reject_governor", "SetGovernor rejects an unknown governor",
                "SetGovernor", GLib.Variant("(s)", ("goblin-turbo",)),
                ERR_FAILED, "unsupported governor", sec, unchanged=gov)

        def epp():
            paths = _epp_paths()
            return _read(paths[0]) if paths else None
        self._expect_error(
            "reject_epp", "SetEPP rejects an unsupported preference",
            "SetEPP", GLib.Variant("(s)", ("ludicrous",)),
            ERR_FAILED, "unsupported epp", sec, unchanged=epp)

        # A real sysctl that is deliberately NOT in the allowlist.
        outside = Path("/proc/sys/vm/dirty_ratio")
        self._expect_error(
            "reject_sysctl_allowlist", "SetSysctl refuses a key outside the allowlist",
            "SetSysctl", GLib.Variant("(ss)", ("vm.dirty_ratio", "42")),
            ERR_FAILED, "not in allowlist", sec,
            unchanged=(lambda: _read(outside)) if outside.exists() else None)

        key = "vm.swappiness"
        path = Path("/proc/sys/vm/swappiness")
        if not path.exists():
            self._add("reject_sysctl_range", "SetSysctl refuses an out-of-range value",
                      SKIP, f"{key} does not exist on this kernel", sec)
        else:
            self._expect_error(
                "reject_sysctl_range", "SetSysctl refuses an out-of-range value",
                "SetSysctl",
                GLib.Variant("(ss)", (key, str(SYSCTL_RANGES[key][1] + 1))),
                ERR_FAILED, "out of range", sec,
                unchanged=lambda: _read(path))

        self._expect_error(
            "reject_sysctl_nonnumeric", "SetSysctl refuses a non-numeric value",
            "SetSysctl", GLib.Variant("(ss)", ("vm.swappiness", "lots")),
            ERR_FAILED, "non-numeric", sec,
            unchanged=(lambda: _read(path)) if path.exists() else None)

        # NOTE: the plan describes this as "SetPowerLimits above the firmware
        # max". That is not a rejection - the helper clamps a too-high request
        # to the zone maximum and succeeds. The refusal is on the *low* side,
        # where an unbounded write would be a silent local denial of service.
        self._expect_error(
            "reject_rapl_floor", "SetPowerLimits refuses a limit below the RAPL floor",
            "SetPowerLimits", GLib.Variant("(tt)", (1_000_000, 1_000_000)),
            ERR_FAILED, "floor", sec,
            unchanged=lambda: self._power_limits())

        pwms = _pwm_controls()
        self._expect_error(
            "reject_fan_floor",
            f"SpinUpFans refuses a duty below the {MIN_FAN_PERCENT}% floor",
            "SpinUpFans", GLib.Variant("(u)", (MIN_FAN_PERCENT - 1,)),
            ERR_FAILED, "floor", sec,
            unchanged=(lambda: [_read(p) for p in pwms]) if pwms else None)

        self._expect_error(
            "reject_renice_init", "Renice refuses pid 1",
            "Renice", GLib.Variant("(ui)", (1, -5)),
            ERR_FAILED, "no such process", sec)

        # Unknown method: the interface must reject it by name, not hang.
        try:
            self.helper.bus.call_sync(
                BUS_NAME, OBJECT_PATH, IFACE, "NoSuchMethod", None, None,
                Gio.DBusCallFlags.NONE, 10000, None)
            self._add("reject_unknown_method", "An unknown method is refused by name",
                      FAIL, "the call was accepted", sec)
        except GLib.Error as exc:
            name = _dbus_error_name(exc)
            ok = name in (ERR_UNKNOWN_METHOD, "org.freedesktop.DBus.Error.UnknownMethod")
            self._add("reject_unknown_method", "An unknown method is refused by name",
                      PASS if ok else FAIL,
                      f"{name}: {_dbus_error_message(exc)}", sec, error_name=name)

    def check_renice_ownership(self):
        """The ownership gate can only be observed as a non-root caller.

        `renice()` skips the check entirely when the caller's uid is 0, so
        running this under sudo would assert nothing and report PASS for a
        gate that never ran.
        """
        sec = "Rejections"
        if os.geteuid() == 0:
            return self._add(
                "reject_renice_owner", "Renice refuses a process the caller does not own",
                SKIP,
                "running as root: renice() skips the ownership check for uid 0, so "
                "this gate cannot be observed. Re-run as your normal user.", sec)
        # pid 1 is init, owned by root, and always present.
        victim = self._find_root_owned_pid()
        if victim is None:
            return self._add(
                "reject_renice_owner", "Renice refuses a process the caller does not own",
                SKIP, "found no root-owned process to attempt", sec)
        self._expect_error(
            "reject_renice_owner", "Renice refuses a process the caller does not own",
            "Renice", GLib.Variant("(ui)", (victim, -5)),
            ERR_FAILED, "not owned by uid", sec,
            unchanged=lambda: os.getpriority(os.PRIO_PROCESS, victim))

    @staticmethod
    def _find_root_owned_pid() -> int | None:
        for entry in sorted(Path("/proc").iterdir()):
            if not entry.name.isdigit() or entry.name == "1":
                continue
            try:
                if entry.stat().st_uid == 0:
                    return int(entry.name)
            except OSError:
                continue
        return None

    def _power_limits(self):
        try:
            return self.helper.call("GetPowerLimits", timeout_ms=10000).unpack()
        except GLib.Error:
            return None

    def check_snapshot_and_revert(self):
        """apply -> inspect the snapshot -> RevertAll -> back to the recorded value.

        The snapshot lives in `/run/goblin-mode-pro/`, which is 0700 and
        root-owned on purpose. That makes the schema half of this check
        unobservable to the caller the suite is meant to run as, and it is a
        SKIP rather than a FAIL - see `_state_dir_readable`. The behavioural
        half needs no privilege: sysfs says whether the governor moved and
        whether it came back.
        """
        sec = "Snapshot / revert"
        if not self.apply:
            return self._add("snapshot", "Snapshot and RevertAll round-trip", SKIP,
                             "needs --apply: this one really does change the "
                             "governor on your machine and put it back", sec)
        govs = self.caps["available_governors"]
        current = _read(_governor_paths()[0]) if _governor_paths() else None
        if not govs or current is None:
            return self._add("snapshot", "Snapshot and RevertAll round-trip", SKIP,
                             "no cpufreq governor to change on this machine", sec)
        target = next((g for g in govs if g != current), None)
        if target is None:
            return self._add("snapshot", "Snapshot and RevertAll round-trip", SKIP,
                             f"only one governor available ({current}); nothing to "
                             "change to", sec)
        readable = _state_dir_readable()
        if readable and STATE_FILE.exists():
            return self._add("snapshot", "Snapshot and RevertAll round-trip", SKIP,
                             f"{STATE_FILE} already exists - something is mid-session, "
                             "refusing to disturb a live snapshot", sec)
        try:
            self.helper.call("SetGovernor", GLib.Variant("(s)", (target,)))
        except GLib.Error as exc:
            if _dbus_error_name(exc) == ERR_NOT_AUTHORIZED:
                return self._denied("snapshot", "Snapshot and RevertAll round-trip",
                                    "SetGovernor", _dbus_error_message(exc), sec)
            return self._add("snapshot", "Snapshot and RevertAll round-trip", FAIL,
                             f"SetGovernor failed: {_dbus_error_message(exc)}", sec)

        try:
            now = _read(_governor_paths()[0])
            self._add("snapshot_applied", "The change actually took effect",
                      PASS if now == target else FAIL,
                      f"governor is {now!r}, asked for {target!r}", sec)
            self._check_snapshot_schema(current, readable, sec)
        finally:
            # Whatever happened above, put the governor back. A `return` here
            # would discard an exception in flight from the try block, so the
            # outcome is recorded in a flag and acted on afterwards.
            reverted = True
            try:
                self.helper.call("RevertAll")
            except GLib.Error as exc:
                reverted = False
                self._add("revert", "RevertAll restores the recorded state", FAIL,
                          f"RevertAll failed - YOUR GOVERNOR MAY STILL BE {target!r}: "
                          f"{_dbus_error_message(exc)}", sec)
        if not reverted:
            return
        back = _read(_governor_paths()[0])
        self._add("revert", "RevertAll restores the recorded state",
                  PASS if back == current else FAIL,
                  f"governor is {back!r}, was {current!r} before the test", sec)
        if not readable:
            self._add("revert_clears_state", "RevertAll clears the snapshot file",
                      SKIP, _STATE_DIR_REASON, sec)
        else:
            gone = not STATE_FILE.exists()
            self._add("revert_clears_state", "RevertAll clears the snapshot file",
                      PASS if gone else FAIL,
                      f"{STATE_FILE} " + ("is gone" if gone else "still exists"), sec)

    def _check_snapshot_schema(self, expected_governor: str, readable: bool, sec: str):
        if not readable:
            return self._add("snapshot_file", "The snapshot records the prior state",
                             SKIP, _STATE_DIR_REASON, sec)
        raw = _read(STATE_FILE)
        if raw is None:
            return self._add("snapshot_file", "The snapshot records the prior state",
                             FAIL, f"{STATE_FILE} was not created", sec)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            return self._add("snapshot_file", "The snapshot records the prior state",
                             FAIL, f"{STATE_FILE} is not valid JSON: {exc}", sec)
        if data.get("governor") != expected_governor:
            return self._add("snapshot_file", "The snapshot records the prior state",
                             FAIL,
                             f"snapshot says governor={data.get('governor')!r}, it was "
                             f"{expected_governor!r}", sec, snapshot=data)
        extra = set(data) - _SNAPSHOT_KEYS
        return self._add("snapshot_file", "The snapshot records the prior state",
                         PASS if not extra else INFO,
                         f"governor={data['governor']!r}"
                         + (f"; unrecognised keys {sorted(extra)}" if extra else ""),
                         sec, snapshot=data)

    def check_idempotency(self):
        sec = "Idempotency"
        if not self.apply:
            return self._add("idempotent", "Repeated reverts are harmless", SKIP,
                             "needs --apply", sec)
        for name, method, params in (
            ("revert_all_twice", "RevertAll", None),
            ("reset_fans_unapplied", "ResetFans", None),
            ("reset_power_unapplied", "ResetPowerLimits", None),
            ("reset_tdp_unapplied", "ResetTDP", None),
        ):
            try:
                first = self.helper.call(method, params).unpack()[0]
                second = self.helper.call(method, params).unpack()[0]
            except GLib.Error as exc:
                if _dbus_error_name(exc) == ERR_NOT_AUTHORIZED:
                    self._denied(name, f"{method} twice with nothing applied",
                                 method, _dbus_error_message(exc), sec)
                    continue
                self._add(name, f"{method} twice with nothing applied", FAIL,
                          f"{_dbus_error_name(exc)}: {_dbus_error_message(exc)}", sec)
                continue
            self._add(name, f"{method} twice with nothing applied",
                      PASS if (first, second) == (True, True) else FAIL,
                      f"returned {first} then {second}; both must be True - a "
                      "revert with nothing to revert is a no-op, not an error", sec)

        current = _read(_governor_paths()[0]) if _governor_paths() else None
        if current is None:
            self._add("set_same_governor", "SetGovernor to the value it already has",
                      SKIP, "no cpufreq governor on this machine", sec)
        else:
            try:
                ok = self.helper.call(
                    "SetGovernor", GLib.Variant("(s)", (current,))).unpack()[0]
                now = _read(_governor_paths()[0])
                self._add("set_same_governor",
                          "SetGovernor to the value it already has",
                          PASS if ok and now == current else FAIL,
                          f"returned {ok}, governor is {now!r}", sec)
            except GLib.Error as exc:
                self._add("set_same_governor",
                          "SetGovernor to the value it already has", FAIL,
                          _dbus_error_message(exc), sec)
            finally:
                # SetGovernor snapshots even when it changes nothing, so the
                # state file has to be cleared or the next run refuses to start.
                with contextlib.suppress(GLib.Error):
                    self.helper.call("RevertAll")

    def _neutral_args(self, method: str) -> GLib.Variant | None:
        """Arguments that reach the authorization check and do as little as
        possible if it grants.

        Most probes are inert because the value is refused by validation after
        the polkit check - an unknown governor, a key outside the allowlist, a
        fan duty under the floor. `SetNvidiaModeset` is the exception: it takes
        a bool, and BOTH values write /etc/modprobe.d. So it is probed with the
        value already on disk, which makes the write a byte-identical no-op,
        and skipped entirely when the file does not exist rather than creating
        one as a side effect of a test.
        """
        if method != "SetNvidiaModeset":
            return _NEUTRAL_ARGS.get(method)
        text = _read(NVIDIA_MODESET_CONF)
        if text is None:
            raise _NotProbeable(
                f"{NVIDIA_MODESET_CONF} does not exist, and SetNvidiaModeset takes "
                "a bool - either value would CREATE it. Refusing to change the "
                "machine's boot configuration to observe a polkit action.")
        current = "modeset=1" in text
        return GLib.Variant("(b)", (current,))

    def check_polkit_routing(self, enabled: bool):
        """Which polkit action does each mutating method actually demand?

        Answered by eavesdropping the helper's own CheckAuthorization call,
        which is the only way to see the action id from outside. Eavesdropping
        needs root, so this is its own mode.
        """
        sec = "polkit routing"
        if not enabled:
            return self._add("polkit_routing", "Each method demands the right action",
                             SKIP,
                             "needs to eavesdrop the system bus, which needs root: "
                             "sudo python3 tests/conformance/helper.py --polkit-routing",
                             sec)
        if os.geteuid() != 0:
            return self._add("polkit_routing", "Each method demands the right action",
                             SKIP, "--polkit-routing needs root to call BecomeMonitor",
                             sec)
        try:
            monitor = _PolkitMonitor()
        except GLib.Error as exc:
            return self._add("polkit_routing", "Each method demands the right action",
                             SKIP, f"could not become a bus monitor: "
                             f"{_dbus_error_message(exc)}", sec)

        for method, expected in sorted(EXPECTED_ACTION.items()):
            if _prompts(method) and not self.allow_prompts:
                self._add(f"action_{method}", f"{method} -> {expected}", SKIP,
                          f"{expected} is auth_admin_keep: calling this raises a "
                          "password dialog on the desktop. Pass --prompts, while "
                          "watching the screen, to include it.", sec)
                continue
            try:
                params = self._neutral_args(method)
            except _NotProbeable as exc:
                self._add(f"action_{method}", f"{method} -> {expected}", SKIP,
                          str(exc), sec)
                continue
            monitor.clear()
            # The verdict does not matter: the action id is read out of the
            # CheckAuthorization call, which happens first. Under sudo the
            # session is inactive so most of these are denied, which is exactly
            # why nothing gets mutated here.
            with contextlib.suppress(GLib.Error):
                self.helper.call(method, params, timeout_ms=8000)
            seen = monitor.actions(timeout_s=2.0)
            if not seen:
                self._add(f"action_{method}", f"{method} -> {expected}", FAIL,
                          "no CheckAuthorization was observed - either the method "
                          "is not authorized at all, or the monitor missed it", sec)
            elif expected in seen:
                extra = [a for a in seen if a != expected]
                self._add(f"action_{method}", f"{method} -> {expected}",
                          PASS if not extra else INFO,
                          expected + (f" (also saw {extra})" if extra else ""),
                          sec, actions=sorted(seen))
            else:
                self._add(f"action_{method}", f"{method} -> {expected}", FAIL,
                          f"demanded {sorted(seen)}, contract says {expected}. A "
                          "method on a more permissive action than it should be is "
                          "a privilege boundary bug.", sec, actions=sorted(seen))
        monitor.close()

    # -- run -------------------------------------------------------------
    def run(self, polkit_routing: bool = False):
        self.check_interface()
        if any(r.name == "bus" and r.status == FAIL for r in self.results):
            return
        self.check_read_only()
        self.check_rejections()
        self.check_renice_ownership()
        self.check_snapshot_and_revert()
        self.check_idempotency()
        self.judge_authorization()
        self.check_polkit_routing(polkit_routing)

    def judge_authorization(self):
        """Interpret the pattern of denials, once, at the end.

        Being refused the two `auth_admin_keep` actions in a shell with no
        polkit agent is ordinary and expected. Being refused
        `manage-performance` is not: the policy grants it outright to an
        active local session, so a denial means this caller is not one - an
        SSH session, a sudo shell, or a service - and every mutating result
        above is about the session, not about the helper.
        """
        sec = "Authorization"
        if not self.denied_actions:
            return
        if POLKIT_PERF in self.denied_actions:
            return self._add(
                "authorization", "polkit grants this caller manage-performance",
                FAIL,
                "denied. The policy grants this action to an ACTIVE local session "
                "with no prompt, so a refusal means this caller is not one - an "
                "SSH session, a sudo shell or a service. Every mutating check "
                "above is measuring that, not the helper. Re-run as your own user "
                "from your desktop.", sec)
        prompted = sorted(self.denied_actions)
        return self._add(
            "authorization", "polkit grants this caller manage-performance", PASS,
            "granted without a prompt, so this is an active local session. The "
            f"checks that skipped needed {prompted}, which the policy puts behind "
            "auth_admin_keep - they need a desktop with a polkit agent to answer "
            "the prompt.", sec)


#: arguments that are valid enough to reach the authorization check but do as
#: little as possible if they somehow get past it.
_NEUTRAL_ARGS = {
    "SetGovernor": GLib.Variant("(s)", ("__conformance_probe__",)),
    "SetEPP": GLib.Variant("(s)", ("__conformance_probe__",)),
    "Renice": GLib.Variant("(ui)", (1, 0)),
    "SetPowerLimits": GLib.Variant("(tt)", (1, 1)),
    "ResetPowerLimits": None,
    "SetTDP": GLib.Variant("(u)", (0,)),
    "ResetTDP": None,
    "RevertAll": None,
    "SetSysctl": GLib.Variant("(ss)", ("__not.a.key__", "0")),
    "RevertSysctl": GLib.Variant("(s)", ("__not.a.key__",)),
    "ApplyUndervolt": None,
    "ApplyAmdUndervolt": None,
    "SetNvidiaModeset": GLib.Variant("(b)", (True,)),
    "SpinUpFans": GLib.Variant("(u)", (0,)),
    "ResetFans": None,
}


class _PolkitMonitor:
    """Records the action id of every polkit CheckAuthorization on the bus."""

    _RULE = ("type='method_call',interface='org.freedesktop.PolicyKit1.Authority',"
             "member='CheckAuthorization'")

    def __init__(self):
        self.bus = Gio.DBusConnection.new_for_address_sync(
            Gio.dbus_address_get_for_bus_sync(Gio.BusType.SYSTEM, None),
            Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
            | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
            None, None,
        )
        self.bus.call_sync(
            "org.freedesktop.DBus", "/org/freedesktop/DBus",
            "org.freedesktop.DBus.Monitoring", "BecomeMonitor",
            GLib.Variant("(asu)", ([self._RULE], 0)),
            None, Gio.DBusCallFlags.NONE, 5000, None,
        )
        self._seen: list[str] = []
        self.bus.add_filter(self._on_message)

    def _on_message(self, _conn, message, _incoming):
        try:
            if message.get_member() == "CheckAuthorization":
                body = message.get_body()
                if body is not None:
                    self._seen.append(body.unpack()[1])
        except Exception:  # noqa: BLE001 - a bus filter must never raise
            # This runs inside GLib's message dispatch. Letting anything
            # escape here takes down the monitor connection mid-run and the
            # remaining methods would all report "no CheckAuthorization seen",
            # which reads as a routing bug that is not there.
            pass
        return None  # a monitor consumes nothing

    def clear(self):
        self._seen.clear()

    def actions(self, timeout_s: float = 2.0) -> set[str]:
        ctx = GLib.MainContext.default()
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if not ctx.iteration(False):
                time.sleep(0.02)
            if self._seen:
                break
        while ctx.pending():
            ctx.iteration(False)
        return set(self._seen)

    def close(self):
        with contextlib.suppress(GLib.Error):
            self.bus.close_sync(None)


# --------------------------------------------------------------------------
# output
# --------------------------------------------------------------------------
_COLOUR = {PASS: "\033[32m", FAIL: "\033[31m", SKIP: "\033[33m", INFO: "\033[36m"}


def complementary_run() -> str:
    """The other run, for the checks this one structurally cannot grade.

    A root run and an unprivileged run cover DISJOINT sets, and neither alone
    is complete - which is easy to miss, because each one looks like the whole
    suite. Root is needed to read the root-only state directory and to
    eavesdrop the bus for polkit routing; unprivileged is the only way to see
    the ownership gate at all, because renice() skips it for uid 0. So the run
    that can check the most is also the one that cannot check who you are.
    """
    if os.geteuid() == 0:
        return (
            "Some checks CANNOT be graded from a root run: renice() skips the "
            "ownership check for uid 0, so the gate that protects other users "
            "is invisible here. For those, also run:\n"
            "    python3 tests/conformance/helper.py --apply"
        )
    return (
        "Some checks need root: reading the root-only state directory, and "
        "eavesdropping the bus to confirm polkit routing. For those, also run, "
        "while watching the screen for password dialogs:\n"
        "    sudo python3 tests/conformance/helper.py --apply --polkit-routing "
        "--prompts"
    )


def render(results: list[Result], caps: dict, use_colour: bool) -> str:
    out = []
    sections: dict[str, list[Result]] = {}
    for r in results:
        sections.setdefault(r.section, []).append(r)
    for section, rows in sections.items():
        out.append(f"\n{section}")
        out.append("-" * len(section))
        for r in rows:
            tag = r.status
            if use_colour:
                tag = f"{_COLOUR.get(r.status, '')}{r.status}\033[0m"
            out.append(f"  {tag}  {r.title}")
            for line in r.detail.splitlines():
                out.append(f"        {line}")
    counts = {s: sum(1 for r in results if r.status == s)
              for s in (PASS, FAIL, SKIP, INFO)}
    out.append("")
    out.append(f"{counts[PASS]} PASS  {counts[FAIL]} FAIL  "
               f"{counts[SKIP]} SKIP  {counts[INFO]} INFO")
    if counts[SKIP]:
        out.append("A SKIP is not a pass. Each one above says what is missing.")
        out.append(complementary_run())
    if caps["session_active"] is False and os.geteuid() != 0:
        out.append("NOTE: this is not an active local session, so polkit will not "
                   "grant manage-performance without a prompt.")
    elif caps["session_active"] is None and os.geteuid() != 0:
        out.append("NOTE: could not tell whether this is an active local session "
                   "(loginctl knows no session for this process). polkit resolves "
                   "the subject from the bus name's pid and may well authorize it "
                   "anyway - the Authorization section above is the real answer.")
    return "\n".join(out)


def build_parser() -> argparse.ArgumentParser:
    """Split out from main() so the suggestions in `complementary_run` can be
    parsed back and proved runnable - a renamed flag should break a test, not
    quietly leave the summary pointing somebody at a command that no longer
    exists."""
    ap = argparse.ArgumentParser(
        description="Conformance suite for the goblin-mode-pro privileged helper.")
    ap.add_argument("--apply", action="store_true",
                    help="run the checks that really change the machine and put "
                         "it back (governor round-trip, idempotency)")
    ap.add_argument("--polkit-routing", action="store_true",
                    help="assert which polkit action each method demands; needs "
                         "root, and expects its own calls to be denied")
    ap.add_argument("--prompts", action="store_true",
                    help="include the checks whose polkit action is "
                         "auth_admin_keep. These raise a password dialog on "
                         "your desktop - run this only while you are watching "
                         "the screen, because three dismissed dialogs lock the "
                         "account out of sudo for ten minutes (pam_faillock)")
    ap.add_argument("--json", action="store_true",
                    help="machine-readable results plus the capability report")
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    suite = Conformance(apply=args.apply, allow_prompts=args.prompts)
    suite.run(polkit_routing=args.polkit_routing)

    failed = sum(1 for r in suite.results if r.status == FAIL)
    if args.json:
        print(json.dumps({
            "capabilities": suite.caps,
            "results": [vars(r) for r in suite.results],
            "summary": {
                s: sum(1 for r in suite.results if r.status == s)
                for s in (PASS, FAIL, SKIP, INFO)
            },
        }, indent=2))
    else:
        print(render(suite.results, suite.caps, sys.stdout.isatty()))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
