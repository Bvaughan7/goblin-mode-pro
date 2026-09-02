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

import os
import re
import subprocess
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


def _rust_helper() -> Path | None:
    """The built Rust helper, or None if it has not been built.

    `GMP_HELPER_RS` overrides the search, so an installed binary or a
    cross-built one can be graded without a working tree layout.
    """
    override = os.environ.get("GMP_HELPER_RS")
    if override:
        return Path(override) if Path(override).exists() else None
    for profile in ("debug", "release"):
        candidate = _REPO / "target" / profile / "gmp-helper"
        if candidate.exists():
            return candidate
    return None


class RustImplementation(unittest.TestCase):
    """The Rust helper serves the same frozen interface as the Python one.

    This is the check the freeze exists for. Both binaries are asked what they
    serve and both answers go through the SAME canonicalizer in
    `tests/_dbusxml.py` - deliberately, because a canonicalizer reimplemented
    in Rust would be a second source of truth, and two of those drifting apart
    is how a freeze check starts passing for the wrong reason.

    The Rust binary is asked via `--introspect`, which prints its interface and
    exits without touching the bus, so this runs in CI and on a machine where
    the real helper already holds the bus name.
    """

    def setUp(self):
        self.binary = _rust_helper()
        if self.binary is None:
            # A skip that can never fail is a check that quietly stops
            # existing. CI sets this so the freeze is actually enforced there;
            # locally, `cargo build` is not required to run the Python suite.
            if os.environ.get("GMP_REQUIRE_RUST_HELPER") == "1":
                self.fail(
                    "GMP_REQUIRE_RUST_HELPER=1 but no Rust helper was found - "
                    "run `cargo build` before the Python suite"
                )
            self.skipTest("the Rust helper is not built; run `cargo build`")

    def _served(self) -> str:
        proc = subprocess.run(
            [str(self.binary), "--introspect"],
            capture_output=True, text=True, timeout=30, check=False,
        )
        self.assertEqual(
            proc.returncode, 0,
            f"{self.binary} --introspect failed: {proc.stderr.strip()}",
        )
        return proc.stdout

    def test_rust_helper_serves_exactly_the_frozen_interface(self):
        self.assertEqual(
            canonicalize(self._served(), gh.IFACE),
            _frozen_body(),
            "the Rust helper does not serve the frozen v1 interface.\n"
            "Fix the Rust side - do NOT regenerate docs/dbus-interface-v1.xml, "
            "that is what breaks mixed Python/Rust installs.",
        )

    def test_both_implementations_promise_identical_signatures(self):
        """Compared as a mapping, so a mismatch names the method.

        The byte comparison above already covers this, but it fails with a
        wall of XML. This one says `SetTDP` and the two signatures, which is
        the difference between a five-second fix and a bisect.
        """
        self.assertEqual(
            signatures(self._served(), gh.IFACE),
            signatures(gh.INTROSPECTION_XML, gh.IFACE),
        )


class DropInParity(unittest.TestCase):
    """Both helpers answer the same way to the way the UNIT invokes them.

    Swapping which implementation the unit runs is only safe if the command
    line is the same shape too. `ExecStopPost=... --revert` is the one that
    matters: it is what puts the machine back when the service stops, so a
    Rust helper that ignored the flag would silently stop reverting on
    shutdown - a break nobody notices until a governor survives a reboot.
    """

    def setUp(self):
        self.binary = _rust_helper()
        if self.binary is None:
            if os.environ.get("GMP_REQUIRE_RUST_HELPER") == "1":
                self.fail("GMP_REQUIRE_RUST_HELPER=1 but no Rust helper was found")
            self.skipTest("the Rust helper is not built; run `cargo build`")

    def _run(self, command: list[str]) -> tuple[int, str]:
        proc = subprocess.run(
            command, capture_output=True, text=True, timeout=30, check=False
        )
        return proc.returncode, proc.stderr.strip()

    def test_the_unit_only_uses_flags_both_helpers_implement(self):
        """Driven off the unit file, so adding a flag there fails here."""
        unit = (_REPO / "data" / "systemd" / "goblin-mode-pro-helper.service").read_text()
        flags = set()
        for line in unit.splitlines():
            if line.startswith("Exec"):
                flags.update(re.findall(r"(--[a-z][a-z-]*)", line))
        self.assertEqual(flags, {"--revert"}, "the unit's flags changed; port the new one")

    def test_both_refuse_revert_identically_without_root(self):
        """Same message, same exit status, from both implementations."""
        python = self._run(
            [sys.executable, str(_REPO / "helper" / "goblin_helper.py"), "--revert"]
        )
        rust = self._run([str(self.binary), "--revert"])
        self.assertEqual(python, rust)
        self.assertEqual(rust, (1, "goblin-helper must run as root"))

    def test_the_rust_helper_actually_recognises_revert(self):
        """The check above is not enough on its own, and that is the point.

        The root gate runs BEFORE the flag is handled, so unprivileged
        `--revert` answers "must run as root" whether or not the flag is
        implemented at all - deleting the whole branch keeps that test green.
        What proves the flag exists is that it is treated DIFFERENTLY from one
        that does not: a known flag reaches the root gate (exit 1), an unknown
        one is refused as a usage error first (exit 2).
        """
        known = self._run([str(self.binary), "--revert"])
        unknown = self._run([str(self.binary), "--definitely-not-a-flag"])
        self.assertEqual(known[0], 1, f"--revert should reach the root gate: {known}")
        self.assertEqual(unknown[0], 2, f"an unknown flag should be usage: {unknown}")
        self.assertIn("unknown argument", unknown[1])

    def test_introspect_is_the_only_mode_that_works_without_root(self):
        """It has to: CI runs it, and it reads nothing privileged."""
        code, _ = self._run([str(self.binary), "--introspect"])
        self.assertEqual(code, 0)

