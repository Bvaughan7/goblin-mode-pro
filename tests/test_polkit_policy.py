"""Sanity check the polkit policy against the helper.

Would have caught SpinUpFans landing on the permissive action by default: the
policy file and the helper's action routing must agree on exactly which action
ids exist, and every mutating method must route to one that's actually declared.
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

import goblin_helper as gh  # noqa: E402

_POLICY = _REPO / "data" / "polkit" / "com.goblinmode.pro.policy"


def _policy_action_ids() -> set[str]:
    root = ET.parse(_POLICY).getroot()
    return {a.get("id") for a in root.iter("action")}


def _helper_action_ids() -> set[str]:
    return {gh.POLKIT_PERF, gh.POLKIT_KERNEL, gh.POLKIT_THERMAL}


class PolicyMatchesHelper(unittest.TestCase):
    def test_action_ids_match_exactly(self):
        self.assertEqual(
            _policy_action_ids(), _helper_action_ids(),
            "the polkit policy file and the helper's POLKIT_* constants "
            "declare different action ids",
        )

    def test_every_mutating_method_routes_to_a_declared_action(self):
        declared = _policy_action_ids()
        for method in gh._MUTATING:
            action = gh._polkit_action_for(method)
            self.assertIn(action, declared,
                          f"{method} routes to {action}, which isn't in the policy")

    def test_every_declared_action_carries_the_expected_defaults(self):
        root = ET.parse(_POLICY).getroot()
        for action in root.iter("action"):
            defaults = action.find("defaults")
            self.assertIsNotNone(defaults, f"{action.get('id')} has no <defaults>")
            self.assertEqual(defaults.findtext("allow_any"), "no")
            # the permissive runtime action is promptless while active; the two
            # persistent-effect actions must prompt
            active = defaults.findtext("allow_active")
            if action.get("id") == gh.POLKIT_PERF:
                self.assertEqual(active, "yes")
            else:
                self.assertEqual(active, "auth_admin_keep")


if __name__ == "__main__":
    unittest.main()
