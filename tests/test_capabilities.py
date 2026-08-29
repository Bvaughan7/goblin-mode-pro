import unittest
import unittest.mock

from tests._support import _SRC  # noqa: F401

from goblinmode import capabilities


class CpuListParsing(unittest.TestCase):
    def test_ranges_and_singletons(self):
        self.assertEqual(capabilities._parse_cpu_list("0-3,8,10-11"),
                         [0, 1, 2, 3, 8, 10, 11])

    def test_empty_and_whitespace(self):
        self.assertEqual(capabilities._parse_cpu_list(""), [])
        self.assertEqual(capabilities._parse_cpu_list(" 2 , 4 "), [2, 4])

    def test_garbage_is_skipped(self):
        self.assertEqual(capabilities._parse_cpu_list("0,foo,2-x,3"), [0, 3])


class InstallCommand(unittest.TestCase):
    def test_known_manager_builds_a_command(self):
        cmd = capabilities.install_command("pacman", "mangohud", "gamemode")
        self.assertEqual(cmd, "sudo pacman -S --needed mangohud gamemode")

    def test_per_manager_package_name_override(self):
        self.assertEqual(capabilities.install_command("xbps-install", "mangohud"),
                         "sudo xbps-install MangoHud")

    def test_unknown_manager_returns_none(self):
        self.assertIsNone(capabilities.install_command(None, "mangohud"))
        self.assertIsNone(capabilities.install_command("nonesuch", "mangohud"))

    def test_no_packages_returns_none(self):
        self.assertIsNone(capabilities.install_command("apt"))


class KernelUpgradeTip(unittest.TestCase):
    def test_known_distro(self):
        why, cmd = capabilities.kernel_upgrade_tip("arch")
        self.assertTrue(why)
        self.assertIn("pacman", cmd)

    def test_distro_with_no_recommendation(self):
        self.assertEqual(capabilities.kernel_upgrade_tip("cachyos"), ("", ""))

    def test_unknown_distro_falls_back(self):
        why, cmd = capabilities.kernel_upgrade_tip("some-unknown-distro")
        self.assertTrue(why)
        self.assertEqual(cmd, "")


class OnAcPower(unittest.TestCase):
    def test_no_power_supply_dir_returns_none(self):
        with unittest.mock.patch.object(capabilities.Path, "is_dir", return_value=False):
            self.assertIsNone(capabilities.on_ac_power())

    def test_returns_bool_or_none(self):
        # whatever this machine actually reports, it must be a valid tri-state
        self.assertIn(capabilities.on_ac_power(), (True, False, None))


class HandheldTdpPresets(unittest.TestCase):
    def test_every_model_has_an_ac_and_battery_preset(self):
        for model in capabilities.HANDHELD_TDP_PRESETS:
            self.assertIn(model, capabilities.HANDHELD_TDP_PRESETS_BATTERY)

    def test_battery_preset_is_lower_than_ac(self):
        for model, (ac1, ac2) in capabilities.HANDHELD_TDP_PRESETS.items():
            b1, b2 = capabilities.HANDHELD_TDP_PRESETS_BATTERY[model]
            self.assertLessEqual(b1, ac1)
            self.assertLessEqual(b2, ac2)


class WritablePwmDetection(unittest.TestCase):
    def test_no_hwmon_dir_returns_false(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            missing = capabilities.Path(d) / "nonexistent"
            self.assertFalse(capabilities._has_writable_pwm(missing))

    def test_finds_pwm_with_matching_enable_file(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            base = capabilities.Path(d)
            hwmon = base / "hwmon3"
            hwmon.mkdir()
            (hwmon / "pwm1").write_text("128")
            (hwmon / "pwm1_enable").write_text("2")
            self.assertTrue(capabilities._has_writable_pwm(base))

    def test_pwm_without_enable_sibling_is_ignored(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            base = capabilities.Path(d)
            hwmon = base / "hwmon0"
            hwmon.mkdir()
            (hwmon / "pwm1").write_text("128")  # no pwm1_enable
            self.assertFalse(capabilities._has_writable_pwm(base))


class DetectShape(unittest.TestCase):
    def test_detect_has_the_documented_keys(self):
        caps = capabilities.detect()
        for key in ("cpu_vendor", "cpufreq_driver", "governor_control",
                    "epp_control", "rapl_control", "tdp_control", "gpu_vendors",
                    "compositor", "core_layout"):
            self.assertIn(key, caps)
        self.assertIn("online", caps["core_layout"])
        self.assertIsInstance(caps["core_layout"]["online"], list)

    def test_tdp_control_is_consistent_with_rapl(self):
        caps = capabilities.detect()
        if caps["rapl_control"]:
            self.assertEqual(caps["tdp_control"], "rapl")


if __name__ == "__main__":
    unittest.main()
