import unittest

from tests._support import _SRC  # noqa: F401

from goblinmode import report


class BuildWorksForMe(unittest.TestCase):
    def test_shape_and_note_truncation(self):
        profile = {"exe": "game.exe", "display_name": "A Game", "steam_app_id": "123456",
                  "tearing_enabled": True, "amd_undervolt_reapply": True, "notes": "x"}
        rep = report.build_works_for_me(profile, note="x" * 900)
        self.assertEqual(rep["schema"], "gmp.worksforme.v1")
        self.assertEqual(rep["game"], "A Game")
        self.assertEqual(rep["steam_app_id"], "123456")
        self.assertEqual(len(rep["note"]), 500)

    def test_profile_is_filtered_to_shareable_fields(self):
        profile = {"exe": "game.exe", "tearing_enabled": True,
                  "amd_undervolt_reapply": True, "fan_spinup_enabled": True}
        rep = report.build_works_for_me(profile)
        self.assertIn("tearing_enabled", rep["profile"])
        self.assertNotIn("amd_undervolt_reapply", rep["profile"])
        self.assertNotIn("fan_spinup_enabled", rep["profile"])

    def test_falls_back_to_exe_when_no_display_name(self):
        rep = report.build_works_for_me({"exe": "bare.exe"})
        self.assertEqual(rep["game"], "bare.exe")


class WorksForMeMarkdown(unittest.TestCase):
    def test_includes_note_and_profile_json(self):
        rep = {"game": "A Game", "note": "runs great", "steam_app_id": "42",
              "system": {"cpu": "Some CPU", "gpu": "Some GPU"},
              "profile": {"tearing_enabled": True}}
        md = report.works_for_me_markdown(rep)
        self.assertIn("A Game", md)
        self.assertIn("runs great", md)
        self.assertIn("Some CPU", md)
        self.assertIn('"tearing_enabled": true', md)
        self.assertIn("42", md)

    def test_missing_note_is_omitted_cleanly(self):
        md = report.works_for_me_markdown({"game": "G", "system": {}, "profile": {}})
        self.assertNotIn(">", md.splitlines()[0])


class WorksForMeIssueUrl(unittest.TestCase):
    def test_url_targets_the_right_repo_and_label(self):
        rep = {"game": "A Game", "system": {}, "profile": {}}
        url = report.works_for_me_issue_url(rep)
        self.assertTrue(url.startswith(
            "https://github.com/Bvaughan7/goblin-mode-pro/issues/new?"))
        self.assertIn("labels=works-for-me", url)
        self.assertIn("title=", url)


class ActiveTweaksSection(unittest.TestCase):
    """A bug report is read by somebody trying to help. It saying the governor
    is tuned when it is not sends them looking in the wrong place.

    `governor` in the tweaks object holds a NAME. Every non-empty name is
    truthy, so the section listed it on every machine - which is the same
    mistake the CLI's status line made, in the same object, for the same
    reason.
    """

    def _section(self, tweaks: dict) -> str:
        md = report.as_markdown({"active_tweaks": tweaks}).splitlines()
        heading = md.index("### Active tweaks")
        return md[heading + 1]

    def test_an_untouched_machine_lists_nothing(self):
        self.assertIn("none", self._section({"governor": "powersave"}))
        self.assertIn("none", self._section({"governor": "schedutil"}))

    def test_a_pinned_governor_is_listed(self):
        self.assertIn("governor", self._section({"governor": "performance"}))

    def test_the_finer_knob_alone_still_counts(self):
        self.assertIn("governor",
                      self._section({"governor": "powersave", "epp_boosted": True}))

    def test_it_agrees_with_the_cli_about_the_same_object(self):
        """Two surfaces, deliberately separate constants, which may spell the
        answer differently and may not disagree about it."""
        from goblinmode import cli

        for tweaks in ({"governor": "powersave"}, {"governor": "performance"},
                       {"governor": "powersave", "epp_boosted": True},
                       {"tearing": True}, {}):
            with self.subTest(tweaks=tweaks):
                line = next(x for x in cli.status_lines({"tweaks": tweaks})
                            if x.startswith("active tweaks"))
                self.assertEqual("governor" in self._section(tweaks) if tweaks else False,
                                 "governor" in line)


if __name__ == "__main__":
    unittest.main()
