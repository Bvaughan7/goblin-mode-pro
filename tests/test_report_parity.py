"""The Rust and Python render the same bug report from the same record.

``build_report`` gathers - it probes the machine, runs the pre-flight checks,
reads the newest Wine/Proton log and runs a read-only selftest - and stays in
Python. What is pinned here is what comes out the other end: the markdown a
person pastes into a thread, and the two pre-filled issue links.

Those links deserve the attention they get below. They are the entire "upload"
mechanism for this project - no server, no account, no telemetry - so a report
reaches anybody only because the user clicks a URL with the body already inside
it. The encoding of that URL is the transport, and it is the thing two
implementations could most easily disagree about without anyone noticing until
a report arrived mangled. Python's ``urlencode`` defaults to ``quote_plus``,
whose unreserved set is ``A-Za-z0-9`` plus ``-._~`` and nothing else, whose
space is ``+`` rather than ``%20``, and whose hex digits are upper case. Every
popular Rust crate differs from that somewhere, so the port writes the rule out
and this corpus checks it against the real ``urllib``.

The corpus also carries the shape that produced a real defect. ``mesa_gl`` and
``ram_gb`` are set to ``None`` by ``build_report`` on any machine without
``glxinfo`` or without ``psutil`` - the key is present, so ``.get(k, '?')``
never substituted - and a bug report from such a machine read ``driver None``
and ``RAM None GB``. Those are the machines whose reports matter most.
"""

from __future__ import annotations

import json
import os
import subprocess
import unittest
import urllib.parse
from pathlib import Path

from tests._support import _SRC, typed  # noqa: F401

from goblinmode import report

_REPO = Path(__file__).resolve().parent.parent
REPO_SLUG = "Bvaughan7/goblin-mode-pro"


def _binary() -> Path | None:
    override = os.environ.get("GMP_REPORT_BIN")
    if override:
        return Path(override) if Path(override).exists() else None
    for profile in ("debug", "release"):
        candidate = _REPO / "target" / profile / "examples" / "report"
        if candidate.exists():
            return candidate
    return None


SYSTEM = {
    "cpu": "Intel Core i7-10750H", "gpu": "NVIDIA RTX 2060",
    "nvidia_driver": "550.90.07", "mesa_gl": "4.6 (Compatibility Profile)",
    "kernel": "6.9.3-cachyos", "distro": "CachyOS", "desktop": "KDE",
    "session_type": "wayland", "ram_gb": 15.5, "gmp_version": "1.5.0",
}


def rep(**over) -> dict:
    base = {
        "schema": "gmp.report.v1",
        "generated": "2026-09-03T14:22:07.123456+00:00",
        "system": dict(SYSTEM),
        "game": "", "user_note": "",
        "preflight_summary": {"ok": 11, "warn": 2, "fail": 1},
        "preflight_flags": [],
        "log_file": "", "log_findings": [], "incident": None,
        "active_tweaks": {}, "capability_selftest": None,
    }
    base.update(over)
    return base


FLAGS = [
    {"status": "warn", "title": "CPU temperature", "value": "99°C",
     "detail": "at TjMax under load", "why": "thermal throttling costs frames"},
    {"status": "fail", "title": "helper", "value": "absent", "detail": "",
     "why": "privileged tweaks need it"},
]

FINDINGS = [
    {"label": "missing d3d11", "count": 3, "category": "dxvk",
     "cause": "the DLL was not overridden", "fix": "set WINEDLLOVERRIDES",
     "sample": "err:module:import_dll Library d3d11.dll not found"},
]

INCIDENT = {
    "kind": "gpu_bottleneck", "detail": "frame time doubled for 4s",
    "gpu_state": {"vram_used_mb": 5900, "vram_total_mb": 6144, "pcie_gen": 3,
                  "pcie_width": 16, "pstate": "P2", "clock_gfx_mhz": 1200,
                  "clock_gfx_max_mhz": 1900},
}

SELFTEST = {
    "summary": {"PASS": 9, "SKIP": 3, "FAIL": 1},
    "results": [
        {"status": "PASS", "title": "governor", "detail": "set to performance"},
        {"status": "FAIL", "title": "fan control", "detail": "no writable hwmon"},
        {"status": "SKIP", "title": "undervolt", "detail": "not an Intel CPU"},
    ],
}

TWEAKS = {"governor": True, "epp_boosted": True, "tearing": False,
          "adaptive_sync": False, "power_limited": True, "focus_mode": True,
          "scx_scheduler": "rusty", "reniced": {"4242": -5, "1717": -5}}

CASES = {
    # -- a real report, and its empty counterpart --------------------------
    "everything": rep(game="World of Warcraft", user_note="stutters in Valdrakken",
                      preflight_flags=FLAGS, log_file="steam-1234.log",
                      log_findings=FINDINGS, incident=INCIDENT,
                      active_tweaks=TWEAKS, capability_selftest=SELFTEST),
    "bare": rep(),
    "empty_record": {},
    # -- the defect this port found ----------------------------------------
    # `build_report` sets both of these to None on a machine without glxinfo
    # or without psutil. The key exists, so the `.get(k, '?')` default never
    # applied and the report read "driver None" / "RAM None GB".
    "no_glxinfo_and_no_psutil": rep(system={**SYSTEM, "nvidia_driver": None,
                                            "mesa_gl": None, "ram_gb": None}),
    # An AMD or Intel machine that DOES have glxinfo: the NVIDIA key is null
    # and the Mesa version is real, which is the case the fallback exists for.
    "null_nvidia_driver_with_mesa": rep(system={**SYSTEM, "nvidia_driver": None}),
    "no_nvidia_driver_key": rep(system={k: v for k, v in SYSTEM.items()
                                        if k != "nvidia_driver"}),
    "no_nvidia_and_mesa_absent": rep(system={k: v for k, v in SYSTEM.items()
                                             if k not in ("nvidia_driver", "mesa_gl")}),
    "system_is_empty": rep(system={}),
    "system_is_missing": rep(system=None),
    "system_is_a_list": rep(system=[1]),
    "ram_is_an_integer": rep(system={**SYSTEM, "ram_gb": 16}),
    "ram_is_a_string": rep(system={**SYSTEM, "ram_gb": "16"}),
    # -- the header --------------------------------------------------------
    "generated_missing": rep(generated=None),
    "generated_short": rep(generated="2026"),
    "generated_exactly_19": rep(generated="2026-09-03T14:22:07"),
    "note_present": rep(user_note="it only happens in raids"),
    "note_is_a_number": rep(user_note=5),
    "note_with_a_newline": rep(user_note="line one\nline two"),
    "game_present": rep(game="World of Warcraft"),
    "game_is_a_list": rep(game=["a", "b"]),
    # -- pre-flight ---------------------------------------------------------
    "preflight_all_clear": rep(preflight_flags=[]),
    "preflight_summary_missing": rep(preflight_summary={}),
    "preflight_summary_is_a_list": rep(preflight_summary=[1]),
    "preflight_summary_null_count": rep(preflight_summary={"ok": None}),
    "preflight_flag_no_detail": rep(preflight_flags=[
        {"status": "warn", "title": "t", "value": "v", "detail": "", "why": "w"}]),
    "preflight_flag_no_why_either": rep(preflight_flags=[
        {"status": "warn", "title": "t", "value": "v", "detail": "", "why": ""}]),
    "preflight_flag_missing_keys": rep(preflight_flags=[{}]),
    "preflight_flag_is_a_string": rep(preflight_flags=["nope"]),
    "preflight_flag_status_is_a_number": rep(preflight_flags=[
        {"status": 5, "title": "t", "value": "v", "detail": "d"}]),
    "preflight_flags_is_a_string": rep(preflight_flags="nope"),
    # -- the log section -----------------------------------------------------
    "log_present_no_findings": rep(log_file="steam-1234.log"),
    "log_absent_with_findings": rep(log_findings=FINDINGS),
    "finding_missing_keys": rep(log_file="x.log", log_findings=[{}]),
    "finding_with_backticks": rep(log_file="x.log", log_findings=[
        {"label": "l", "count": 1, "category": "c", "cause": "z", "fix": "f",
         "sample": "a`b`c"}]),
    "finding_sample_at_200": rep(log_file="x.log", log_findings=[
        {"label": "l", "count": 1, "category": "c", "cause": "z", "fix": "f",
         "sample": "x" * 200}]),
    "finding_sample_past_200": rep(log_file="x.log", log_findings=[
        {"label": "l", "count": 1, "category": "c", "cause": "z", "fix": "f",
         "sample": "x" * 201}]),
    # 200 CHARACTERS, not bytes - a log line is as likely to be UTF-8 as not.
    "finding_sample_multibyte": rep(log_file="x.log", log_findings=[
        {"label": "l", "count": 1, "category": "c", "cause": "z", "fix": "f",
         "sample": "ゲ" * 300}]),
    "finding_sample_is_a_number": rep(log_file="x.log", log_findings=[
        {"label": "l", "count": 1, "category": "c", "cause": "z", "fix": "f",
         "sample": 12345}]),
    "finding_is_a_string": rep(log_file="x.log", log_findings=["nope"]),
    "log_file_is_a_number": rep(log_file=5),
    # -- the incident section ------------------------------------------------
    "incident_present": rep(incident=INCIDENT),
    "incident_without_gpu_state": rep(incident={"kind": "cpu_throttle",
                                                "detail": "pinned at 99C"}),
    "incident_empty_gpu_state": rep(incident={"kind": "k", "detail": "d",
                                              "gpu_state": {}}),
    "incident_gpu_state_partial": rep(incident={"kind": "k", "detail": "d",
                                                "gpu_state": {"pstate": "P2"}}),
    "incident_gpu_state_is_a_list": rep(incident={"kind": "k", "detail": "d",
                                                  "gpu_state": [1]}),
    "incident_missing_keys": rep(incident={}),
    "incident_is_a_string": rep(incident="nope"),
    # -- the capabilities section ---------------------------------------------
    "selftest_present": rep(capability_selftest=SELFTEST),
    "selftest_all_pass": rep(capability_selftest={
        "summary": {"PASS": 12},
        "results": [{"status": "PASS", "title": "t", "detail": "d"}]}),
    "selftest_no_results": rep(capability_selftest={"summary": {"PASS": 1},
                                                    "results": []}),
    "selftest_error": rep(capability_selftest={"error": "OSError: nope"}),
    "selftest_summary_missing": rep(capability_selftest={
        "results": [{"status": "FAIL", "title": "t", "detail": "d"}]}),
    "selftest_summary_is_a_list": rep(capability_selftest={
        "summary": [1], "results": [{"status": "FAIL", "title": "t", "detail": "d"}]}),
    "selftest_result_missing_keys": rep(capability_selftest={
        "summary": {"FAIL": 1}, "results": [{"status": "FAIL"}]}),
    "selftest_result_is_a_string": rep(capability_selftest={
        "summary": {"FAIL": 1}, "results": ["nope"]}),
    "selftest_is_a_string": rep(capability_selftest="nope"),
    # -- the tweaks section -----------------------------------------------------
    "tweaks_present": rep(active_tweaks=TWEAKS),
    "tweaks_all_off": rep(active_tweaks={k: False for k in report.TWEAK_KEYS}),
    "tweaks_all_on": rep(active_tweaks={k: True for k in report.TWEAK_KEYS}),
    "tweaks_out_of_order": rep(active_tweaks={"focus_mode": True, "governor": True}),
    "tweaks_unknown_key": rep(active_tweaks={"warp_drive": True}),
    "tweaks_scx_only": rep(active_tweaks={"scx_scheduler": "lavd"}),
    "tweaks_reniced_only": rep(active_tweaks={"reniced": {"1": -5}}),
    # Insertion order, not sorted order - the pids come out in the order they
    # were reniced, which is what a Python dict preserves.
    "tweaks_reniced_out_of_order": rep(active_tweaks={"reniced": {"99": -5, "11": -5}}),
    "tweaks_reniced_is_a_list": rep(active_tweaks={"reniced": [1], "governor": True}),
    "tweaks_reniced_empty": rep(active_tweaks={"reniced": {}, "governor": True}),
    "tweaks_is_a_list": rep(active_tweaks=[1]),
    # -- text that has to survive the URL encoding --------------------------------
    "game_with_a_space": rep(game="World of Warcraft"),
    "game_with_an_ampersand": rep(game="Command & Conquer"),
    "game_with_unicode": rep(game="ゲーム"),
    "game_with_the_unreserved_set": rep(game="a-b._c~d"),
    "game_with_a_percent": rep(game="100% CPU"),
    "game_with_a_plus": rep(game="C++ game"),
    "note_with_every_ascii_punctuation": rep(
        user_note="".join(chr(c) for c in range(32, 127))),
    # -- the 6000-character truncation --------------------------------------------
    "body_just_under_the_cap": rep(user_note="u" * 5000),
    "body_over_the_cap": rep(user_note="u" * 9000),
    "body_over_the_cap_multibyte": rep(user_note="ゲ" * 9000),
    # The cap is 6000 CHARACTERS. This body is comfortably under that and
    # comfortably over 6000 bytes, so it is the case that tells the two
    # measures apart - and it is what a report in any non-Latin script looks
    # like.
    "body_under_the_char_cap_over_the_byte_cap": rep(user_note="ゲ" * 3000),
}

# Exactly 6000 characters, so that `>` and `>=` are different answers. Solved
# for rather than guessed at: every other line contributes to the count, and a
# note changes the overhead by adding its own wrapper line.
def _body_of_exactly(target: int) -> dict:
    padding = target
    for _ in range(8):
        candidate = rep(user_note="u" * padding)
        length = len(report.as_markdown(candidate))
        if length == target:
            return candidate
        padding += target - length
    raise AssertionError(f"could not build a body of exactly {target} characters")


CASES["body_exactly_at_the_cap"] = _body_of_exactly(6000)
CASES["body_one_under_the_cap"] = _body_of_exactly(5999)
CASES["body_one_over_the_cap"] = _body_of_exactly(6001)

WORKS_FOR_ME = {
    "plain": ({"schema": "gmp.worksforme.v1", "game": "World of Warcraft",
               "system": dict(SYSTEM), "note": "", "steam_app_id": "",
               "profile": {}}, "{}"),
    "with_a_note_and_appid": ({"game": "WoW", "system": dict(SYSTEM),
                               "note": "runs great on GE-Proton9", "steam_app_id": "1091500",
                               "profile": {}}, '{\n  "renice_enabled": true\n}'),
    "no_game": ({"system": dict(SYSTEM)}, "{}"),
    "game_is_null": ({"game": None, "system": dict(SYSTEM)}, "{}"),
    "no_system": ({"game": "g"}, "{}"),
    "system_is_a_list": ({"game": "g", "system": [1]}, "{}"),
    "null_system_fields": ({"game": "g", "system": {**SYSTEM, "mesa_gl": None,
                                                    "ram_gb": None}}, "{}"),
    "unicode_game": ({"game": "ゲーム", "system": dict(SYSTEM)}, "{}"),
    "note_is_a_number": ({"game": "g", "system": {}, "note": 5}, "{}"),
    "appid_is_a_number": ({"game": "g", "system": {}, "steam_app_id": 1091500}, "{}"),
    "empty": ({}, "{}"),
}


class BothImplementationsAgree(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.binary = _binary()
        if self.binary is None:
            if os.environ.get("GMP_REQUIRE_RUST_HELPER") == "1":
                self.fail("GMP_REQUIRE_RUST_HELPER=1 but the report example is not "
                          "built - run `cargo build -p gmp-core --example report`")
            self.skipTest("build it with `cargo build -p gmp-core --example report`")

    def _rust(self, record: dict, profile_json: str = "{}") -> dict:
        payload = {"rep": record, "profile_json": profile_json, "repo": REPO_SLUG}
        r = subprocess.run([str(self.binary)], input=json.dumps(payload),
                           capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    @staticmethod
    def _python(record: dict, profile_json: str = "{}") -> dict:
        # The Python builds the profile block itself; the Rust is handed it,
        # because matching CPython's JSON writer is a separate problem.
        wfm = dict(record)
        wfm["profile"] = json.loads(profile_json)
        return {
            "markdown": report.as_markdown(record),
            "works_for_me": report.works_for_me_markdown(wfm),
            "works_for_me_url": report.works_for_me_issue_url(wfm, REPO_SLUG),
            "issue_url": report.github_issue_url(record, REPO_SLUG),
        }

    def test_every_record_renders_the_same_way(self):
        for label, record in CASES.items():
            with self.subTest(label):
                self.assertEqual(typed(self._rust(record)),
                                 typed(self._python(record)))

    def test_every_works_for_me_note_renders_the_same_way(self):
        for label, (record, profile_json) in WORKS_FOR_ME.items():
            with self.subTest(label):
                got = self._rust(record, profile_json)
                want = self._python(record, profile_json)
                self.assertEqual(got["works_for_me"], want["works_for_me"])
                self.assertEqual(got["works_for_me_url"], want["works_for_me_url"])

    def test_the_url_encoding_matches_urllib_byte_for_byte(self):
        """The links are the transport, so this is checked against urllib itself."""
        alphabet = "".join(chr(c) for c in range(32, 127)) + "ゲーム£€\t\n\r"
        for chunk in [alphabet] + [c for c in alphabet]:
            with self.subTest(repr(chunk)):
                record = rep(game=chunk)
                url = self._rust(record)["issue_url"]
                want = urllib.parse.quote_plus(f"[{chunk}] ")
                self.assertIn(f"title={want}&", url)

    def test_the_corpus_reaches_every_section_and_every_branch(self):
        every = "\n".join(self._python(r)["markdown"] for r in CASES.values())
        for fragment in ("### System", "### Pre-flight", "### Wine/Proton log",
                         "### Last incident", "### Capabilities", "### Active tweaks",
                         "- all clear", "no known failure patterns matched",
                         "goblin-run %command%",
                         "every privileged path this machine has is reachable",
                         "reniced: none", "- **Game** ", "\n> ", "  - fix: ",
                         "MB VRAM", "driver ?", "**RAM** ? GB"):
            self.assertIn(fragment, every, f"nothing produces {fragment!r}")
        for key in report.TWEAK_KEYS:
            self.assertIn(key, every)
        # And the word this port removed must not come back.
        self.assertNotIn("driver None", every)
        self.assertNotIn("RAM None GB", every)


class NoRecordShapeCanStopAReportBeingWritten(unittest.TestCase):
    """A report that fails to render is worse than one missing a section.

    Building the report already swallows a failing probe for exactly this
    reason - see the comment on `capability_selftest` in `build_report`. The
    renderer should hold the same line.
    """

    def test_no_corpus_record_raises(self):
        for label, record in CASES.items():
            with self.subTest(label):
                report.as_markdown(record)
                report.github_issue_url(record)

    def test_nor_do_shapes_the_corpus_does_not_bother_pinning(self):
        for record in ({"system": {"cpu": {"nested": 1}}},
                       {"preflight_flags": [{"status": ["warn"], "title": {"a": 1}}]},
                       {"log_findings": [{"sample": {"a": 1}, "count": [1]}],
                        "log_file": "x"},
                       {"incident": {"gpu_state": {"pstate": [1]}}},
                       {"capability_selftest": {"summary": {"PASS": [1]},
                                                "results": [{"status": "FAIL",
                                                             "title": [1]}]}},
                       {"active_tweaks": {"scx_scheduler": {"a": 1}}},
                       {"generated": 5}):
            with self.subTest(str(record)[:60]):
                report.as_markdown(record)
                report.github_issue_url(record)


if __name__ == "__main__":
    unittest.main()
