"""The conformance suite's own guard rails.

The suite is the thing that grades both helper implementations, so the parts
of it that tell a human what to do next are worth testing: a stale instruction
is worse than none, because it looks authoritative.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

_REPO = Path(__file__).resolve().parent.parent
for _p in (_REPO / "tests" / "conformance", _REPO / "helper"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import helper as suite


def _commands_in(text: str) -> list[list[str]]:
    """Every runnable command the message suggests, as argv."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if "helper.py" not in line:
            continue
        parts = line.split()
        if parts and parts[0] == "sudo":
            parts = parts[1:]
        # drop the interpreter and the script itself; what is left is argv
        out.append(parts[2:])
    return out


class ComplementaryRun(unittest.TestCase):
    """Root and unprivileged runs grade disjoint sets of checks.

    Neither alone is complete, and each one LOOKS complete, which is exactly
    why the suite has to say so. The root run cannot see the ownership gate at
    all, because renice() skips it for uid 0 - so the run with the most
    privilege is the one that cannot check who you are.
    """

    def test_each_run_points_at_the_other_one(self):
        with mock.patch("os.geteuid", return_value=0):
            as_root = suite.complementary_run()
        with mock.patch("os.geteuid", return_value=1000):
            as_user = suite.complementary_run()

        self.assertNotEqual(as_root, as_user)
        # The root run must send you to an unprivileged one, and vice versa.
        self.assertNotIn("sudo", as_root)
        self.assertIn("sudo", as_user)

    def test_the_root_message_names_the_gate_it_cannot_grade(self):
        with mock.patch("os.geteuid", return_value=0):
            message = suite.complementary_run()
        self.assertIn("ownership", message)
        self.assertIn("uid 0", message)

    def test_every_suggested_command_is_actually_runnable(self):
        """A renamed flag must break this, not silently send someone nowhere.

        The suggestions are free text, so nothing else would notice if
        `--polkit-routing` were renamed and the hint kept naming the old one.
        """
        parser = suite.build_parser()
        for euid in (0, 1000):
            with mock.patch("os.geteuid", return_value=euid):
                message = suite.complementary_run()
            commands = _commands_in(message)
            self.assertTrue(commands, f"euid {euid} suggests no command: {message}")
            for argv in commands:
                # Raises SystemExit on an unknown flag.
                parser.parse_args(argv)


if __name__ == "__main__":
    unittest.main()
