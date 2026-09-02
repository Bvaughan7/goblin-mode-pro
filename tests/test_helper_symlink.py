"""The unit runs a symlink, and everything that installs must create it.

The helper is being ported to Rust, and the two implementations meet only over
D-Bus. Which one runs is therefore a property of the machine, not of the unit:
`/usr/libexec/goblin-mode-pro/helper` is a symlink, switching is a relink and a
restart, and rolling back is relinking the other way.

One unit, deliberately - two units with `Conflicts=` would allow a machine to
end up with both enabled and racing for the same bus name.

The failure this guards is specific and quiet: the unit references a path that
some install path does not create, so the service fails to start with
"No such file or directory" on a machine that installed from a package rather
than from install.sh. Nothing else would notice, because nothing else reads
both files.
"""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_UNIT = _REPO / "data" / "systemd" / "goblin-mode-pro-helper.service"

#: every path that installs the helper, and the text each must contain
_INSTALLERS = (
    "install.sh",
    "packaging/arch/PKGBUILD",
    "packaging/aur/PKGBUILD",
    "packaging/rpm/goblin-mode-pro.spec",
    "packaging/debian/rules",
)


def _exec_lines() -> list[str]:
    return [
        line for line in _UNIT.read_text().splitlines()
        if re.match(r"^Exec\w+=", line)
    ]


def _helper_path() -> str:
    """The path the unit actually runs, read out of the unit itself."""
    start = [line for line in _exec_lines() if line.startswith("ExecStart=")]
    assert len(start) == 1, f"expected one ExecStart, got {start}"
    return start[0].split("=", 1)[1].split()[0]


class HelperIndirection(unittest.TestCase):
    def test_the_unit_names_no_implementation(self):
        """The point of the indirection.

        If the unit named `goblin_helper.py` or a Rust binary directly, then
        switching implementations would mean editing and reinstalling the unit
        - and rolling back would mean doing it again under whatever pressure
        made the rollback necessary.
        """
        for line in _exec_lines():
            self.assertNotIn("goblin_helper.py", line, f"unit names an implementation: {line}")
            self.assertNotIn("python3", line, f"unit names an interpreter: {line}")

    def test_the_unit_runs_the_libexec_symlink(self):
        self.assertEqual(_helper_path(), "/usr/libexec/goblin-mode-pro/helper")

    def test_every_exec_line_uses_the_same_path(self):
        """ExecStopPost must revert through the SAME implementation that ran.

        Reverting through the other one would work today, because both read
        the same state file - but it would stop being obvious that it has to.
        """
        helper = _helper_path()
        for line in _exec_lines():
            self.assertIn(helper, line, f"{line} does not run {helper}")

    def test_every_install_path_creates_what_the_unit_runs(self):
        """Driven off the unit, so changing the path there fails here.

        A package that installs the unit without the symlink produces a
        service that will not start, and only on machines that used that
        package - which is the hardest kind of break to reproduce.
        """
        # The package files build the name from their own variables
        # ($pkgname, %{name}, ...), so match on the fixed part of the path and
        # on the link itself rather than trying to expand each dialect.
        libexec = str(Path(_helper_path()).parent.parent)  # /usr/libexec
        leaf = Path(_helper_path()).name                   # helper
        for installer in _INSTALLERS:
            text = (_REPO / installer).read_text()
            with self.subTest(installer=installer):
                self.assertIn(libexec, text, f"{installer} never mentions {libexec}")
                self.assertRegex(
                    text, rf"ln -sfn .*/{leaf}\b",
                    f"{installer} does not create the {leaf} symlink",
                )

    def test_the_symlink_is_relinkable_not_a_copy(self):
        """`ln -sfn`, not `ln -s`.

        Without -f a second install fails because the link exists; without -n
        `ln` follows an existing link to a directory and creates the new one
        INSIDE it. Both turn a re-install into a confusing mess.
        """
        for installer in _INSTALLERS:
            text = (_REPO / installer).read_text()
            with self.subTest(installer=installer):
                self.assertNotIn("ln -s ", text, f"{installer} uses a non-forcing ln")


class InstallerHelperSelection(unittest.TestCase):
    """`install.sh --helper=` picks which implementation the symlink points at."""

    _INSTALL = _REPO / "install.sh"

    def test_an_unknown_implementation_is_refused_before_anything_happens(self):
        """Runnable without sudo: the validation is argument parsing, and it
        has to run before any install step, or a typo gets halfway through."""
        proc = subprocess.run(
            [str(self._INSTALL), "--helper=perl"],
            capture_output=True, text=True, timeout=60, check=False,
        )
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("want python or rust", proc.stderr)

    def test_python_is_installed_whichever_implementation_is_chosen(self):
        """THE ROLLBACK GUARANTEE.

        Going back to Python must never need a toolchain or a rebuild - it is
        the thing you reach for when the Rust helper is misbehaving, quite
        possibly on a machine that has no cargo at all. So the Python helper is
        installed unconditionally, and this asserts it structurally: its
        install line comes before anything branches on the choice.
        """
        lines = self._INSTALL.read_text().splitlines()
        python_install = next(
            i for i, line in enumerate(lines)
            if "install -Dm0755" in line and "goblin_helper.py" in line
        )
        first_branch = next(
            i for i, line in enumerate(lines) if '"$HELPER_IMPL" = rust' in line
        )
        self.assertLess(
            python_install, first_branch,
            "the Python helper is installed inside a branch - a rollback would "
            "then depend on which implementation was chosen",
        )

    def test_the_rust_binary_is_checked_against_the_frozen_contract_first(self):
        """A helper that does not serve the contract is worse than none.

        It starts, claims the bus name, and answers nothing the daemon asks -
        which presents as a hang, not a failure. So the freshly built binary is
        asked what it serves and compared with the frozen file BEFORE it is
        allowed into /usr.
        """
        text = self._INSTALL.read_text()
        build = text.split("build_rust_helper()", 1)[1].split("\ninstall_helper()", 1)[0]
        self.assertIn("dbus-interface-v1.xml", build, "the build path checks no contract")
        self.assertIn("--introspect", build)
        # and the check must come before the install, not after it
        self.assertLess(
            build.index("dbus-interface-v1.xml"), build.index("sudo install"),
            "the contract check runs after the binary is already installed",
        )

    def test_uninstall_removes_the_libexec_directory(self):
        text = self._INSTALL.read_text()
        uninstall = text.split("uninstall()", 1)[1].split("\n}", 1)[0]
        self.assertIn("$LIBEXEC_DIR", uninstall, "uninstall leaves the symlink behind")


def _install_actions(path: Path) -> str:
    """The part of a packaging file that actually installs things.

    An rpm spec documents the relink command in its %description, which is
    prose telling a human what to do - not the package doing it. Scanning the
    whole file would read that as the package switching implementations by
    itself, which is exactly what this must not do and exactly what the
    documentation exists to explain.
    """
    text = path.read_text()
    if path.suffix == ".spec":
        return text.split("%install", 1)[1].split("\n%package", 1)[0].split("\n%files", 1)[0]
    return text


class PackagedRustHelper(unittest.TestCase):
    """The Rust helper is packaged separately, and only IT is arch-specific.

    Making the whole package architecture-specific because one file in it is
    compiled would drop every non-x86 user of a package that is otherwise pure
    Python, and would do it for no benefit at all. So the main package stays
    architecture-independent and the compiled helper is its own package.
    """

    def test_the_main_package_stays_architecture_independent(self):
        checks = {
            "packaging/debian/control": "Architecture: all",
            "packaging/rpm/goblin-mode-pro.spec": "BuildArch:      noarch",
            "packaging/arch/PKGBUILD": "arch=('any')",
            "packaging/aur/PKGBUILD": "arch=('any')",
        }
        for path, expected in checks.items():
            with self.subTest(path=path):
                self.assertIn(expected, (_REPO / path).read_text(),
                              f"{path} no longer keeps the main package arch-independent")

    def test_each_target_packages_the_rust_helper_separately(self):
        """One extra package per target, carrying the one compiled file."""
        checks = {
            "packaging/debian/control": "Package: goblin-mode-pro-helper-rust",
            "packaging/rpm/goblin-mode-pro.spec": "%package helper-rust",
            "packaging/arch/PKGBUILD": "package_goblin-mode-pro-helper-rust()",
            "packaging/aur/PKGBUILD": "package_goblin-mode-pro-helper-rust-git()",
        }
        for path, expected in checks.items():
            with self.subTest(path=path):
                text = (_REPO / path).read_text()
                self.assertIn(expected, text, f"{path} has no separate Rust package")
                self.assertIn("helper-rust", text)

    def test_installing_the_rust_package_does_not_switch_to_it(self):
        """It ships the binary; it must not touch the symlink.

        Installing a package should never silently change which implementation
        runs as root. Switching is the user relinking, deliberately.
        """
        for path in ("packaging/debian/rules", "packaging/rpm/goblin-mode-pro.spec",
                     "packaging/arch/PKGBUILD", "packaging/aur/PKGBUILD"):
            with self.subTest(path=path):
                for line in _install_actions(_REPO / path).splitlines():
                    if "ln -sfn" in line and "helper-rust" in line:
                        self.fail(f"{path} points the symlink at the Rust helper: {line}")

    def test_the_release_workflow_can_build_what_the_packaging_asks_for(self):
        """The packages compile Rust now; the release jobs have to be able to.

        This is the failure that would only show up when a tag is pushed - the
        worst possible moment, because by then the release is half-published.
        Both jobs built fine for years without a toolchain, so nothing else
        would notice that they suddenly need one.

        Parsed as text, not YAML: the CI job that runs these tests installs no
        pyyaml, and adding a dependency to check a workflow file is a poor
        trade.
        """
        workflow = (_REPO / ".github" / "workflows" / "release.yml").read_text()
        builds_rust = [
            path for path in ("packaging/debian/rules",
                              "packaging/rpm/goblin-mode-pro.spec")
            if "cargo build" in (_REPO / path).read_text()
        ]
        self.assertTrue(builds_rust, "no packaging target builds Rust any more")
        # the .deb job takes its toolchain from rustup, the .rpm job from dnf
        self.assertIn("dtolnay/rust-toolchain", workflow,
                      "the .deb job has no Rust toolchain")
        self.assertRegex(workflow, r"dnf install[^\n]*\bcargo\b",
                         "the .rpm job installs no cargo")

    def test_the_deb_job_collects_both_packages(self):
        """The glob has to widen when a second binary package appears.

        `goblin-mode-pro_*.deb` matches the main package only - the helper one
        is `goblin-mode-pro-helper-rust_*`, with a hyphen where the underscore
        was. It would have been silently left behind in the build directory.
        """
        workflow = (_REPO / ".github" / "workflows" / "release.yml").read_text()
        self.assertNotIn("goblin-mode-pro_*.deb", workflow,
                         "the deb glob still matches only the main package")

    def test_the_targets_that_build_rust_declare_a_toolchain(self):
        checks = {
            "packaging/debian/control": "cargo",
            "packaging/rpm/goblin-mode-pro.spec": "BuildRequires:  cargo",
            "packaging/arch/PKGBUILD": "makedepends=('cargo')",
            "packaging/aur/PKGBUILD": "'cargo'",
        }
        for path, expected in checks.items():
            with self.subTest(path=path):
                self.assertIn(expected, (_REPO / path).read_text(),
                              f"{path} builds Rust without declaring a toolchain")


if __name__ == "__main__":
    unittest.main()
