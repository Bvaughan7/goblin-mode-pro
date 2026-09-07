"""What the dashboard says is currently applied.

The row is built from the same `tweaks` object the CLI and the session
fingerprint read, and it had the same gap: the kernel scheduler was the one
thing the daemon could change that nothing downstream reported. Extracted from
the widget so it can be asked without a display.
"""

from __future__ import annotations

import unittest

from tests._support import _SRC  # noqa: F401

from goblinmode.gui.page_dashboard import active_tweak_labels


class TheActiveTweaksRow(unittest.TestCase):
    def test_nothing_applied_is_an_empty_list(self):
        self.assertEqual(active_tweak_labels({}), [])

    def test_the_governor_counts_when_only_the_finer_knob_moved(self):
        """Same rule as the session fingerprint: on intel_pstate the EPP moves
        without the governor being pinned, and that is not an untuned run."""
        self.assertEqual(active_tweak_labels({"epp_boosted": True}), ["governor"])
        self.assertEqual(active_tweak_labels({"governor": "performance"}),
                         ["governor"])
        self.assertEqual(active_tweak_labels({"governor": "powersave"}), [])

    def test_renice_carries_how_many_processes(self):
        self.assertEqual(
            active_tweak_labels({"reniced": {"Wow.exe": 1, "rs2client": 2}}),
            ["renice\u00d72"])

    def test_the_scheduler_is_named(self):
        """The biggest lever in the tool, and the dashboard did not mention
        it: the row was built from a fixed list of keys that did not include
        it, exactly like the fingerprint and the CLI branch that could never
        run."""
        self.assertEqual(active_tweak_labels({"scx_scheduler": "lavd"}),
                         ["scx_lavd"])
        self.assertEqual(active_tweak_labels({"scx_scheduler": None}), [])

    def test_everything_at_once_reads_in_a_fixed_order(self):
        self.assertEqual(
            active_tweak_labels({
                "governor": "performance", "power_limited": True,
                "tearing": True, "adaptive_sync": True,
                "reniced": {"Wow.exe": 1}, "scx_scheduler": "lavd",
                "mangohud_files": ["/tmp/x"],
            }),
            ["governor", "power-limit", "tearing", "VRR", "renice\u00d71",
             "scx_lavd", "mangohud"])

    def test_a_tweaks_object_that_is_not_one(self):
        """It crosses the frozen interface as a JSON string, so its shape is
        not guaranteed by the signature."""
        self.assertEqual(active_tweak_labels(None), [])
        self.assertEqual(active_tweak_labels("nope"), [])
        self.assertEqual(active_tweak_labels({"reniced": "not a mapping"}), [])


if __name__ == "__main__":
    unittest.main()
