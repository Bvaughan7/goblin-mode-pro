"""Structural checks on the RPM specs, because rpmbuild is not on this machine.

Every other packaging target can be built where this is developed - makepkg on
Arch, and the .deb in CI. The rpm cannot, so it is the one target where a
mistake reaches a release build before anything notices. Two already have:

  * an arch-specific subpackage of a noarch package, which rpm refuses
    outright ("Only noarch subpackages are supported")
  * `%license LICENSE` left inside `%install` after a bad edit, where
    `%license` expands to the License: field and the shell tries to run `MIT`

Neither was visible to a test that checked for the presence of a string. These
check shape instead.
"""

from __future__ import annotations

import datetime
import re
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_SPECS = (_REPO / "packaging/rpm/goblin-mode-pro.spec",
          _REPO / "packaging/rpm/goblin-mode-pro-helper-rust.spec")

#: sections that must appear, in this order, in every spec
_ORDER = ("%prep", "%build", "%install", "%files", "%changelog")


def _sections(text: str) -> dict[str, str]:
    """Each top-level section's body, keyed by its directive."""
    found = [(m.group(0), m.start()) for m in re.finditer(r"^%\w+", text, re.M)
             if m.group(0) in _ORDER]
    out = {}
    for i, (name, start) in enumerate(found):
        end = found[i + 1][1] if i + 1 < len(found) else len(text)
        out[name] = text[start:end]
    return out


class SpecStructure(unittest.TestCase):
    def test_every_spec_has_the_required_sections_in_order(self):
        for spec in _SPECS:
            with self.subTest(spec=spec.name):
                positions = []
                for section in _ORDER:
                    idx = spec.read_text().find(f"\n{section}")
                    self.assertNotEqual(idx, -1, f"{spec.name} has no {section}")
                    positions.append(idx)
                self.assertEqual(positions, sorted(positions),
                                 f"{spec.name}'s sections are out of order")

    def test_file_directives_never_appear_in_install(self):
        """`%license` and `%doc` belong in %files.

        Left in %install they are not markers at all - rpm expands `%license`
        to the License: field and the shell tries to execute it. That is
        exactly how a release build died with `MIT: command not found`.
        """
        for spec in _SPECS:
            with self.subTest(spec=spec.name):
                install = _sections(spec.read_text()).get("%install", "")
                for directive in ("%license", "%doc"):
                    self.assertNotIn(directive, install,
                                     f"{spec.name}: {directive} is in %install, not %files")

    def test_the_noarch_spec_declares_no_subpackages(self):
        """rpm allows noarch-inside-arch, never arch-inside-noarch."""
        main = (_REPO / "packaging/rpm/goblin-mode-pro.spec").read_text()
        self.assertIn("BuildArch:      noarch", main)
        self.assertNotIn("%package", main)

    def test_changelog_weekdays_match_their_dates(self):
        """rpm reports every mismatch as a "bogus date" in the build output.

        Nine of them had accumulated, which is enough noise to hide a real
        error in the same block - and one did hide there.
        """
        for spec in _SPECS:
            text = spec.read_text()
            for m in re.finditer(r"^\* (\w{3}) (\w{3}) (\d{2}) (\d{4})", text, re.M):
                weekday, month, day, year = m.groups()
                actual = datetime.datetime.strptime(
                    f"{month} {day} {year}", "%b %d %Y").date().strftime("%a")
                with self.subTest(entry=m.group(0)):
                    self.assertEqual(weekday, actual,
                                     f"{spec.name}: {m.group(0)} is a {actual}")

    def test_every_tree_installed_into_is_also_packaged(self):
        """rpmbuild fails the build on files installed but not listed.

        H1 added the /usr/libexec symlink to %install and never to %files. That
        alone would have failed the rpm build; nothing noticed because an
        earlier error reached rpmbuild first.

        Deliberately COARSE: it compares the top-level tree written into
        (libexec, bin, share, lib), not whole paths. Matching paths properly
        would mean expanding rpm's macros - %install says /usr/libexec/%{name}
        where %files says %{_prefix}/libexec/%{name} - and reimplementing that
        to check a spec is a worse bet than a check that cannot be precise but
        also cannot be wrong. Forgetting an entire tree is the mistake that
        actually happens.
        """
        trees = {
            "_bindir": "bin", "_datadir": "share", "_unitdir": "lib",
            "_userunitdir": "lib", "libdir": "lib", "libexec": "libexec",
        }
        for spec in _SPECS:
            sections = _sections(spec.read_text())
            install = sections.get("%install", "")
            files = sections.get("%files", "")
            written = set()
            for dest in re.findall(r"%\{buildroot\}(\S+)", install):
                for macro, tree in trees.items():
                    if macro in dest or f"/{tree}/" in dest:
                        written.add(tree)
            self.assertTrue(written, f"{spec.name}: %install writes nothing?")
            for tree in sorted(written):
                covered = any(
                    macro in files or f"/{tree}" in files
                    for macro, t in trees.items() if t == tree
                ) or f"/{tree}" in files
                with self.subTest(spec=spec.name, tree=tree):
                    self.assertTrue(
                        covered,
                        f"{spec.name} installs into /{tree} but %files never mentions it",
                    )

    def test_the_two_specs_do_not_ship_the_same_paths(self):
        """Both installing a path would make the packages conflict on disk."""
        files = {}
        for spec in _SPECS:
            body = _sections(spec.read_text()).get("%files", "")
            files[spec.name] = {
                line.strip() for line in body.splitlines()
                if line.startswith("%{") or line.startswith("/")
            }
        main, rust = (files[s.name] for s in _SPECS)
        self.assertFalse(main & rust, f"both specs ship {main & rust}")


if __name__ == "__main__":
    unittest.main()
