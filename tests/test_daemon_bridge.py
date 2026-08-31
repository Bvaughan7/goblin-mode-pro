"""The session-bus bridge - the spine both the GUI and the CLI talk through.

528 lines with no test of its own until now. What lives here is a hand-written
`elif` chain dispatching 28 D-Bus methods, and the failure mode is nasty and
silent: a method declared in the introspection XML with no branch to handle it
never replies, so the caller blocks until D-Bus times out ~25 s later. Nothing
raises, nothing logs, the GUI just hangs.

These drive `_handle_call` directly with a recording handler and a fake
invocation, and pump a real GLib main context so the threaded `_async_str`
replies land too. No bus is needed, so this runs in the ordinary suite.
"""

from __future__ import annotations

import json
import logging
import time
import warnings
import unittest
import xml.etree.ElementTree as ET

from tests._support import _SRC  # noqa: F401

warnings.filterwarnings("ignore", category=DeprecationWarning)

import gi  # noqa: E402

gi.require_version("Gio", "2.0")
from gi.repository import GLib  # noqa: E402

from goblinmode.ipc import daemon_bridge as db  # noqa: E402

logging.getLogger("goblinmode.ipc.daemon_bridge").setLevel(logging.CRITICAL)

# pumping a GLib main context makes PyGObject touch asyncio's deprecated
# get_event_loop_policy(); it is not ours to fix and it drowns the output
warnings.filterwarnings("ignore", category=DeprecationWarning)


def _interface() -> ET.Element:
    return ET.fromstring(db.INTROSPECTION_XML).find("interface")


def _methods() -> dict[str, tuple[list[str], list[str]]]:
    """{name: (in type codes, out type codes)} from the interface XML."""
    out = {}
    for m in _interface().findall("method"):
        ins = [a.get("type") for a in m.findall("arg")
               if a.get("direction") != "out"]
        outs = [a.get("type") for a in m.findall("arg")
                if a.get("direction") == "out"]
        out[m.get("name")] = (ins, outs)
    return out


class _Handler:
    """Records every call, and answers with the shape the bridge expects."""

    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def record(*args):
            self.calls.append((name, args))
            # the bridge json.dumps() dict/list returns and passes str through
            if name in ("export_last_incident", "write_wrapper", "build_report",
                        "export_setup"):
                return "text"
            if name in ("get_metrics", "get_incidents", "get_sessions",
                        "get_session_history", "run_preflight", "analyze_log"):
                return [{"a": 1}]
            if name in ("get_status", "get_health", "apply_preflight_fixes",
                        "get_system_info", "get_proton_info",
                        "clear_shader_cache", "revert_preflight_fix",
                        "get_nvidia_module_state", "build_works_for_me"):
                return {"a": 1}
            return True
        return record


class _Invocation:
    """Stands in for Gio.DBusMethodInvocation."""

    def __init__(self):
        self.value = None
        self.error = None

    def return_value(self, variant):
        self.value = variant

    def return_dbus_error(self, name, message):
        self.error = (name, message)

    @property
    def answered(self) -> bool:
        return self.value is not None or self.error is not None


def _params(codes: list[str]):
    """A GLib.Variant tuple of dummy args matching the declared signature."""
    if not codes:
        return None
    vals = []
    for c in codes:
        # "{}" is valid JSON *and* a valid plain string, so it works for
        # SetProfile (which json.loads it) and for path/name args alike
        vals.append(True if c == "b" else "{}")
    return GLib.Variant(f"({''.join(codes)})", tuple(vals))


def _drive(inv: _Invocation, timeout: float = 5.0) -> None:
    """Pump the main context until the invocation is answered.

    The warnings filter is set here rather than at module scope because
    unittest resets filters per test: pumping the context makes PyGObject
    touch asyncio's deprecated get_event_loop_policy(), which is not ours to
    fix and otherwise buries the suite output.
    """
    ctx = GLib.MainContext.default()
    deadline = time.monotonic() + timeout
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        while not inv.answered and time.monotonic() < deadline:
            ctx.iteration(False)
            time.sleep(0.005)


class InterfaceConsistency(unittest.TestCase):
    """The XML, the dispatch chain and the handler protocol must agree.

    A method in the XML with no branch does not error - it never replies, and
    the caller hangs until the D-Bus timeout. That is the bug this catches.
    """

    def _dispatched(self) -> set[str]:
        import re
        src = __import__("pathlib").Path(db.__file__).read_text()
        body = src[src.index("def _handle_call"):src.index("def _async_str")]
        return set(re.findall(r'method == "(\w+)"', body))

    def test_the_xml_parses_and_names_the_right_interface(self):
        self.assertEqual(_interface().get("name"), db.IFACE)

    def test_every_declared_method_is_dispatched(self):
        missing = set(_methods()) - self._dispatched()
        self.assertEqual(missing, set(),
                         f"declared but never handled - the caller would hang: {missing}")

    def test_no_branch_dispatches_an_undeclared_method(self):
        extra = self._dispatched() - set(_methods())
        self.assertEqual(extra, set(), f"handled but not declared: {extra}")

    def test_every_declared_signal_has_an_emitter(self):
        import re
        src = __import__("pathlib").Path(db.__file__).read_text()
        declared = {s.get("name") for s in _interface().findall("signal")}
        emitted = set(re.findall(r'self\._emit\("(\w+)"', src))
        self.assertEqual(declared, emitted)


class Dispatch(unittest.TestCase):
    """Every method reaches the handler and produces a well-formed reply."""

    def setUp(self):
        self.handler = _Handler()
        self.bridge = db.DaemonBridge(self.handler)

    def _call(self, name, codes):
        inv = _Invocation()
        self.bridge._handle_call(None, ":1.0", db.OBJECT_PATH, db.IFACE,
                                 name, _params(codes), inv)
        _drive(inv)
        return inv

    def test_every_method_replies_and_matches_its_declared_signature(self):
        """The sweep. Any branch that forgets to reply fails here."""
        for name, (ins, outs) in sorted(_methods().items()):
            with self.subTest(method=name):
                inv = self._call(name, ins)
                self.assertTrue(inv.answered, f"{name} never replied")
                self.assertIsNone(inv.error, f"{name} errored: {inv.error}")
                got = inv.value.get_type_string()
                self.assertEqual(got, f"({''.join(outs)})",
                                 f"{name} replied {got}, XML says ({''.join(outs)})")

    def test_every_method_actually_reaches_the_handler(self):
        for name, (ins, _outs) in sorted(_methods().items()):
            with self.subTest(method=name):
                self.handler.calls.clear()
                self._call(name, ins)
                self.assertTrue(self.handler.calls,
                                f"{name} replied without calling the handler")

    def test_json_methods_return_parseable_json(self):
        for name in ("GetStatus", "GetMetrics", "GetIncidents", "GetSessions"):
            with self.subTest(method=name):
                inv = self._call(name, _methods()[name][0])
                json.loads(inv.value.unpack()[0])

    def test_set_profile_forwards_the_decoded_dict(self):
        inv = _Invocation()
        self.bridge._handle_call(
            None, ":1.0", db.OBJECT_PATH, db.IFACE, "SetProfile",
            GLib.Variant("(s)", (json.dumps({"exe": "Wow.exe"}),)), inv)
        _drive(inv)
        name, args = self.handler.calls[-1]
        self.assertEqual(name, "set_profile")
        self.assertEqual(args[0], {"exe": "Wow.exe"})

    def test_an_unknown_method_does_not_hang_the_caller(self):
        inv = self._call("NoSuchMethod", [])
        self.assertTrue(inv.answered,
                        "an unknown method must be answered, not left to time out")


class HandlerFailures(unittest.TestCase):
    """A handler that raises must produce a D-Bus error, never silence."""

    class _Boom:
        def __getattr__(self, name):
            def raise_it(*_a):
                raise RuntimeError("handler exploded")
            return raise_it

    def setUp(self):
        self.bridge = db.DaemonBridge(self._Boom())

    def test_a_raising_sync_handler_returns_an_error(self):
        inv = _Invocation()
        self.bridge._handle_call(None, ":1.0", db.OBJECT_PATH, db.IFACE,
                                 "GetStatus", None, inv)
        _drive(inv)
        self.assertTrue(inv.answered, "a raising handler must still reply")
        self.assertIsNotNone(inv.error)
        self.assertIn("exploded", inv.error[1])

    def test_a_raising_async_handler_returns_an_error(self):
        """The threaded path: the exception happens off the main loop."""
        inv = _Invocation()
        self.bridge._async_str(inv, lambda: (_ for _ in ()).throw(
            RuntimeError("async exploded")))
        _drive(inv)
        self.assertIsNotNone(inv.error, "async failure must not hang the caller")
        self.assertIn("async exploded", inv.error[1])

    def test_async_success_replies_with_the_string(self):
        inv = _Invocation()
        self.bridge._async_str(inv, lambda: "done")
        _drive(inv)
        self.assertIsNone(inv.error)
        self.assertEqual(inv.value.unpack()[0], "done")


class SignalEmission(unittest.TestCase):
    def test_emitting_without_a_connection_is_a_no_op(self):
        """The daemon emits status on every poll; before publish() there is no
        connection, and that must not raise on a background thread."""
        bridge = db.DaemonBridge(_Handler())
        self.assertIsNone(bridge._conn)
        bridge.emit_status({"a": 1})
        bridge.emit_metrics({"a": 1})
        bridge.emit_incident({"a": 1})
        bridge.emit_detected({"a": 1})
        bridge.emit_session({"a": 1})


if __name__ == "__main__":
    unittest.main()
