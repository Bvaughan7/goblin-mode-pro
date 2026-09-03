"""The Rust and Python incident payloads are the same text.

Compared as a STRING, not as parsed JSON. This payload is what a user copies
into somebody else's model, so key order, indentation and the code fence are
all part of the answer — two implementations that agree on the data but not on
the bytes do not agree.

Both sides are given the same machine description and the same identity to
redact against, because neither is a property of the incident.
"""

from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from tests._support import _SRC  # noqa: F401

from goblinmode import incidents, logrules

_REPO = Path(__file__).resolve().parent.parent
_HOME, _USER = "/home/alice", "alice"
_SYSTEM = {"distro": "TestOS", "kernel": "7.2.2", "cpu": "i7-10750H"}


def _binary() -> Path | None:
    override = os.environ.get("GMP_PAYLOAD_BIN")
    if override:
        return Path(override) if Path(override).exists() else None
    for profile in ("debug", "release"):
        candidate = _REPO / "target" / profile / "examples" / "payload"
        if candidate.exists():
            return candidate
    return None


class BothImplementationsBuildTheSamePayload(unittest.TestCase):
    def setUp(self):
        self.binary = _binary()
        if self.binary is None:
            if os.environ.get("GMP_REQUIRE_RUST_HELPER") == "1":
                self.fail("GMP_REQUIRE_RUST_HELPER=1 but the payload example is not "
                          "built - run `cargo build -p gmp-core --example payload`")
            self.skipTest("build it with `cargo build -p gmp-core --example payload`")

    def _both(self, incident: incidents.Incident, hint: str = "") -> tuple[str, str]:
        with patch.object(incidents, "_system_info", lambda: _SYSTEM), \
                patch.object(logrules, "_HOME", _HOME), \
                patch.object(logrules, "_USER", _USER):
            py = incidents.build_llm_payload(incident, model_hint=hint)
        payload_in = json.dumps({
            "incident": incident.as_dict() | {"ts": incident.ts},
            "system": _SYSTEM, "hint": hint, "home": _HOME, "user": _USER,
        })
        proc = subprocess.run([str(self.binary)], input=payload_in,
                              capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return py, proc.stdout

    def _assert_same(self, incident, hint=""):
        py, rs = self._both(incident, hint)
        self.assertEqual(py, rs)
        return py

    def test_a_plain_incident(self):
        text = self._assert_same(incidents.Incident(
            kind="thermal_throttle", detail="package hit 97C", ts="2026-09-03T00:00:00+00:00"))
        self.assertTrue(text.startswith(incidents.SYSTEM_PROMPT))

    def test_the_system_prompt_is_identical(self):
        """It is the instruction an external model actually receives; a
        paraphrase would change the answers users get back."""
        rs = (_REPO / "crates/gmp-core/src/incidents.rs").read_text()
        import re
        m = re.search(r'pub const SYSTEM_PROMPT: &str = r(#*)"(.*?)"\1;', rs, re.S)
        self.assertIsNotNone(m, "could not find SYSTEM_PROMPT in the Rust source")
        self.assertEqual(m.group(2), incidents.SYSTEM_PROMPT)

    def test_an_incident_with_gpu_state_and_a_frame_trace(self):
        self._assert_same(incidents.Incident(
            kind="gpu_fault", detail="device lost", ts="T",
            game="Wow.exe", game_pid=1234,
            gpu_state={"util": 99, "vram_mb": 7800},
            fps_trace=[{"t": i, "fps": 60 - i} for i in range(50)],
            active_tweaks={"governor": "performance"},
        ))

    def test_redaction_of_the_detail_and_the_log_tail(self):
        text = self._assert_same(incidents.Incident(
            kind="crash", detail=f"crash in {_HOME}/games/x", ts="T",
            logs_tail=[f"err:module {_HOME}/.steam/y.dll not found",
                       "/home/bob/other/prefix"],
        ))
        self.assertNotIn("alice", text)
        self.assertNotIn("bob", text)

    def test_a_long_metric_window_is_thinned_the_same_way(self):
        """Same 20 samples, same positions - the thinning arithmetic has to
        agree, not just the count."""
        self._assert_same(incidents.Incident(
            kind="thermal_throttle", detail="d", ts="T",
            metrics_window=[{"i": i, "temp": 80 + (i % 20)} for i in range(997)],
        ))

    def test_a_long_log_tail_keeps_the_same_twenty_lines(self):
        self._assert_same(incidents.Incident(
            kind="crash", detail="d", ts="T",
            logs_tail=[f"line {i}" for i in range(200)],
        ))

    def test_a_user_note(self):
        self._assert_same(incidents.Incident(kind="k", detail="d", ts="T"),
                          hint="stutters every 30s in Valdrakken")

    def test_an_empty_incident(self):
        self._assert_same(incidents.Incident(kind="", detail="", ts=""))


if __name__ == "__main__":
    unittest.main()
