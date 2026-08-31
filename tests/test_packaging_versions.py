"""Every packaging target's version matches src/goblinmode/__about__.py.

The release process bumps five files by hand (__about__, the two PKGBUILDs and
the .SRCINFO, the RPM spec, the Debian changelog). Missing one is silent: the
build still succeeds and ships a package labelled with the wrong version. The
AUR placeholder had drifted a full release behind before this test existed.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from tests._support import _SRC  # noqa: F401

from goblinmode.__about__ import __version__

_REPO = Path(__file__).resolve().parent.parent
_PKG = _REPO / "packaging"


class PackagingVersions(unittest.TestCase):
    def test_arch_pkgbuild(self):
        m = re.search(r"^pkgver=(\S+)$", (_PKG / "arch/PKGBUILD").read_text(), re.M)
        self.assertIsNotNone(m, "no pkgver= in the Arch PKGBUILD")
        self.assertEqual(m.group(1), __version__)

    def test_rpm_spec(self):
        m = re.search(r"^Version:\s+(\S+)$",
                      (_PKG / "rpm/goblin-mode-pro.spec").read_text(), re.M)
        self.assertIsNotNone(m, "no Version: in the RPM spec")
        self.assertEqual(m.group(1), __version__)

    def test_debian_changelog(self):
        first = (_PKG / "debian/changelog").read_text().splitlines()[0]
        m = re.match(r"\S+ \((\d+\.\d+\.\d+)-\d+\)", first)
        self.assertIsNotNone(m, f"unparseable changelog entry: {first!r}")
        self.assertEqual(m.group(1), __version__)

    def test_aur_pkgbuild_placeholder(self):
        """The -git package resolves pkgver() at build time, so this value is
        only a placeholder - but it is what the AUR page shows until someone
        builds it, and it should not advertise a release we are past."""
        m = re.search(r"^pkgver=(\d+\.\d+\.\d+)\.r",
                      (_PKG / "aur/PKGBUILD").read_text(), re.M)
        self.assertIsNotNone(m, "no versioned pkgver= placeholder in the AUR PKGBUILD")
        self.assertEqual(m.group(1), __version__)

    def test_aur_srcinfo_agrees_with_its_pkgbuild(self):
        """Only the base version has to match.

        `makepkg --printsrcinfo` records the pkgver() *result* at the time it
        was run, so the `.rN.gSHA` suffix is a snapshot of that build and will
        legitimately differ from the PKGBUILD's placeholder. The base version
        in front of it is the part that must not drift.
        """
        srcinfo = re.search(r"^\s*pkgver = (\d+\.\d+\.\d+)",
                            (_PKG / "aur/.SRCINFO").read_text(), re.M)
        self.assertIsNotNone(srcinfo, "no pkgver in .SRCINFO")
        self.assertEqual(srcinfo.group(1), __version__,
                         ".SRCINFO is regenerated from the PKGBUILD "
                         "(makepkg --printsrcinfo) and has gone stale")

    def test_aur_optdepends_match_the_arch_pkgbuild(self):
        """The two PKGBUILDs describe the same package and drift silently."""
        def opts(path):
            body = (_PKG / path).read_text()
            return {line.split(":")[0].strip().strip("'\"")
                    for line in body.splitlines() if ": " in line and "'" in line
                    and not line.strip().startswith("#")}
        missing = opts("arch/PKGBUILD") - opts("aur/PKGBUILD")
        self.assertEqual(missing, set(),
                         f"in the Arch PKGBUILD but not the AUR one: {missing}")


if __name__ == "__main__":
    unittest.main()
