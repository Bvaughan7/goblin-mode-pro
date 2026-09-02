"""The helper still serves the frozen v1 interface, byte for byte.

This is the test that protects the whole Python-to-Rust conversion. Both
implementations of the helper are compared against `docs/dbus-interface-v1.xml`,
so a caller - the daemon, the CLI, `selftest` - can talk to either one without
knowing or caring which language answered.

If this test fails, the interface changed. The fix is to revert the change or
to cut a v2 and update every implementation and every caller. The fix is *not*
to regenerate the frozen file: that turns a loud failure here into a silent
one on somebody's machine, where a mixed install meets a method that no longer
exists or whose reply type moved.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
for _p in (_REPO / "helper", _REPO / "tests"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import goblin_helper as gh
from _dbusxml import InterfaceNotFound, canonicalize, signatures

_FROZEN = _REPO / "docs" / "dbus-interface-v1.xml"

#: how many methods v1 promises. Spelled out so that *adding* a method fails
#: here too - a new method is an interface change even though it breaks no
#: existing caller, and the Rust port has to grow it at the same time.
_V1_METHOD_COUNT = 19


def _frozen_text() -> str:
    return _FROZEN.read_text()


def _frozen_body() -> str:
    """The frozen file with its explanatory comment stripped."""
    return re.sub(r"^<!--.*?-->\n", "", _frozen_text(), flags=re.S)


class FrozenInterface(unittest.TestCase):
    def test_helper_serves_exactly_the_frozen_interface(self):
        """The whole point. Canonical form, compared byte for byte."""
        served = canonicalize(gh.INTROSPECTION_XML, gh.IFACE)
        self.assertEqual(
            served,
            _frozen_body(),
            "the helper no longer serves the frozen v1 interface.\n"
            "Revert the interface change, or cut a v2 and update every "
            "implementation - do NOT regenerate docs/dbus-interface-v1.xml to "
            "make this pass, that is what breaks mixed Python/Rust installs.",
        )

    def test_frozen_file_is_itself_canonical(self):
        """So the file on disk is the exact byte target, not merely equivalent.

        Without this, a hand-edit that reordered methods or changed the indent
        would still pass the comparison above (both sides get canonicalized),
        and the file would drift away from what a Rust implementation is
        diffed against.
        """
        body = _frozen_body()
        self.assertEqual(
            canonicalize(body, gh.IFACE), body,
            "docs/dbus-interface-v1.xml is not in canonical form; regenerate "
            "its body with tests/_dbusxml.canonicalize()",
        )

    def test_bus_identity_is_documented_and_unchanged(self):
        """The names a client connects to are as much the contract as the XML.

        A renamed bus name or object path is a total break - the client finds
        nothing on the bus - and it would not show up in the method list at
        all, so it is asserted separately.
        """
        self.assertEqual(gh.BUS_NAME, "com.goblinmode.ProHelper")
        self.assertEqual(gh.OBJECT_PATH, "/com/goblinmode/ProHelper")
        self.assertEqual(gh.IFACE, "com.goblinmode.ProHelper.Manager")
        header = _frozen_text()
        for value in (gh.BUS_NAME, gh.OBJECT_PATH, gh.IFACE):
            self.assertIn(
                value, header,
                "docs/dbus-interface-v1.xml documents a different bus identity "
                "than the helper uses",
            )

    def test_v1_has_the_expected_method_count(self):
        """Checked against what the helper *serves*, not just what the frozen
        file contains - the latter is a constant compared with a constant and
        would stay green while a method was being added to the helper."""
        self.assertEqual(len(signatures(_frozen_body(), gh.IFACE)), _V1_METHOD_COUNT)
        self.assertEqual(
            len(signatures(gh.INTROSPECTION_XML, gh.IFACE)), _V1_METHOD_COUNT,
            "the helper serves a different number of methods than v1 declares",
        )

    def test_every_frozen_method_is_dispatched(self):
        """A method in the XML with no branch in `_handle_call` never replies.

        Nothing raises and nothing is logged - the caller just blocks until
        the D-Bus timeout, around 25 seconds later. The daemon's bridge grew
        the same class of bug, which is why it is checked on both sides.
        """
        source = (_REPO / "helper" / "goblin_helper.py").read_text()
        dispatch = source.split("def _handle_call(", 1)[1].split("\ndef ", 1)[0]
        for name in signatures(_frozen_body(), gh.IFACE):
            with self.subTest(method=name):
                self.assertIn(
                    f'"{name}"', dispatch,
                    f"{name} is in the frozen interface but has no branch in "
                    "_handle_call - a caller would hang until the bus timeout",
                )

    def test_mutating_methods_are_a_subset_of_the_interface(self):
        """`_MUTATING` naming a method that does not exist would be dead code
        gating nothing, and is a sign the two lists drifted apart."""
        declared = set(signatures(_frozen_body(), gh.IFACE))
        self.assertLessEqual(gh._MUTATING, declared)
        self.assertLessEqual(gh._KERNEL_ACTION_METHODS, gh._MUTATING)
        self.assertLessEqual(gh._THERMAL_ACTION_METHODS, gh._MUTATING)

    def test_read_only_methods_need_no_authorization(self):
        """The complement of `_MUTATING` is the read-only surface. Pinned so
        that moving a method between the two - in either direction - is a
        deliberate, visible change rather than a side effect."""
        declared = set(signatures(_frozen_body(), gh.IFACE))
        self.assertEqual(
            declared - gh._MUTATING,
            {"GetGovernor", "GetPowerLimits", "HasTDPControl", "ReadUndervolt"},
        )


class Canonicalizer(unittest.TestCase):
    """The canonicalizer is the thing every other assertion here trusts."""

    _XML = """
    <node>
      <interface name="x.Y">
        <method name="B"><arg type="s" name="a" direction="in"/></method>
        <method name="A"><arg type="b" name="ok" direction="out"/></method>
      </interface>
    </node>
    """

    def test_sorts_methods_but_keeps_argument_order(self):
        out = canonicalize(
            '<node><interface name="x.Y"><method name="M">'
            '<arg type="s" name="first" direction="in"/>'
            '<arg type="u" name="second" direction="in"/>'
            "</method></interface></node>",
            "x.Y",
        )
        self.assertLess(out.index("first"), out.index("second"))
        self.assertLess(canonicalize(self._XML, "x.Y").index('"A"'),
                        canonicalize(self._XML, "x.Y").index('"B"'))

    def test_is_idempotent(self):
        once = canonicalize(self._XML, "x.Y")
        self.assertEqual(canonicalize(once, "x.Y"), once)

    def test_ignores_the_standard_bus_interfaces(self):
        """A live bus appends Peer/Introspectable/Properties. Those are not
        part of anybody's contract and must not appear in the comparison."""
        with_standard = (
            '<node><interface name="org.freedesktop.DBus.Peer">'
            '<method name="Ping"/></interface>'
            '<interface name="x.Y"><method name="A">'
            '<arg type="b" name="ok" direction="out"/></method></interface></node>'
        )
        self.assertNotIn("Ping", canonicalize(with_standard, "x.Y"))

    def test_defaults_a_missing_direction_to_in(self):
        """Direction is optional in the D-Bus spec. Two implementations that
        disagree only about spelling it out still describe the same method."""
        explicit = canonicalize(
            '<node><interface name="x.Y"><method name="A">'
            '<arg type="s" name="k" direction="in"/></method></interface></node>',
            "x.Y")
        implicit = canonicalize(
            '<node><interface name="x.Y"><method name="A">'
            '<arg type="s" name="k"/></method></interface></node>',
            "x.Y")
        self.assertEqual(explicit, implicit)

    def test_missing_interface_names_what_it_did_find(self):
        with self.assertRaises(InterfaceNotFound) as caught:
            canonicalize(self._XML, "not.There")
        self.assertIn("x.Y", str(caught.exception))

    def test_signatures_split_in_from_out(self):
        sigs = signatures(gh.INTROSPECTION_XML, gh.IFACE)
        self.assertEqual(sigs["SetSysctl"], ("ss", "b"))
        self.assertEqual(sigs["GetPowerLimits"], ("", "tt"))
        self.assertEqual(sigs["Renice"], ("ui", "b"))


if __name__ == "__main__":
    unittest.main()
