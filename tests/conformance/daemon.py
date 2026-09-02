#!/usr/bin/env python3
"""Conformance suite for the goblin-mode-pro daemon's session-bus interface.

Drives the daemon from OUTSIDE, over the session bus, and never imports its
source - the same rule the helper suite follows, and for the same reason: a
suite that imports what it grades can only ever agree with it. The method list
and every declared signature come from docs/dbus-daemon-interface-v1.xml, which
is a contract document rather than an implementation.

    python3 tests/conformance/daemon.py            # read-only, safe any time
    python3 tests/conformance/daemon.py --apply    # adds reversible round-trips

WHAT THIS WILL NOT DO, EVEN WITH --apply
----------------------------------------
Some methods on this interface destroy or persist things a user cares about,
and a conformance run is not worth any of them:

  SetProfile / RemoveProfile   rewrite the user's own per-game settings
  ClearShaderCache             deletes files
  SetNvidiaModeset             writes boot configuration; needs a reboot
  ApplyPreflightFixes /
  RevertPreflightFix           change kernel tunables

They are always SKIPped, with the reason. The behaviour behind the last three
is graded at the other seam, by tests/conformance/helper.py, where it can be
applied and reverted against a snapshot.

Unlike the helper's interface, NOTHING here is authorized. The daemon runs as
the user, so anything in the user's session can call it - that is the correct
trust model for a per-user service, and it is why the privileged operations
live on the other interface rather than this one.
"""

from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _report import (  # noqa: E402
    FAIL,
    INFO,
    PASS,
    SKIP,
    Result,
    counts,
    dbus_error_message,
    dbus_error_name,
)
from _report import render as _render_report  # noqa: E402

_REPO = Path(__file__).resolve().parent.parent.parent
FROZEN = _REPO / "docs" / "dbus-daemon-interface-v1.xml"

BUS_NAME = "com.goblinmode.Pro.Daemon"
OBJECT_PATH = "/com/goblinmode/Pro/Daemon"
IFACE = "com.goblinmode.Pro.Daemon"
ERR_FAILED = f"{IFACE}.Failed"

#: Methods that only read. Safe to call on a live daemon at any time.
READ_ONLY = {
    "GetStatus", "GetMetrics", "GetIncidents", "GetSessions", "GetHealth",
    "GetSystemInfo", "GetProtonInfo", "GetNvidiaModuleState", "RunPreflight",
    "ExportSetup", "ExportLastIncident", "AnalyzeLog",
}

#: Read-only, but they take an argument, so they are called explicitly below.
READ_ONLY_WITH_ARGS = {"GetSessionHistory", "BuildReport", "BuildWorksForMe"}

#: Never called by this suite. The value is the reason, printed as the SKIP.
NEVER = {
    "SetProfile": "it rewrites the user's own per-game settings",
    "RemoveProfile": "it deletes one of the user's per-game settings",
    "ClearShaderCache": "it deletes files",
    "SetNvidiaModeset": "it writes boot configuration and needs a reboot to take effect",
    "ApplyPreflightFixes": "it changes kernel tunables; graded at the helper seam instead",
    "RevertPreflightFix": "it changes kernel tunables; graded at the helper seam instead",
    "IgnoreGame": (
        "it is IRREVERSIBLE over this interface - nothing removes an entry from "
        "ignored_games, so a probe would be permanent. KeepGame is not its "
        "inverse: it clears auto_created on a profile and never touches that list"
    ),
}

#: Reversible, and only with --apply. Each is restored to what it was.
ROUND_TRIP = {"SetMasterEnabled", "SetAutoDetect", "ForceBoost",
              "KeepGame", "ArmBenchmark", "WriteWrapper"}

#: An executable name no real game will have, for the methods that take one.
SENTINEL_EXE = "__gmp_conformance_probe__"


def frozen_signatures() -> dict[str, tuple[str, str]]:
    """{method: (in_signature, out_signature)} from the frozen contract."""
    root = ET.fromstring(FROZEN.read_text())
    node = next(n for n in root.iter("interface") if n.get("name") == IFACE)
    out = {}
    for method in node.findall("method"):
        ins = "".join(a.get("type", "") for a in method.findall("arg")
                      if a.get("direction", "in") == "in")
        outs = "".join(a.get("type", "") for a in method.findall("arg")
                       if a.get("direction") == "out")
        out[method.get("name") or ""] = (ins, outs)
    return out


class Daemon:
    """A proxy that never auto-starts the daemon.

    DO_NOT_AUTO_START matters: activating the service would mean grading a
    daemon this suite started, in a state no user ever has.
    """

    def __init__(self) -> None:
        self._proxy = Gio.DBusProxy.new_for_bus_sync(
            Gio.BusType.SESSION, Gio.DBusProxyFlags.DO_NOT_AUTO_START,
            None, BUS_NAME, OBJECT_PATH, IFACE, None,
        )
        if self._proxy.get_name_owner() is None:
            raise RuntimeError(f"{BUS_NAME} has no owner")

    def call(self, method: str, params: GLib.Variant | None = None,
             timeout_ms: int = 30_000) -> GLib.Variant:
        return self._proxy.call_sync(
            method, params, Gio.DBusCallFlags.NONE, timeout_ms, None)

    def property(self, name: str):
        value = self._proxy.get_cached_property(name)
        return None if value is None else value.unpack()


class Conformance:
    def __init__(self, apply: bool) -> None:
        self.apply = apply
        self.results: list[Result] = []
        self.signatures = frozen_signatures()
        self.daemon: Daemon | None = None

    def _add(self, name, title, status, detail, section="General", **observed):
        self.results.append(Result(name=name, title=title, status=status,
                                   detail=detail, section=section,
                                   observed=observed))

    # -- sections ---------------------------------------------------------
    def probe_interface(self) -> None:
        sec = "Interface"
        try:
            self.daemon = Daemon()
        except Exception as exc:                              # noqa: BLE001
            self._add("on_bus", "Daemon is on the session bus", FAIL,
                      f"{type(exc).__name__}: {exc} - start it with "
                      "`systemctl --user start goblin-mode-pro`; every check "
                      "below needs it", sec)
            return
        self._add("on_bus", "Daemon is on the session bus", PASS,
                  f"{BUS_NAME} at {OBJECT_PATH}", sec)

        served = self.daemon.call("org.freedesktop.DBus.Introspectable.Introspect") \
            if False else None
        # Introspection is compared byte for byte by
        # tests/test_daemon_interface_freeze.py, which can do it without a live
        # bus. Repeating it here would add a second place to keep in step.
        self._add("frozen_contract", "Frozen contract is the source of truth", INFO,
                  f"{len(self.signatures)} methods read from "
                  f"{FROZEN.relative_to(_REPO)}; the byte comparison lives in "
                  "tests/test_daemon_interface_freeze.py", sec)

    def probe_identity(self) -> None:
        sec = "Identity"
        if not self.daemon:
            return
        values = {n: self.daemon.property(n)
                  for n in ("Version", "InterfaceVersion", "Implementation")}
        if all(v is None for v in values.values()):
            self._add("identity", "Reports which implementation is answering", SKIP,
                      "the daemon serves no identity properties, so it predates "
                      "them - upgrade it to tell one implementation from another "
                      "in a bug report", sec)
            return
        self._add("identity", "Reports which implementation is answering", PASS,
                  f"{values['Implementation']} daemon v{values['Version']}, "
                  f"interface v{values['InterfaceVersion']}", sec, **values)
        if values["InterfaceVersion"] != 1:
            self._add("interface_version", "Interface version is still 1", FAIL,
                      f"got {values['InterfaceVersion']}; a v2 means callers have "
                      "to care which implementation answered, which the freeze "
                      "exists to prevent", sec)

    def probe_read_only(self) -> None:
        """Every no-argument reader replies, with the declared type."""
        sec = "Read-only methods"
        if not self.daemon:
            return
        for method in sorted(READ_ONLY):
            ins, outs = self.signatures.get(method, ("", ""))
            if ins:
                continue
            self._call_and_check(method, None, outs, sec)

        for method, params in (
            ("GetSessionHistory", GLib.Variant("(s)", (SENTINEL_EXE,))),
            ("BuildReport", GLib.Variant("(s)", ("conformance probe",))),
            ("BuildWorksForMe", GLib.Variant("(ss)", (SENTINEL_EXE, "probe"))),
        ):
            _ins, outs = self.signatures.get(method, ("", ""))
            self._call_and_check(method, params, outs, sec)

    def _call_and_check(self, method, params, out_signature, section) -> None:
        try:
            reply = self.daemon.call(method, params)
        except GLib.Error as exc:
            self._add(method.lower(), method, FAIL,
                      f"{dbus_error_name(exc)}: {dbus_error_message(exc)}", section)
            return
        got = reply.get_type_string()
        want = f"({out_signature})"
        if got != want:
            self._add(method.lower(), method, FAIL,
                      f"replied {got}, the contract says {want}", section)
            return
        detail = f"{got}"
        # A method that promises JSON has to deliver JSON. The signature says
        # "s", which a plain error string would also satisfy.
        if out_signature == "s" and method not in {"ExportSetup", "BuildReport",
                                                   "BuildWorksForMe",
                                                   "ExportLastIncident"}:
            payload = reply.unpack()[0]
            try:
                json.loads(payload)
                detail += f", {len(payload)} bytes of valid JSON"
            except json.JSONDecodeError as exc:
                self._add(method.lower(), method, FAIL,
                          f"declared a string and returned something that is not "
                          f"JSON: {exc}", section)
                return
        self._add(method.lower(), method, PASS, detail, section)

    def probe_mutating(self) -> None:
        sec = "Methods that change something"
        if not self.daemon:
            return
        for method, reason in sorted(NEVER.items()):
            self._add(f"never_{method.lower()}", method, SKIP,
                      f"never called by this suite: {reason}", sec)

        if not self.apply:
            for method in sorted(ROUND_TRIP):
                self._add(f"rt_{method.lower()}", method, SKIP,
                          "changes daemon state; pass --apply to round-trip it", sec)
            return

        self._round_trip_toggle("SetMasterEnabled", "master_enabled", sec)
        self._round_trip_toggle("SetAutoDetect", "auto_detect", sec)
        self._force_boost(sec)
        self._sentinel_pair(sec)
        self._write_wrapper(sec)

    def _status(self) -> dict:
        try:
            return json.loads(self.daemon.call("GetStatus").unpack()[0])
        except Exception:                                     # noqa: BLE001
            return {}

    def _round_trip_toggle(self, method: str, key: str, sec: str) -> None:
        """Set it to the value it already has, then confirm nothing moved."""
        before = self._status().get(key)
        if before is None:
            self._add(f"rt_{method.lower()}", method, SKIP,
                      f"GetStatus does not report {key!r}, so there is nothing to "
                      "restore it to and this will not guess", sec)
            return
        try:
            ok = self.daemon.call(method, GLib.Variant("(b)", (bool(before),))).unpack()[0]
        except GLib.Error as exc:
            self._add(f"rt_{method.lower()}", method, FAIL,
                      f"{dbus_error_name(exc)}: {dbus_error_message(exc)}", sec)
            return
        after = self._status().get(key)
        if not ok or after != before:
            self._add(f"rt_{method.lower()}", method, FAIL,
                      f"returned {ok} and {key} went {before!r} -> {after!r}", sec)
            return
        self._add(f"rt_{method.lower()}", method, PASS,
                  f"set to its current value ({before!r}) and it stayed there", sec)

    def _force_boost(self, sec: str) -> None:
        """On, then back off. Left on, this would pin the machine's governor."""
        try:
            self.daemon.call("ForceBoost", GLib.Variant("(b)", (True,)))
            on = self._status().get("forced_boost")
            self.daemon.call("ForceBoost", GLib.Variant("(b)", (False,)))
            off = self._status().get("forced_boost")
        except GLib.Error as exc:
            self._add("rt_forceboost", "ForceBoost", FAIL,
                      f"{dbus_error_name(exc)}: {dbus_error_message(exc)}", sec)
            return
        if on is True and off is False:
            self._add("rt_forceboost", "ForceBoost", PASS,
                      "on then off, and GetStatus followed both ways", sec)
        else:
            self._add("rt_forceboost", "ForceBoost", FAIL,
                      f"GetStatus reported forced_boost={on} then {off}; the "
                      "machine may still be boosted", sec)

    def _sentinel_pair(self, sec: str) -> None:
        """KeepGame on a name no real game has, and the asymmetry beside it.

        KeepGame is safe on an unknown exe: it looks up a profile, finds none,
        and does nothing. IgnoreGame is NOT safe, because nothing takes an
        entry back out of ignored_games - see NEVER.
        """
        try:
            kept = self.daemon.call(
                "KeepGame", GLib.Variant("(s)", (SENTINEL_EXE,))).unpack()[0]
        except GLib.Error as exc:
            self._add("rt_keepgame", "KeepGame", FAIL,
                      f"{dbus_error_name(exc)}: {dbus_error_message(exc)}", sec)
            return
        self._add("rt_keepgame", "KeepGame", PASS if kept else FAIL,
                  f"kept={kept} for {SENTINEL_EXE!r}, which has no profile, so "
                  "this is a no-op by design", sec)
        try:
            armed = self.daemon.call(
                "ArmBenchmark", GLib.Variant("(s)", (SENTINEL_EXE,))).unpack()[0]
            self._add("rt_armbenchmark", "ArmBenchmark", PASS if armed else FAIL,
                      f"armed={armed} for a game that will never launch", sec)
        except GLib.Error as exc:
            self._add("rt_armbenchmark", "ArmBenchmark", FAIL,
                      f"{dbus_error_name(exc)}: {dbus_error_message(exc)}", sec)

    def _write_wrapper(self, sec: str) -> None:
        """Idempotent: the daemon writes this file at every startup anyway."""
        try:
            path = self.daemon.call("WriteWrapper").unpack()[0]
        except GLib.Error as exc:
            self._add("rt_writewrapper", "WriteWrapper", FAIL,
                      f"{dbus_error_name(exc)}: {dbus_error_message(exc)}", sec)
            return
        exists = Path(path).is_file() if path else False
        self._add("rt_writewrapper", "WriteWrapper", PASS if exists else FAIL,
                  f"wrote {path or '(nothing)'}" + ("" if exists else " - which is not there"),
                  sec)

    def probe_errors(self) -> None:
        sec = "Rejections"
        if not self.daemon:
            return
        try:
            self.daemon.call("NoSuchMethod")
            self._add("unknown_method", "An unknown method is refused by name", FAIL,
                      "it returned a reply instead of an error", sec)
        except GLib.Error as exc:
            name = dbus_error_name(exc)
            expected = "org.freedesktop.DBus.Error.UnknownMethod"
            self._add("unknown_method", "An unknown method is refused by name",
                      PASS if name == expected else FAIL,
                      f"{name}: {dbus_error_message(exc)}", sec)

    def probe_game_state(self) -> None:
        """What this suite cannot reach on its own."""
        sec = "Game state"
        if not self.daemon:
            return
        status = self._status()
        active = status.get("active_games") or status.get("games") or []
        self._add("current_games", "Games the daemon currently sees", INFO,
                  f"{len(active) if isinstance(active, list) else active} - the "
                  "checks above ran against this state", sec,
                  games=active if isinstance(active, list) else None)
        ignored = status.get("ignored_games")
        if isinstance(ignored, list):
            self._add("ignore_is_one_way", "Ignoring a game can be undone", FAIL,
                      f"it cannot. ignored_games has {len(ignored)} entr"
                      f"{'y' if len(ignored) == 1 else 'ies'} and nothing on this "
                      "interface removes one - IgnoreGame only appends, and "
                      "KeepGame clears auto_created on a profile instead. A user "
                      "who ignores a game by mistake has to hand-edit "
                      "~/.config/goblin-mode-pro/config.json to get it back", sec,
                      ignored_games=ignored)
        self._add("two_games", "Behaviour with two games at once", SKIP,
                  "needs two real game processes running. This is where the "
                  "refcounted global tweaks and the power-leak fix live, so it is "
                  "the most valuable thing here and the least reachable from "
                  "outside - verify it by playing two games, not from a suite", sec)


def footer(results: list[Result]) -> list[str]:
    lines: list[str] = []
    if counts(results)[SKIP]:
        lines.append(
            "Methods that rewrite settings, delete files or change boot config are "
            "never called here - see the list at the top of this file. The three "
            "kernel-tunable ones are graded by tests/conformance/helper.py instead."
        )
    return lines


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Conformance suite for the goblin-mode-pro daemon interface.")
    ap.add_argument("--apply", action="store_true",
                    help="also round-trip the reversible state-changing methods, "
                         "restoring each to what it was")
    ap.add_argument("--json", action="store_true",
                    help="machine-readable results")
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    suite = Conformance(apply=args.apply)
    suite.probe_interface()
    suite.probe_identity()
    suite.probe_read_only()
    suite.probe_mutating()
    suite.probe_errors()
    suite.probe_game_state()

    failed = sum(1 for r in suite.results if r.status == FAIL)
    if args.json:
        print(json.dumps({
            "results": [vars(r) for r in suite.results],
            "summary": counts(suite.results),
        }, indent=2))
    else:
        print(_render_report(suite.results, sys.stdout.isatty(), footer(suite.results)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
