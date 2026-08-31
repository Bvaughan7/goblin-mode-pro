"""Coverage test: every path the privileged helper writes to is granted by a
``ReadWritePaths=`` entry in ``goblin-mode-pro-helper.service``.

This is the real deliverable of the 1.2.x sandbox-mismatch fix. Three features
(``SetNvidiaModeset``, the ``user.max_user_namespaces`` fix, fan control) shipped
writing to paths the hardened unit made read-only. A green unit test on the
Python side said nothing because the sandbox only exists at runtime. This test
parses the unit file directly and fails the build the moment the allowlist and
the code drift apart again.
"""

from __future__ import annotations

import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_HELPER_DIR = _REPO / "helper"
if str(_HELPER_DIR) not in sys.path:
    sys.path.insert(0, str(_HELPER_DIR))

import goblin_helper as gh

_UNIT = _REPO / "data" / "systemd" / "goblin-mode-pro-helper.service"


def _unit_value(key: str) -> str | None:
    """Last assignment of `key` in the unit (systemd semantics), unquoted."""
    found = None
    for raw in _UNIT.read_text().splitlines():
        line = raw.strip()
        if line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        if name.strip() == key:
            found = value.strip()
    return found


def _read_write_paths() -> list[str]:
    paths: list[str] = []
    for raw in _UNIT.read_text().splitlines():
        line = raw.strip()
        if line.startswith("#") or line.startswith(";") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() != "ReadWritePaths":
            continue
        # systemd has no inline comments; the whole RHS is the value. Entries
        # are whitespace-separated; a leading '-' means "ok if absent".
        for token in value.split():
            paths.append(token.lstrip("-"))
    return paths


def _covered(path: str, roots: list[str]) -> bool:
    p = Path(path)
    return any(p == Path(r) or Path(r) in p.parents for r in roots)


class HelperSandboxCoverage(unittest.TestCase):
    def setUp(self):
        self.rw = _read_write_paths()
        self.assertTrue(self.rw, "no ReadWritePaths= entries parsed from the unit")

    def test_every_sysctl_in_the_allowlist_is_writable(self):
        for key in gh.SYSCTL_ALLOW:
            procfs = "/proc/sys/" + key.replace(".", "/")
            self.assertTrue(
                _covered(procfs, self.rw),
                f"{key} ({procfs}) is in SYSCTL_ALLOW but no ReadWritePaths= "
                f"entry covers it",
            )

    def test_every_sysfs_write_root_is_writable(self):
        for root in gh.SYSFS_WRITE_ROOTS:
            self.assertTrue(
                _covered(root, self.rw),
                f"{root} is in SYSFS_WRITE_ROOTS but no ReadWritePaths= entry "
                f"covers it",
            )

    def test_nvidia_modeset_conf_dir_is_writable(self):
        self.assertTrue(
            _covered(str(gh.NVIDIA_MODESET_CONF.parent), self.rw),
            f"{gh.NVIDIA_MODESET_CONF} cannot be written under the current unit",
        )

    def test_fan_pwm_base_is_writable(self):
        self.assertTrue(_covered(str(gh._HWMON_BASE), self.rw))


class CapabilityCoverage(unittest.TestCase):
    """Every capability the helper needs is in CapabilityBoundingSet=.

    The sibling of the ReadWritePaths test above, and it exists for the same
    reason - except this one cost a shipped feature. `user.max_user_namespaces`
    is one of the pre-flight fixes, and writing it had never once worked on any
    machine: the unit granted /proc/sys/user under ReadWritePaths=, but the
    kernel gates /proc/sys/user/* writes on CAP_SYS_RESOURCE, which the
    bounding set dropped. The failure is EACCES, which reads like a permission
    bug rather than a sandbox one, and no test could see it because the
    capability set only exists at runtime. Found by `selftest --apply`.
    """

    def test_bounding_set_matches_the_declared_requirements(self):
        declared = _unit_value("CapabilityBoundingSet")
        self.assertIsNotNone(declared, "the unit sets no CapabilityBoundingSet=")
        self.assertEqual(
            set(declared.split()), set(gh.HELPER_CAPABILITIES),
            "CapabilityBoundingSet= in the unit and HELPER_CAPABILITIES in "
            "goblin_helper.py have drifted apart - every capability the helper "
            "needs must be in both, with a comment saying what needs it",
        )

    def test_every_capability_says_what_needs_it(self):
        for cap, why in gh.HELPER_CAPABILITIES.items():
            self.assertTrue(why.strip(), f"{cap} has no stated reason")
            self.assertTrue(cap.startswith("CAP_"), f"{cap} is not a capability name")

    def test_no_ambient_capabilities(self):
        """Ambient caps would be inherited by anything the helper spawns."""
        self.assertEqual((_unit_value("AmbientCapabilities") or "").strip(), "")

    def test_sysctl_allow_paths_are_covered_by_a_capability_or_plain_root(self):
        """Any /proc/sys/user key needs CAP_SYS_RESOURCE; flag a new one."""
        userns_keys = [k for k in gh.SYSCTL_ALLOW if k.startswith("user.")]
        if userns_keys:
            self.assertIn(
                "CAP_SYS_RESOURCE", gh.HELPER_CAPABILITIES,
                f"{userns_keys} live under /proc/sys/user, whose writes the "
                "kernel gates on CAP_SYS_RESOURCE",
            )


class InterfaceSurface(unittest.TestCase):
    """The D-Bus interface XML is the helper's public privileged surface.

    It is an f-string in the middle of the module, so it is one careless edit
    away from swallowing code or losing a method, and neither failure shows up
    until the helper tries to acquire the bus on a real system - as root, at
    boot. Parsing it here turns that into a build failure.
    """

    def _iface(self):
        node = ET.fromstring(gh.INTROSPECTION_XML)
        ifaces = node.findall("interface")
        self.assertEqual(len(ifaces), 1, "expected exactly one interface")
        return ifaces[0]

    def test_the_xml_is_well_formed_and_names_the_right_interface(self):
        self.assertEqual(self._iface().get("name"), gh.IFACE)

    def test_every_mutating_method_is_declared(self):
        declared = {m.get("name") for m in self._iface().findall("method")}
        missing = sorted(set(gh._MUTATING) - declared)
        self.assertEqual(missing, [], f"in _MUTATING but not in the interface: {missing}")

    def test_every_method_routes_to_a_real_polkit_action(self):
        actions = {gh.POLKIT_PERF, gh.POLKIT_KERNEL, gh.POLKIT_THERMAL}
        for method in self._iface().findall("method"):
            self.assertIn(gh._polkit_action_for(method.get("name")), actions)

    def test_the_action_split_is_what_we_think_it_is(self):
        """Guards the routing itself, not just that it returns something."""
        self.assertEqual(gh._polkit_action_for("SetGovernor"), gh.POLKIT_PERF)
        self.assertEqual(gh._polkit_action_for("SetSysctl"), gh.POLKIT_KERNEL)
        self.assertEqual(gh._polkit_action_for("SetNvidiaModeset"), gh.POLKIT_KERNEL)
        self.assertEqual(gh._polkit_action_for("SpinUpFans"), gh.POLKIT_THERMAL)
        # handing fan control back to the EC must never need a prompt
        self.assertEqual(gh._polkit_action_for("ResetFans"), gh.POLKIT_PERF)


if __name__ == "__main__":
    unittest.main()
