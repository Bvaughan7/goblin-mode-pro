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
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_HELPER_DIR = _REPO / "helper"
if str(_HELPER_DIR) not in sys.path:
    sys.path.insert(0, str(_HELPER_DIR))

import goblin_helper as gh  # noqa: E402

_UNIT = _REPO / "data" / "systemd" / "goblin-mode-pro-helper.service"


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


if __name__ == "__main__":
    unittest.main()
