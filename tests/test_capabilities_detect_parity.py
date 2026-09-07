"""The Rust capability snapshot and the Python's `detect()` agree, whole.

This is the map that goes onto the daemon status and that the GUI hides
features from. A difference here is not a number that is slightly off - it is
a feature that appears on a machine that cannot do it, or vanishes from one
that can.

The eight probes underneath are graded on their own elsewhere. What is graded
here is the assembly: which answer wins when two could, and which answers are
conditional on another. Four of those are real decisions rather than lookups,
and the corpus has a machine for each side of every one.

Both sides get the same fixture machine. The Python is driven for real: its
`Path`, `glob`, `shutil.which`, `os.environ` and `platform.release` are all
redirected at the modules, and `detect`'s own cache is cleared between cases,
so what runs is `capabilities.detect`, not a copy of it.
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

from goblinmode import capabilities, scx

_REPO = Path(__file__).resolve().parent.parent
_SCRATCH = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "gmp-caps-detect-parity"

RAPL = "/sys/class/powercap"
POWER_LIMIT = "intel-rapl/intel-rapl:0/constraint_0_power_limit_uw"
BINS = ("/usr/bin", "/usr/local/bin")
VK_LAYER = "/usr/share/vulkan/implicit_layer.d/vkBasalt.json"


def _binary() -> Path | None:
    override = os.environ.get("GMP_CAPABILITIES_DETECT_BIN")
    if override:
        return Path(override) if Path(override).exists() else None
    for profile in ("debug", "release"):
        candidate = _REPO / "target" / profile / "examples" / "capabilities_detect"
        if candidate.exists():
            return candidate
    return None


def _redirect(mapping: dict[str, str]):
    """A `Path` that sends a handful of absolute paths into the fixture.

    The modules under test name those paths inside themselves; this moves
    them without moving the code that reads them."""
    real = Path

    def _P(*args):
        p = real(*args)
        text = str(p)
        for src, dst in mapping.items():
            if text == src:
                return real(dst)
            if text.startswith(src + "/"):
                return real(dst) / text[len(src) + 1:]
        return p

    return _P


def _python(case: dict) -> dict:
    have = set(case["have"])
    root = Path(case["cpu_root"]).parent
    mapping = {
        "/proc/cpuinfo": str(root / "proc/cpuinfo"),
        "/etc/os-release": str(root / "etc/os-release"),
        RAPL: case["powercap_root"],
        BINS[0]: case["scx_bin_dirs"][0],
        BINS[1]: case["scx_bin_dirs"][1],
        VK_LAYER: case["vkbasalt_layer"],
    }
    _P = _redirect(mapping)
    real_glob = capabilities.glob.glob

    def _glob_here(pattern: str) -> list[str]:
        drm = "/sys/class/drm"
        if pattern.startswith(drm + "/"):
            pattern = str(Path(case["drm_root"]) / pattern[len(drm) + 1:])
        return real_glob(pattern)

    def _which_here(name):
        return f"/usr/bin/{name}" if name in have else None

    capabilities.detect.cache_clear()
    try:
        with patch.object(capabilities, "Path", _P), \
                patch.object(scx, "Path", _P), \
                patch.object(capabilities, "_CPU", Path(case["cpu_root"])), \
                patch.object(capabilities, "_DMI", Path(case["dmi_root"])), \
                patch.object(scx, "SCHED_EXT_SYSFS",
                             Path(case["sched_ext_sysfs"])), \
                patch.object(capabilities.glob, "glob", _glob_here), \
                patch.object(capabilities.shutil, "which", _which_here), \
                patch.object(scx.shutil, "which", _which_here), \
                patch.object(capabilities.platform, "release",
                             lambda: case["kernel_release"]), \
                patch.dict(capabilities.os.environ, case["env"], clear=True), \
                patch.object(capabilities._has_writable_pwm, "__defaults__",
                             (Path(case["hwmon_root"]),)):
            return capabilities.detect()
    finally:
        capabilities.detect.cache_clear()


INTEL = ("processor\t: 0\nvendor_id\t: GenuineIntel\n"
         "model name\t: Intel(R) Core(TM) i9-14900K\n")
AMD = ("processor\t: 0\nvendor_id\t: AuthenticAMD\n"
       "model name\t: AMD Ryzen 7 7800X3D 8-Core Processor\n")


def _case(name: str, *, cpuinfo=INTEL, os_release="ID=arch\n",
          kernel_release="6.10.6-arch1-1", env=None, have=(), cpu=None,
          dmi=None, drm=None, hwmon=None, powercap=None, sched_ext=False,
          bins=(), links=(), extra=None, vk_layer=False) -> dict:
    root = _SCRATCH / name
    shutil.rmtree(root, ignore_errors=True)
    files = {"proc/cpuinfo": cpuinfo, "etc/os-release": os_release}
    for prefix, tree in (("cpu", cpu), ("dmi", dmi), ("drm", drm),
                         ("hwmon", hwmon), ("powercap", powercap)):
        for path, contents in (tree or {}).items():
            files[f"{prefix}/{path}"] = contents
    for tool in bins:
        files[f"bin/{tool}"] = "#!/bin/sh\n"
    files.update(extra or {})
    for path, contents in files.items():
        full = root / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(contents)
    for prefix in ("cpu", "dmi", "drm", "hwmon", "powercap", "bin", "bin2"):
        (root / prefix).mkdir(parents=True, exist_ok=True)
    if sched_ext:
        (root / "sched_ext").mkdir(parents=True, exist_ok=True)
    if vk_layer:
        (root / "vkBasalt.json").write_text("{}\n")
    # Symlinks last, so a link to a file that is also in the tree resolves.
    for link, target in dict(links or {}).items():
        full = root / link
        full.parent.mkdir(parents=True, exist_ok=True)
        full.symlink_to(target)
    return {
        "cpuinfo": cpuinfo, "os_release": os_release,
        "kernel_release": kernel_release,
        "env": dict(env or {}), "have": list(have),
        "cpu_root": str(root / "cpu"), "dmi_root": str(root / "dmi"),
        "drm_root": str(root / "drm"), "hwmon_root": str(root / "hwmon"),
        "powercap_root": str(root / "powercap"),
        "sched_ext_sysfs": str(root / "sched_ext"),
        "scx_bin_dirs": [str(root / "bin"), str(root / "bin2")],
        "vkbasalt_layer": str(root / "vkBasalt.json"),
    }


def _corpus() -> list[dict]:
    intel_pstate = {"cpu0/cpufreq/scaling_driver": "intel_pstate\n",
                    "cpu0/cpufreq/scaling_governor": "powersave\n",
                    "cpu0/cpufreq/energy_performance_preference": "balance_performance\n",
                    "cpu0/cpufreq/cpuinfo_max_freq": "5700000\n",
                    "cpu1/cpufreq/cpuinfo_max_freq": "4400000\n",
                    "online": "0-1\n"}
    rapl = {POWER_LIMIT: "125000000\n"}
    return [
        # A full-fat Intel desktop: RAPL, EPP, a governor, an NVIDIA card,
        # every tool installed, KDE on Wayland, a hybrid core layout.
        _case("intel-everything", cpuinfo=INTEL, cpu=intel_pstate,
              powercap=rapl, drm={"card0/device/vendor": "0x10de\n"},
              hwmon={"hwmon0/pwm1": "128", "hwmon0/pwm1_enable": "2"},
              dmi={"product_name": "MS-7D25\n"},
              env={"XDG_CURRENT_DESKTOP": "KDE", "XDG_SESSION_TYPE": "wayland"},
              have=["pacman", "nvidia-smi", "gamescope", "gamemoderun",
                    "mangohud", "intel-undervolt", "ryzenadj", "scx_loader",
                    "gpu-screen-recorder", "vkBasalt"],
              sched_ext=True, bins=["scx_bpfland", "scx_rusty", "scx_loader"]),
        # The same machine with RAPL gone: tdp_control falls through to
        # ryzenadj even though this is an Intel part, because the check is on
        # the tool and not on the vendor.
        _case("intel-no-rapl", cpuinfo=INTEL, cpu=intel_pstate,
              have=["ryzenadj", "intel-undervolt"]),
        # ... and with neither, where there is no TDP control at all.
        _case("intel-no-tdp", cpuinfo=INTEL, cpu=intel_pstate, have=[]),
        # An AMD machine: amd_undervolt names ryzenadj, undervolt does not
        # name intel-undervolt even though it is installed. The two are gated
        # on the CPU vendor and this is the case that proves each gate.
        _case("amd-ryzenadj", cpuinfo=AMD,
              cpu={"cpu0/cpufreq/scaling_driver": "amd-pstate-epp\n",
                   "cpu0/cpufreq/scaling_governor": "powersave\n",
                   "cpu0/cpufreq/energy_performance_preference": "power\n",
                   "online": "0-15\n"},
              have=["ryzenadj", "intel-undervolt", "dnf"],
              drm={"card0/device/vendor": "0x1002\n"}),
        # An AMD machine WITHOUT ryzenadj: amd_undervolt is nothing, and so
        # is tdp_control.
        _case("amd-bare", cpuinfo=AMD, have=["intel-undervolt"]),
        # A machine of neither vendor: both undervolt fields are nothing no
        # matter what is installed.
        _case("other-vendor", cpuinfo="vendor_id\t: KVMKVMKVM\n",
              have=["ryzenadj", "intel-undervolt"]),
        # gpu_deep_stats is a conjunction. Each half alone, and both.
        _case("nvidia-no-smi", drm={"card0/device/vendor": "0x10de\n"}),
        _case("smi-no-nvidia", drm={"card0/device/vendor": "0x1002\n"},
              have=["nvidia-smi"]),
        _case("nvidia-and-smi", drm={"card0/device/vendor": "0x10de\n"},
              have=["nvidia-smi"]),
        # sched_ext: kernel without loader, loader without kernel, both, and
        # neither. The scheduler list is only read when BOTH are true, which
        # is the thing that stops a capability probe walking /usr/bin on
        # every machine that has never heard of sched_ext.
        _case("scx-kernel-only", sched_ext=True,
              bins=["scx_bpfland", "scx_loader"]),
        _case("scx-loader-only", have=["scx_loader"],
              bins=["scx_bpfland", "scx_loader"]),
        _case("scx-both", sched_ext=True, have=["scx_loader"],
              bins=["scx_rustland", "scx_lavd", "scx_loader"]),
        _case("scx-both-nothing-installed", sched_ext=True,
              have=["scx_loader"]),
        _case("scx-neither", bins=["scx_bpfland"]),
        # The same scheduler in both bin directories, and one that is a
        # directory rather than a file. Duplicates collapse; the directory is
        # not a scheduler.
        _case("scx-duplicates", sched_ext=True, have=["scx_loader"],
              bins=["scx_lavd", "scx_loader"],
              links={"bin2/scx_lavd": "../bin/scx_lavd",
                     "bin2/scx_bpfland": "../bin/scx_bpfland"}),
        # A scheduler that is only in the SECOND directory, reached through
        # a symlink, on a machine whose first directory holds `scxtop` - a
        # real tool that ships beside the schedulers and is not one. Three
        # things at once because each of them is a way to get this wrong that
        # a straightforward tree cannot tell apart: skipping the second
        # directory, refusing to follow the link, and matching `scx` where
        # the rule says `scx_`.
        _case("scx-second-dir", sched_ext=True, have=["scx_loader"],
              bins=["scx_loader", "scxtop"],
              extra={"tools/flash": "#!/bin/sh\n"},
              links={"bin2/scx_flash": "../tools/flash"}),
        # vkBasalt is an OR: the binary, or the layer file, or neither.
        _case("vk-binary", have=["vkBasalt"]),
        _case("vk-layer-only", vk_layer=True),
        _case("vk-both", have=["vkBasalt"], vk_layer=True),
        _case("vk-neither"),
        # EPP and the governor are found with a glob, which does NOT follow
        # symlinks, while RAPL is found with `exists`, which does. A broken
        # link is therefore an EPP that is present and a RAPL that is not -
        # the one place in this map where two existence tests disagree.
        _case("broken-links", cpuinfo=INTEL,
              cpu={"cpu0/cpufreq/scaling_driver": "intel_pstate\n"},
              links={"cpu/cpu0/cpufreq/energy_performance_preference": "nowhere",
                     "cpu/cpu0/cpufreq/scaling_governor": "nowhere",
                     "powercap/" + POWER_LIMIT: "nowhere"}),
        # A handheld: every narrow feature at once, on battery hardware.
        _case("steamdeck", cpuinfo=AMD,
              dmi={"product_name": "Jupiter\n", "sys_vendor": "Valve\n"},
              cpu={"cpu0/cpufreq/scaling_driver": "amd-pstate\n",
                   "cpu0/cpufreq/scaling_governor": "schedutil\n",
                   "online": "0-7\n"},
              drm={"card0/device/vendor": "0x1002\n"},
              os_release="ID=steamos\n", kernel_release="6.5.0-valve-neptune",
              have=["pacman", "gamescope", "mangohud", "ryzenadj"]),
        # A machine with nothing at all: every field's empty answer at once.
        _case("nothing", cpuinfo="", os_release="", kernel_release=""),
    ]


class BothImplementationsAgree(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(_SCRATCH, ignore_errors=True)

    def setUp(self):
        self.binary = _binary()
        if self.binary is None:
            if os.environ.get("GMP_REQUIRE_RUST_HELPER") == "1":
                self.fail("GMP_REQUIRE_RUST_HELPER=1 but the "
                          "capabilities_detect example is not built - run "
                          "`cargo build -p gmp-core --example "
                          "capabilities_detect`")
            self.skipTest("build it with `cargo build -p gmp-core "
                          "--example capabilities_detect`")

    def _rust(self, cases: list[dict]) -> list[dict]:
        r = subprocess.run([str(self.binary)], input=json.dumps({"cases": cases}),
                           capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_every_machine_gets_the_same_snapshot(self):
        cases = _corpus()
        rust = self._rust(cases)
        self.assertEqual(len(rust), len(cases))
        for case, got in zip(cases, rust, strict=True):
            with self.subTest(case=Path(case["cpu_root"]).parent.name):
                self.assertEqual(got, _python(case))

    def test_the_two_maps_have_exactly_the_same_keys(self):
        """A key added on one side and not the other is a feature the GUI
        cannot see. Checked on its own so the message says so."""
        case = _corpus()[0]
        self.assertEqual(sorted(self._rust([case])[0]), sorted(_python(case)))

    def test_the_corpus_reaches_both_answers_for_every_yes_or_no(self):
        seen: dict[str, set] = {}
        for got in self._rust(_corpus()):
            for key, value in got.items():
                seen.setdefault(key, set()).add(json.dumps(value))
        for key, values in sorted(seen.items()):
            if values <= {"true", "false"}:
                # sorted() rather than pop(): the message is built before the
                # assertion runs, and pop() would empty the set being graded.
                self.assertEqual(
                    values, {"true", "false"},
                    f"{key} is only ever {sorted(values)}")
        self.assertEqual(seen["tdp_control"], {'"rapl"', '"ryzenadj"', "null"})
        self.assertEqual(seen["undervolt"], {'"intel-undervolt"', "null"})
        self.assertEqual(seen["amd_undervolt"], {'"ryzenadj"', "null"})
        self.assertEqual(seen["cpu_vendor"], {'"intel"', '"amd"', '"other"'})


if __name__ == "__main__":
    unittest.main()
