"""The daemon still serves the frozen v1 session-bus interface, byte for byte.

The second of this project's two seams. `docs/dbus-interface-v1.xml` is between
the daemon and the root helper; this one is between the GUI/CLI and the daemon,
and it is the larger surface - 28 methods and 5 signals against the helper's 19
methods.

Freezing it does not mean the daemon is being rewritten. It means a change to
what the GUI is allowed to assume has to be deliberate rather than incidental,
and that any future implementation has something exact to be measured against.

tests/test_daemon_bridge.py already checks that every declared method is
dispatched and replies with its declared type. This file checks something
different and narrower: that the declaration itself has not moved.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
for _p in (_REPO / "src", _REPO / "tests"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from _dbusxml import canonicalize, signatures
from goblinmode.ipc import daemon_bridge as bridge

_FROZEN = _REPO / "docs" / "dbus-daemon-interface-v1.xml"

#: what v1 promises. Spelled out so that ADDING one fails here too - a new
#: method is an interface change even though it breaks no existing caller.
_V1_METHODS = 29
_V1_SIGNALS = 5
_V1_PROPERTIES = 3


def _frozen_body() -> str:
    return re.sub(r"^<!--.*?-->\n", "", _FROZEN.read_text(), flags=re.S)


class FrozenDaemonInterface(unittest.TestCase):
    def test_the_daemon_serves_exactly_the_frozen_interface(self):
        """The whole point. Canonical form, compared byte for byte."""
        self.assertEqual(
            canonicalize(bridge.INTROSPECTION_XML, bridge.IFACE),
            _frozen_body(),
            "the daemon no longer serves the frozen v1 interface.\n"
            "Revert the change, or cut a v2 and update every caller - do NOT "
            "regenerate docs/dbus-daemon-interface-v1.xml to make this pass.",
        )

    def test_the_frozen_file_is_itself_canonical(self):
        """So the file on disk is the exact byte target, not merely equivalent."""
        body = _frozen_body()
        self.assertEqual(canonicalize(body, bridge.IFACE), body)

    def test_the_bus_identity_is_part_of_the_contract(self):
        """A renamed bus name or path is a total break that would not show up
        in the method list at all - the client simply finds nothing."""
        self.assertEqual(bridge.IFACE, "com.goblinmode.Pro.Daemon")
        self.assertEqual(bridge.BUS_NAME, "com.goblinmode.Pro.Daemon")
        self.assertEqual(bridge.OBJECT_PATH, "/com/goblinmode/Pro/Daemon")

    def test_the_surface_is_the_expected_size(self):
        """Checked against what the daemon SERVES, not against the frozen file.

        Comparing the frozen file with a constant would be a tautology - both
        sides would move together. This repo has shipped that mistake once.
        """
        served = canonicalize(bridge.INTROSPECTION_XML, bridge.IFACE)
        self.assertEqual(served.count("<method "), _V1_METHODS)
        self.assertEqual(served.count("<signal "), _V1_SIGNALS)
        self.assertEqual(served.count("<property "), _V1_PROPERTIES)

    def test_json_payload_methods_all_declare_a_string(self):
        """Payloads are JSON strings on purpose: a shape change is then not an
        interface change. That only holds while they really are strings."""
        for method, (_ins, outs) in signatures(
            bridge.INTROSPECTION_XML, bridge.IFACE
        ).items():
            if method.startswith("Get") or method in {"RunPreflight", "AnalyzeLog"}:
                with self.subTest(method=method):
                    self.assertEqual(outs, "s", f"{method} stopped returning a string")


class DaemonIdentityProperties(unittest.TestCase):
    """The same three the helper serves, for the same reason."""

    def test_the_frozen_interface_declares_them(self):
        body = _frozen_body()
        for name in ("Version", "InterfaceVersion", "Implementation"):
            self.assertIn(f'<property name="{name}"', body)

    def test_the_daemon_serves_their_values(self):
        get = bridge.DaemonBridge.__dict__["_handle_get_property"]
        stub = object.__new__(bridge.DaemonBridge)
        from goblinmode.__about__ import __version__
        self.assertEqual(
            get(stub, None, None, None, bridge.IFACE, "Version", None).get_string(),
            __version__,
        )
        self.assertEqual(
            get(stub, None, None, None, bridge.IFACE,
                "InterfaceVersion", None).get_uint32(), 1)
        self.assertEqual(
            get(stub, None, None, None, bridge.IFACE,
                "Implementation", None).get_string(), "python")

    def test_an_unknown_property_is_not_invented(self):
        get = bridge.DaemonBridge.__dict__["_handle_get_property"]
        stub = object.__new__(bridge.DaemonBridge)
        self.assertIsNone(
            get(stub, None, None, None, bridge.IFACE, "Nonsense", None))

    def test_the_interface_version_never_moves(self):
        """A v2 would mean callers have to care which implementation answered,
        which is the property the freeze exists to deny."""
        self.assertEqual(bridge.INTERFACE_VERSION, 1)


if __name__ == "__main__":
    unittest.main()
