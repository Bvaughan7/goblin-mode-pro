"""The Rust environment probes and the Python ones agree.

These are the probes that do not read a tree: what kernel this is, what
desktop is running it, which package manager and which recorder are on
`$PATH`. Plus the two odd ones that DO read sysfs but read the corners of it
nothing else touches - whether any fan has a writable pwm pair, and whether
this machine is on mains.

`$PATH` and the environment are handed to both sides as data rather than set
up for real, so a case can describe a GNOME-on-Wayland machine with `dnf`
and `wf-recorder` without any of that being installed here. The Python's
`shutil.which` and `os.environ` are redirected at the module, so what runs is
`capabilities._package_manager`, not a copy of it.

The two sysfs probes get fixture trees, for the reason every probe here does:
this desktop has one hwmon with a writable pwm and no battery at all, and can
therefore confirm exactly one answer out of each probe's three.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from tests._support import _SRC  # noqa: F401

from goblinmode import capabilities

_REPO = Path(__file__).resolve().parent.parent
_SCRATCH = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "gmp-caps-env-parity"


def _binary() -> Path | None:
    override = os.environ.get("GMP_CAPABILITIES_ENV_BIN")
    if override:
        return Path(override) if Path(override).exists() else None
    for profile in ("debug", "release"):
        candidate = _REPO / "target" / profile / "examples" / "capabilities_env"
        if candidate.exists():
            return candidate
    return None


def _python(case: dict) -> dict:
    have = set(case["have"])

    def _which_here(name):
        return f"/usr/bin/{name}" if name in have else None

    with patch.object(capabilities.shutil, "which", _which_here), \
            patch.object(capabilities.platform, "release",
                         lambda: case["release"]), \
            patch.dict(capabilities.os.environ, case["env"], clear=True):
        return {
            "kernel_flavor": capabilities._kernel_flavor(),
            "compositor": capabilities._compositor(),
            "package_manager": capabilities._package_manager(),
            "session_recorder": capabilities._session_recorder(),
            "fan_control": capabilities._has_writable_pwm(
                Path(case["hwmon_root"])),
            "on_ac_power": _on_ac_power(Path(case["supply_root"])),
        }


def _on_ac_power(root: Path):
    """`on_ac_power` names its root inside itself, so it is called through a
    patch of the one constant it builds from rather than reimplemented."""
    real = capabilities.Path

    class _Path(type(Path())):
        def __new__(cls, *args):
            if args == ("/sys/class/power_supply",):
                return real(root)
            return real(*args)

    with patch.object(capabilities, "Path", _Path):
        return capabilities.on_ac_power()


def _case(name: str, *, release="6.10.0-arch1-1", env=None, have=(),
          hwmon=None, supply=None) -> dict:
    root = _SCRATCH / name
    shutil.rmtree(root, ignore_errors=True)
    for prefix, tree in (("hwmon", hwmon), ("supply", supply)):
        for path, contents in (tree or {}).items():
            full = root / prefix / path
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(contents)
    # A missing root and an empty one are different answers for both of these,
    # so only the trees that were asked for exist.
    return {
        "release": release, "env": dict(env or {}), "have": list(have),
        "hwmon_root": str(root / "hwmon") if hwmon is not None else str(root / "nope"),
        "supply_root": str(root / "supply") if supply is not None else str(root / "nope"),
    }


def _corpus() -> list[dict]:
    return [
        # Every kernel flavour tag, one machine each. They are checked in
        # order and the corpus has to be able to tell that order apart.
        *[_case(f"kernel-{tag}", release=rel) for tag, rel in (
            ("cachyos", "6.10.6-2-cachyos"),
            ("xanmod", "6.10.0-x64v3-xanmod1"),
            ("liquorix", "6.10.0-15.1-liquorix-amd64"),
            ("lqx", "6.10.0-15.1-lqx1-amd64"),
            ("zen", "6.10.6-zen1-1-zen"),
            ("tkg", "6.10.0-tkg-eevdf"),
            ("nobara", "6.10.3-201.nobara.fc40.x86_64"),
            ("bazzite", "6.10.3-201.bazzite.fc40.x86_64"),
            ("clear", "6.10.4-1441.native.clear"),
            ("xero", "6.10.6-1-xero"),
            ("lts", "6.6.48-1-lts"),
            ("generic", "6.10.6-arch1-1"),
            ("generic-x", "5.15.0-119-generic"),
        )],
        # An uppercase release, because the tag test lowercases first.
        _case("kernel-shouty", release="6.10.0-CACHYOS"),
        # A release that ends in -lts with no other tag, and one that carries
        # both - the tag list is checked BEFORE the -lts rule, so zen wins.
        _case("kernel-lts-zen", release="6.6.48-1-lts-zen"),
        # Every compositor branch. KDE and GNOME each answer twice depending
        # on the session type, and the last two are only reachable when
        # XDG_CURRENT_DESKTOP says nothing recognised.
        _case("kde-wayland", env={"XDG_CURRENT_DESKTOP": "KDE",
                                  "XDG_SESSION_TYPE": "wayland"}),
        _case("kde-x11", env={"XDG_CURRENT_DESKTOP": "KDE",
                              "XDG_SESSION_TYPE": "x11"}),
        # KDE with no session type at all: not wayland, therefore x11.
        _case("kde-nothing", env={"XDG_CURRENT_DESKTOP": "KDE"}),
        # The desktop is uppercased before it is matched, and it is a
        # SUBSTRING test - a Plasma session announces itself this way.
        _case("kde-plasma", env={"XDG_CURRENT_DESKTOP": "kde",
                                 "XDG_SESSION_TYPE": "wayland"}),
        _case("kde-ubuntu-style", env={"XDG_CURRENT_DESKTOP": "KDE:Plasma",
                                       "XDG_SESSION_TYPE": "wayland"}),
        _case("gnome-wayland", env={"XDG_CURRENT_DESKTOP": "GNOME",
                                    "XDG_SESSION_TYPE": "wayland"}),
        _case("gnome-x11", env={"XDG_CURRENT_DESKTOP": "ubuntu:GNOME",
                                "XDG_SESSION_TYPE": "x11"}),
        # Hyprland and Sway are found by their own variables, and only when
        # the desktop name is not one of the two above.
        _case("hyprland", env={"XDG_CURRENT_DESKTOP": "Hyprland",
                               "XDG_SESSION_TYPE": "wayland",
                               "HYPRLAND_INSTANCE_SIGNATURE": "abc123"}),
        _case("sway", env={"XDG_CURRENT_DESKTOP": "sway",
                           "XDG_SESSION_TYPE": "wayland",
                           "SWAYSOCK": "/run/user/1000/sway-ipc.sock"}),
        # Hyprland is checked first, so a machine somehow running both still
        # says hyprland.
        _case("both-wlroots", env={"HYPRLAND_INSTANCE_SIGNATURE": "abc",
                                   "SWAYSOCK": "/tmp/s", "XDG_SESSION_TYPE": "wayland"}),
        # Nothing recognised: the session type is the answer, and when there
        # is not even one of those, the word `unknown`.
        _case("bare-x11", env={"XDG_SESSION_TYPE": "x11"}),
        _case("bare-tty", env={"XDG_SESSION_TYPE": "tty"}),
        _case("nothing-at-all", env={}),
        # A KDE variable set to the empty string is not KDE.
        _case("empty-desktop", env={"XDG_CURRENT_DESKTOP": "",
                                    "XDG_SESSION_TYPE": "wayland"}),
        # An empty HYPRLAND_INSTANCE_SIGNATURE is falsy, so it is not Hyprland
        # either - which is the difference between `in os.environ` and the
        # truthiness test the Python actually does.
        _case("empty-hyprland", env={"HYPRLAND_INSTANCE_SIGNATURE": "",
                                     "XDG_SESSION_TYPE": "wayland"}),
        # Every package manager, one machine each, in the order they are
        # tried. apt-get is the only one renamed on the way out.
        *[_case(f"pm-{pm}", have=[pm]) for pm in (
            "pacman", "apt-get", "dnf", "zypper", "xbps-install", "eopkg",
            "emerge")],
        # A machine with several: the first in the list wins, and the list's
        # order is a decision (pacman before apt on a distro that has both
        # through a compatibility shim).
        _case("pm-several", have=["emerge", "dnf", "pacman"]),
        _case("pm-apt-and-dnf", have=["dnf", "apt-get"]),
        _case("pm-none", have=[]),
        # Every recorder, in order, plus the several-at-once case.
        *[_case(f"rec-{tool}", have=[tool]) for tool in (
            "gpu-screen-recorder", "wf-recorder", "obs", "spectacle")],
        _case("rec-several", have=["spectacle", "obs", "wf-recorder"]),
        # A machine with a full toolbox, which is what this desktop is.
        _case("everything", have=["pacman", "gpu-screen-recorder", "obs",
                                  "spectacle", "wf-recorder", "dnf"],
              env={"XDG_CURRENT_DESKTOP": "KDE", "XDG_SESSION_TYPE": "wayland"}),
        # Fan control: the pwmN + pwmN_enable pair, and each of the ways it
        # can be almost-there.
        _case("fan-yes", hwmon={"hwmon0/pwm1": "128", "hwmon0/pwm1_enable": "2"}),
        _case("fan-no-enable", hwmon={"hwmon0/pwm1": "128"}),
        _case("fan-enable-only", hwmon={"hwmon0/pwm1_enable": "2"}),
        # pwm1_enable globs as `pwm[0-9]*` too, but it is not `pwm\\d+`, so it
        # cannot stand in for the pwm file itself. Without the fullmatch this
        # tree would answer yes.
        _case("fan-enable-enable", hwmon={"hwmon0/pwm1_enable": "2",
                                          "hwmon0/pwm1_enable_enable": "2"}),
        # The pair has to be in the SAME hwmon.
        _case("fan-split", hwmon={"hwmon0/pwm1": "128",
                                  "hwmon1/pwm1_enable": "2"}),
        # A two-digit pwm, and a second hwmon that only answers on the second
        # pass.
        _case("fan-later-hwmon", hwmon={"hwmon0/temp1_input": "40000",
                                        "hwmon3/pwm12": "128",
                                        "hwmon3/pwm12_enable": "2"}),
        # A pwm pair somewhere under /sys/class/hwmon that is not in an
        # hwmonN directory. Nothing puts one there, but without the prefix
        # test this tree would answer yes.
        _case("fan-not-an-hwmon", hwmon={"acpitz/pwm1": "128",
                                         "acpitz/pwm1_enable": "2"}),
        _case("fan-empty-tree", hwmon={}),
        _case("fan-no-tree"),
        # Mains: on, off, several supplies, a battery-only laptop, and a
        # desktop with no power_supply directory at all.
        _case("ac-on", supply={"AC/type": "Mains\n", "AC/online": "1\n"}),
        _case("ac-off", supply={"AC/type": "Mains\n", "AC/online": "0\n"}),
        _case("ac-battery-only", supply={"BAT0/type": "Battery\n",
                                         "BAT0/capacity": "80\n"}),
        # A Mains node that publishes no `online` is skipped, and the battery
        # beside it is not an answer either.
        _case("ac-mains-no-online", supply={"AC/type": "Mains\n",
                                            "BAT0/type": "Battery\n"}),
        # A handheld's dock: a Mains that says nothing, and one that does.
        _case("ac-two-mains", supply={"ACAD/type": "Mains\n",
                                      "ADP1/type": "Mains\n", "ADP1/online": "1\n"}),
        # A battery that publishes `online` - some USB-PD and handheld
        # batteries do. It is not a mains supply, so it is not an answer, and
        # a probe that skipped the type check would call this machine
        # unplugged rather than unknown.
        _case("ac-battery-with-online", supply={"BAT0/type": "Battery\n",
                                                "BAT0/online": "0\n"}),
        _case("ac-empty-tree", supply={}),
        _case("ac-no-tree"),
    ]


class BothImplementationsAgree(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(_SCRATCH, ignore_errors=True)

    def setUp(self):
        self.binary = _binary()
        if self.binary is None:
            if os.environ.get("GMP_REQUIRE_RUST_HELPER") == "1":
                self.fail("GMP_REQUIRE_RUST_HELPER=1 but the capabilities_env "
                          "example is not built - run `cargo build -p gmp-core "
                          "--example capabilities_env`")
            self.skipTest("build it with `cargo build -p gmp-core "
                          "--example capabilities_env`")

    def _rust(self, cases: list[dict]) -> list[dict]:
        r = subprocess.run([str(self.binary)], input=json.dumps({"cases": cases}),
                           capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_every_machine_is_described_the_same_way(self):
        cases = _corpus()
        rust = self._rust(cases)
        self.assertEqual(len(rust), len(cases))
        for case, got in zip(cases, rust, strict=True):
            with self.subTest(case=Path(case["hwmon_root"]).parent.name):
                self.assertEqual(got, _python(case))

    def test_this_machine_is_described_the_same_way(self):
        """The one case nobody invented."""
        import platform
        case = {
            "release": platform.release(),
            "env": {k: v for k, v in os.environ.items()
                    if k in ("XDG_CURRENT_DESKTOP", "XDG_SESSION_TYPE",
                             "HYPRLAND_INSTANCE_SIGNATURE", "SWAYSOCK")},
            "have": [t for t in ("pacman", "apt-get", "dnf", "zypper",
                                 "xbps-install", "eopkg", "emerge",
                                 "gpu-screen-recorder", "wf-recorder", "obs",
                                 "spectacle") if shutil.which(t)],
            "hwmon_root": "/sys/class/hwmon",
            "supply_root": "/sys/class/power_supply",
        }
        self.assertEqual(self._rust([case])[0], _python(case))

    def test_the_corpus_reaches_every_answer_each_probe_can_give(self):
        seen: dict[str, set] = {}
        for got in self._rust(_corpus()):
            for key, value in got.items():
                seen.setdefault(key, set()).add(json.dumps(value))
        self.assertEqual(
            seen["kernel_flavor"],
            {json.dumps(f) for f in ("cachyos", "xanmod", "lqx", "zen", "tkg",
                                     "nobara", "bazzite", "clear", "xero",
                                     "lts", "generic")})
        self.assertEqual(
            seen["compositor"],
            {json.dumps(c) for c in ("kwin-wayland", "kwin-x11",
                                     "mutter-wayland", "mutter-x11",
                                     "hyprland", "sway", "x11", "tty",
                                     "wayland", "unknown")})
        self.assertEqual(
            seen["package_manager"],
            {json.dumps(p) for p in ("pacman", "apt", "dnf", "zypper",
                                     "xbps-install", "eopkg", "emerge", None)})
        self.assertEqual(
            seen["session_recorder"],
            {json.dumps(r) for r in ("gpu-screen-recorder", "wf-recorder",
                                     "obs", "spectacle", None)})
        self.assertEqual(seen["fan_control"], {"true", "false"})
        self.assertEqual(seen["on_ac_power"], {"true", "false", "null"})


if __name__ == "__main__":
    unittest.main()
