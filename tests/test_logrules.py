import unittest

from tests._support import _SRC  # noqa: F401

from goblinmode import logrules


class AnalyzeText(unittest.TestCase):
    def test_matches_a_known_rule(self):
        findings = logrules.analyze_text("err:module:import_dll api-ms-win-crt-runtime-l1-1-0.dll not found")
        ids = [f.rule_id for f in findings]
        self.assertIn("vcrun", ids)

    def test_fix_cmd_uses_placeholder_without_appid(self):
        findings = logrules.analyze_text("vcruntime140.dll not found")
        vcrun = next(f for f in findings if f.rule_id == "vcrun")
        self.assertEqual(vcrun.fix_cmd, "protontricks <appid> vcrun2022")

    def test_fix_cmd_substitutes_known_appid(self):
        findings = logrules.analyze_text("vcruntime140.dll not found", appid="123456")
        vcrun = next(f for f in findings if f.rule_id == "vcrun")
        self.assertEqual(vcrun.fix_cmd, "protontricks 123456 vcrun2022")

    def test_rule_without_fix_cmd_stays_none(self):
        findings = logrules.analyze_text("std::bad_alloc thrown")
        oom = next(f for f in findings if f.rule_id == "host_oom")
        self.assertIsNone(oom.fix_cmd)

    def test_no_matches_returns_empty(self):
        self.assertEqual(logrules.analyze_text("nothing interesting here"), [])


if __name__ == "__main__":
    unittest.main()
