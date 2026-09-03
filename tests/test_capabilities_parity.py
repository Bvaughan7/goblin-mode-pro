"""The Rust and Python capability text agree.

Every string this module produces is something a person is invited to paste
into a root shell, so the comparison is on the exact text. A command that is
subtly wrong for their distro is worse than no command at all, which is why an
unknown distro or package manager returns nothing rather than guessing.
"""

from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path

from tests._support import _SRC  # noqa: F401

from goblinmode import capabilities

_REPO = Path(__file__).resolve().parent.parent


def _binary() -> Path | None:
    override = os.environ.get("GMP_CAPABILITIES_BIN")
    if override:
        return Path(override) if Path(override).exists() else None
    for profile in ("debug", "release"):
        candidate = _REPO / "target" / profile / "examples" / "capabilities"
        if candidate.exists():
            return candidate
    return None


DEVICES = (
    'I: Bus=0003 Vendor=045e\n'
    'N: Name="Microsoft X-Box 360 pad"\n'
    'H: Handlers=js0 event3\n'
    '\n'
    'I: Bus=0003 Vendor=1532\n'
    'N: Name="Razer Razer DeathAdder"\n'
    'H: Handlers=mouse0 event4\n'
    '\n'
    'I: Bus=0005 Vendor=054c\n'
    'N: Name="Wireless Controller"\n'
    'H: Handlers=event5\n'
    '\n'
    'I: Bus=0003\n'
    'N: Name="Microsoft X-Box 360 pad"\n'
    'H: Handlers=js1 event6\n'
    '\n'
    'I: Bus=0003\n'
    'H: Handlers=js2 event7\n'
    '\n'
    'I: Bus=0003\n'
    'N: Name="AT Translated Set 2 keyboard"\n'
    'H: Handlers=kbd event0\n'
)


class BothImplementationsAgree(unittest.TestCase):
    def setUp(self):
        self.binary = _binary()
        if self.binary is None:
            if os.environ.get("GMP_REQUIRE_RUST_HELPER") == "1":
                self.fail("GMP_REQUIRE_RUST_HELPER=1 but the capabilities example is "
                          "not built - run `cargo build -p gmp-core --example capabilities`")
            self.skipTest("build it with `cargo build -p gmp-core --example capabilities`")

    def _rust(self, p: dict) -> dict:
        r = subprocess.run([str(self.binary)], input=json.dumps(p),
                           capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def _python(self, p: dict) -> dict:
        return {
            "cpu_list": capabilities._parse_cpu_list(p.get("cpu_list", "")),
            "install_command": capabilities.install_command(
                p.get("package_manager") or "", *p.get("pkgs", [])),
            "kernel_tip": list(capabilities.kernel_upgrade_tip(p.get("distro", ""))),
            "controllers": _controllers(p.get("devices", "")),
        }

    def _same(self, p: dict) -> dict:
        py, rs = self._python(p), self._rust(p)
        self.assertEqual(py, rs)
        return py

    def test_every_package_manager_and_package(self):
        """These are the actual commands users run."""
        for pm in [*capabilities._INSTALL_CMD, "", "brew", None]:
            for pkgs in ([], ["mangohud"], ["gamemode"], ["mangohud", "gamemode"],
                         ["something-unlisted"]):
                with self.subTest(pm=pm, pkgs=pkgs):
                    self._same({"package_manager": pm, "pkgs": pkgs})

    def test_every_distro_in_the_kernel_table_and_some_that_are_not(self):
        for distro in [*capabilities._KERNEL_TIPS, "", "nixos", "slackware"]:
            with self.subTest(distro=distro):
                out = self._same({"distro": distro})
                if distro == "cachyos":
                    self.assertEqual(out["kernel_tip"], ["", ""],
                                     "CachyOS ships a tuned kernel; say nothing")

    def test_cpu_lists_including_malformed_ones(self):
        for spec in ("0-3,8,10-11", "0", "", "  ", "0-0", "3-0", "0-3,,5",
                     "a-b", "x", "0-3,x,5", "10-11,0-3", "0-3,2-5",
                     "0,0,0", " 0 - 3 ", "1-", "-1", "0-2,4"):
            with self.subTest(spec=spec):
                self._same({"cpu_list": spec})

    def test_controller_detection_from_a_captured_blob(self):
        out = self._same({"devices": DEVICES})
        self.assertEqual(out["controllers"],
                         ["Microsoft X-Box 360 pad", "Wireless Controller"])

    def test_a_device_blob_with_nothing_in_it(self):
        for blob in ("", "\n\n", "N: Name=\"no handlers\"\n"):
            with self.subTest(blob=blob):
                self._same({"devices": blob})


def _controllers(blob: str) -> list[str]:
    """The Python side reads a file; feed it the same blob."""
    import re
    out: list[str] = []
    for block in blob.split("\n\n"):
        m = re.search(r'N: Name="([^"]+)"', block)
        if not m:
            continue
        name = m.group(1)
        handlers = re.search(r"H: Handlers=([^\n]+)", block)
        is_js = bool(handlers and re.search(r"\bjs\d", handlers.group(1)))
        if (is_js or capabilities._PAD_RE.search(name)) and name not in out:
            out.append(name)
    return out


if __name__ == "__main__":
    unittest.main()
