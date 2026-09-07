"""The Rust machine probes and the Python ones read a tree the same way.

These are probes, not decisions: nothing here computes anything, it all reads
files and says what it found. That makes them easy to port and easy to port
WRONG, because a probe that reads the wrong file does not raise - it returns
a plausible answer about a different machine, and everything downstream
believes it.

So they are graded against fixture trees rather than against this machine.
This is an Intel desktop with one AMD card; it can never confirm what the
handheld rule does on a Steam Deck, or what happens on a machine with ten DRM
devices, or what a kernel with no cpufreq driver reports. The fixtures are the
author's idea of what those look like - which is why the last case in the
corpus is this machine's REAL /proc, /sys and /etc, where the shape is not
anybody's idea of anything.

The Python is driven for real. Its probes name absolute paths inside
themselves, so the reader and the globber are redirected at the module rather
than reimplemented here: what runs is `capabilities._cpu_model`, not a copy of
it.
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
_SCRATCH = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "gmp-caps-parity"

# The two files the Python probes name as literals rather than as roots.
CPUINFO = "/proc/cpuinfo"
OS_RELEASE = "/etc/os-release"
DRM = "/sys/class/drm"


def _binary() -> Path | None:
    override = os.environ.get("GMP_CAPABILITIES_PROBE_BIN")
    if override:
        return Path(override) if Path(override).exists() else None
    for profile in ("debug", "release"):
        candidate = _REPO / "target" / profile / "examples" / "capabilities_probe"
        if candidate.exists():
            return candidate
    return None


def _write(root: Path, files: dict[str, str]) -> Path:
    for name, contents in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _python(case: dict) -> dict:
    """Run the real probes with their roots pointed at this case."""
    read = capabilities._read
    redirect = {
        CPUINFO: case["cpuinfo_path"],
        OS_RELEASE: case["os_release_path"],
    }

    def _read_here(path):
        return read(redirect.get(str(path), path))

    real_glob = capabilities.glob.glob

    def _glob_here(pattern: str) -> list[str]:
        # The one glob in this module. Rewritten rather than replaced, so the
        # pattern itself - `card[0-9]`, single digit - is still the Python's.
        assert pattern.startswith(DRM + "/"), pattern
        return real_glob(str(Path(case["drm_root"]) / pattern[len(DRM) + 1:]))

    def _which_here(name):
        return "/usr/bin/nvidia-smi" if (
            name == "nvidia-smi" and case["nvidia_smi"]) else None

    with patch.object(capabilities, "_read", _read_here), \
            patch.object(capabilities, "_CPU", Path(case["cpu_root"])), \
            patch.object(capabilities, "_DMI", Path(case["dmi_root"])), \
            patch.object(capabilities.glob, "glob", _glob_here), \
            patch.object(capabilities.shutil, "which", _which_here):
        return {
            "cpu_vendor": capabilities._cpu_vendor(),
            "cpu_model": capabilities._cpu_model(),
            "cpufreq_driver": capabilities._cpufreq_driver(),
            "gpu_vendors": capabilities._gpu_vendors(),
            "handheld": capabilities._handheld(),
            "distro_id": capabilities._distro_id(),
        }


def _case(name: str, *, cpuinfo="", os_release="", cpu=None, drm=None,
          dmi=None, nvidia_smi=False) -> dict:
    """Lay a fixture tree down on disk and describe it to both sides."""
    root = _SCRATCH / name
    shutil.rmtree(root, ignore_errors=True)
    files = {"proc/cpuinfo": cpuinfo, "etc/os-release": os_release}
    for prefix, tree in (("cpu", cpu), ("drm", drm), ("dmi", dmi)):
        for path, contents in (tree or {}).items():
            files[f"{prefix}/{path}"] = contents
    _write(root, files)
    for prefix in ("cpu", "drm", "dmi"):
        (root / prefix).mkdir(parents=True, exist_ok=True)
    return {
        "cpuinfo": cpuinfo, "os_release": os_release,
        "cpuinfo_path": str(root / "proc/cpuinfo"),
        "os_release_path": str(root / "etc/os-release"),
        "cpu_root": str(root / "cpu"), "drm_root": str(root / "drm"),
        "dmi_root": str(root / "dmi"), "nvidia_smi": nvidia_smi,
    }


INTEL = "processor\t: 0\nvendor_id\t: GenuineIntel\nmodel name\t: Intel(R) Core(TM) i9-14900K\n"
AMD = "processor\t: 0\nvendor_id\t: AuthenticAMD\nmodel name\t: AMD Ryzen 7 7800X3D 8-Core Processor\n"


def _corpus() -> list[dict]:
    return [
        # An ordinary Intel desktop with an NVIDIA card.
        _case("intel-nvidia", cpuinfo=INTEL,
              os_release='NAME="Arch Linux"\nID=arch\nID_LIKE=archlinux\n',
              cpu={"cpu0/cpufreq/scaling_driver": "intel_pstate\n"},
              drm={"card0/device/vendor": "0x10de\n"},
              dmi={"product_name": "MS-7D25\n", "board_name": "PRO Z690-A\n",
                   "sys_vendor": "Micro-Star International Co., Ltd.\n"},
              nvidia_smi=True),
        # AMD everything, two cards of the same vendor.
        _case("amd", cpuinfo=AMD,
              os_release='ID_LIKE="arch"\nID=cachyos\n',
              cpu={"cpu0/cpufreq/scaling_driver": "amd-pstate-epp\n"},
              drm={"card0/device/vendor": "0x1002\n",
                   "card1/device/vendor": "0x1002\n"}),
        # A Steam Deck: the valve/steam AND-rule, and an APU.
        _case("steamdeck", cpuinfo=AMD, os_release="ID=steamos\n",
              cpu={"cpu0/cpufreq/scaling_driver": "amd-pstate\n"},
              drm={"card0/device/vendor": "0x1002\n"},
              dmi={"product_name": "Jupiter\n", "board_name": "Jupiter\n",
                   "sys_vendor": "Valve\n"}),
        # A ROG Ally, which identifies itself in the board name only.
        _case("rog-ally", cpuinfo=AMD, os_release="ID=bazzite\n",
              cpu={"cpu0/cpufreq/scaling_driver": "amd-pstate-epp\n"},
              drm={"card0/device/vendor": "0x1002\n"},
              dmi={"product_name": "ROG Ally RC71L_RC71L\n",
                   "board_name": "RC71L\n", "sys_vendor": "ASUSTeK COMPUTER INC.\n"}),
        # A ROG Ally X, which is a different board code from the Ally.
        _case("rog-ally-x", cpuinfo=AMD, os_release="ID=bazzite\n",
              dmi={"product_name": "ROG Ally X RC72LA_RC72LA\n",
                   "board_name": "RC72LA\n", "sys_vendor": "ASUSTeK COMPUTER INC.\n"}),
        # An Ally that reports only its board code, with the words "ROG Ally"
        # nowhere in DMI. That is the whole reason the code is in the rules,
        # so both codes get a machine of their own.
        _case("bare-rc71", cpuinfo=AMD, os_release="ID=bazzite\n",
              dmi={"product_name": "RC71L\n", "sys_vendor": "ASUSTeK COMPUTER INC.\n"}),
        _case("bare-rc72", cpuinfo=AMD, os_release="ID=bazzite\n",
              dmi={"product_name": "RC72LA\n", "sys_vendor": "ASUSTeK COMPUTER INC.\n"}),
        # ... and the mirror of those: the marketing words with no board code,
        # which is what a variant nobody has seen yet would look like. Each
        # token in these rules gets a machine that needs it and no other.
        _case("words-only-ally", cpuinfo=AMD, os_release="ID=bazzite\n",
              dmi={"product_name": "ROG Ally\n", "sys_vendor": "ASUSTeK COMPUTER INC.\n"}),
        _case("words-only-legion", cpuinfo=AMD, os_release="ID=nobara\n",
              dmi={"product_name": "Legion Go S\n", "sys_vendor": "LENOVO\n"}),
        # The four makers that share one answer. All present, because they
        # share the answer and not the rule.
        _case("aokzoe", cpuinfo=AMD, os_release="ID=arch\n",
              dmi={"product_name": "AOKZOE A1 Pro\n", "sys_vendor": "AOKZOE\n"}),
        _case("onexplayer", cpuinfo=AMD, os_release="ID=arch\n",
              dmi={"product_name": "ONEXPLAYER F1\n", "sys_vendor": "ONE-NETBOOK\n"}),
        _case("ayaneo", cpuinfo=AMD, os_release="ID=arch\n",
              dmi={"product_name": "AYANEO FLIP DS\n", "sys_vendor": "AYANEO\n"}),
        _case("aya-neo-spaced", cpuinfo=AMD, os_release="ID=arch\n",
              dmi={"product_name": "AYA NEO FOUNDER\n", "sys_vendor": "AYA\n"}),
        # A Deck whose product name is the marketing one and whose maker is in
        # a different field: the valve/steam rule needs BOTH fields here, so
        # neither can be dropped from the joined string without this failing.
        _case("deck-split-fields", cpuinfo=AMD, os_release="ID=steamos\n",
              dmi={"product_name": "Steam Deck\n", "sys_vendor": "Valve\n"}),
        # And one identified by the BOARD name alone, which is the field a
        # port is most likely to leave out - the other two are the obvious ones.
        _case("board-name-only", cpuinfo=AMD, os_release="ID=holoiso\n",
              dmi={"product_name": "Default string\n", "board_name": "Galileo\n",
                   "sys_vendor": "Default string\n"}),
        # A Legion Go, whose product name is a bare code.
        _case("legion-go", cpuinfo=AMD, os_release="ID=nobara\n",
              dmi={"product_name": "83E1\n", "sys_vendor": "LENOVO\n"}),
        # A machine that merely has Steam on it, from a vendor called Valve
        # in a different field - the AND-rule must not fire on either alone.
        _case("not-a-deck", cpuinfo=INTEL, os_release='ID="ubuntu"\n',
              dmi={"product_name": "Steam Machine Replica\n",
                   "sys_vendor": "Nobody\n"}),
        _case("valve-alone", cpuinfo=INTEL, os_release="ID=debian\n",
              dmi={"sys_vendor": "Valve Corporation\n"}),
        # Eleven DRM devices. `card[0-9]` is one digit, so card10 is invisible
        # - reproduced, not widened, and pinned here so nobody "fixes" it by
        # accident.
        _case("eleven-cards", cpuinfo=INTEL, os_release="ID=fedora\n",
              drm={f"card{n}/device/vendor": "0x8086\n" for n in range(10)}
                  | {"card10/device/vendor": "0x10de\n",
                     "renderD128/device/vendor": "0x10de\n"}),
        # A card this build cannot name, alone: dropped, so the answer is the
        # single word `unknown` rather than a list.
        _case("unknown-card", cpuinfo=INTEL, os_release="ID=gentoo\n",
              drm={"card0/device/vendor": "0x1af4\n"}),
        # ... and beside one it can, where it is dropped silently.
        _case("unknown-plus-known", cpuinfo=INTEL, os_release="ID=opensuse\n",
              drm={"card0/device/vendor": "0x1af4\n",
                   "card1/device/vendor": "0x8086\n"}),
        # nvidia-smi present with no card in sysfs, which is what a container
        # with the driver bind-mounted in looks like.
        _case("smi-no-card", cpuinfo=INTEL, os_release="ID=arch\n",
              nvidia_smi=True),
        # A vendor id in upper case. No kernel writes one, but the Python
        # lowercases before it looks the id up and a port that skips that step
        # would name every card `other` on a machine that did.
        _case("shouty-vendor", cpuinfo=INTEL, os_release="ID=arch\n",
              drm={"card0/device/vendor": "0x10DE\n"}),
        # A cpuinfo whose ONLY model-name line is the first one and is
        # indented. The Python strips the file before it splits it, so this
        # reads; a port that splits the raw text finds nothing.
        _case("indented-model", cpuinfo="  model name\t: Indented\n",
              os_release="  ID=arch\n"),
        # A VM: no cpufreq driver at all, no DMI worth the name, an unknown
        # CPU vendor, and an os-release with only ID_LIKE.
        _case("vm", cpuinfo="processor\t: 0\nvendor_id\t: KVMKVMKVM\n",
              os_release='NAME="Some Linux"\nID_LIKE=debian\n',
              drm={"card0/device/vendor": "0x1234\n"},
              dmi={"sys_vendor": "QEMU\n"}),
        # Nothing readable anywhere - every probe's empty answer at once.
        _case("empty"),
        # A model name longer than the eighty-character cap, with non-ASCII in
        # it so the cap is counted in characters and not in bytes.
        _case("long-model",
              cpuinfo="model name\t: " + "µ" * 200 + "\n",
              os_release="ID=arch\n"),
        # A cpuinfo whose first line is blank and whose model name is spelled
        # in another case, which is what a non-x86 kernel looks like.
        _case("odd-cpuinfo",
              cpuinfo="\n\nModel Name\t: Some ARM thing\nvendor_id\t: other\n",
              os_release="\n\nID=alpine\n"),
        # Two model-name lines: the first wins.
        _case("two-models",
              cpuinfo="model name\t: first\nmodel name\t: second\n",
              os_release="ID=arch\nID=second\n"),
        # A scaling_driver file that exists but is empty - still `none`.
        _case("blank-driver", cpuinfo=INTEL, os_release="ID=arch\n",
              cpu={"cpu0/cpufreq/scaling_driver": "\n"}),
    ]


def _real_case() -> dict:
    """This machine, unredirected. Not anybody's idea of what sysfs looks
    like, which is the point of including it."""
    def read(path: str) -> str:
        try:
            return Path(path).read_text()
        except OSError:
            return ""
    return {
        "cpuinfo": read(CPUINFO), "os_release": read(OS_RELEASE),
        "cpuinfo_path": CPUINFO, "os_release_path": OS_RELEASE,
        "cpu_root": "/sys/devices/system/cpu", "drm_root": DRM,
        "dmi_root": "/sys/class/dmi/id",
        "nvidia_smi": shutil.which("nvidia-smi") is not None,
    }


class BothImplementationsAgree(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(_SCRATCH, ignore_errors=True)

    def setUp(self):
        self.binary = _binary()
        if self.binary is None:
            if os.environ.get("GMP_REQUIRE_RUST_HELPER") == "1":
                self.fail("GMP_REQUIRE_RUST_HELPER=1 but the capabilities_probe "
                          "example is not built - run `cargo build -p gmp-core "
                          "--example capabilities_probe`")
            self.skipTest("build it with `cargo build -p gmp-core "
                          "--example capabilities_probe`")

    def _rust(self, cases: list[dict]) -> list[dict]:
        r = subprocess.run([str(self.binary)], input=json.dumps({"cases": cases}),
                           capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_every_fixture_machine_is_read_the_same_way(self):
        cases = _corpus()
        rust = self._rust(cases)
        self.assertEqual(len(rust), len(cases))
        for case, got in zip(cases, rust, strict=True):
            with self.subTest(case=Path(case["cpu_root"]).parent.name):
                self.assertEqual(got, _python(case))

    def test_this_machine_is_read_the_same_way(self):
        """The one case in the corpus nobody invented."""
        case = _real_case()
        if not case["cpuinfo"]:
            self.skipTest("no readable /proc/cpuinfo")
        self.assertEqual(self._rust([case])[0], _python(case))

    def test_the_corpus_reaches_every_answer_each_probe_can_give(self):
        """A corpus that only ever sees one answer proves nothing about the
        branch that produces the other. Checked here rather than trusted."""
        seen: dict[str, set] = {}
        for got in self._rust(_corpus()):
            for key, value in got.items():
                seen.setdefault(key, set()).add(json.dumps(value))
        self.assertEqual(seen["cpu_vendor"],
                         {'"intel"', '"amd"', '"other"'})
        self.assertEqual(
            seen["handheld"],
            {"null", '"steamdeck"', '"rog_ally"', '"legion_go"',
             '"other_handheld"'})
        self.assertIn('""', seen["cpu_model"])
        self.assertIn('"none"', seen["cpufreq_driver"])
        self.assertIn('["unknown"]', seen["gpu_vendors"])
        self.assertIn('""', seen["distro_id"])
        self.assertGreater(len(seen["gpu_vendors"]), 3)


if __name__ == "__main__":
    unittest.main()
