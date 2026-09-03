"""The incident ring, its on-disk log, and the payload that leaves the machine.

`build_llm_payload` is the one function here whose output a user is invited to
paste into somebody else's LLM, so the tests that matter most are the ones
about what it does NOT contain.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tests._support import _SRC  # noqa: F401

from goblinmode import incidents


def _incident(**kw) -> incidents.Incident:
    base = {"kind": "thermal_throttle", "detail": "package hit 97C"}
    base.update(kw)
    return incidents.Incident(**base)


class IncidentShape(unittest.TestCase):
    def test_optional_sections_are_omitted_when_empty(self):
        """A payload is read by a human and by a model; empty keys are noise
        in both directions."""
        d = _incident().as_dict()
        self.assertNotIn("gpu_state", d)
        self.assertNotIn("fps_trace", d)
        self.assertEqual(d["kind"], "thermal_throttle")

    def test_optional_sections_appear_when_populated(self):
        d = _incident(gpu_state={"util": 99}, fps_trace=[{"fps": 30}]).as_dict()
        self.assertEqual(d["gpu_state"], {"util": 99})
        self.assertEqual(d["fps_trace"], [{"fps": 30}])

    def test_every_incident_is_json_serialisable(self):
        """It is appended to a JSONL file; anything unserialisable loses the
        whole line, and the log is append-only so it is lost for good."""
        json.dumps(_incident(game="Wow.exe", game_pid=42).as_dict())


class Ring(unittest.TestCase):
    def test_the_ring_is_bounded_and_keeps_the_newest(self):
        with TemporaryDirectory() as tmp, \
                patch.object(incidents, "INCIDENT_FILE", Path(tmp) / "i.jsonl"), \
                patch.object(incidents, "ensure_user_dirs", lambda: None):
            log = incidents.IncidentLog(maxlen=3)
            for i in range(5):
                log.add(_incident(detail=f"n{i}"))
            self.assertEqual([i.detail for i in log.all()], ["n2", "n3", "n4"])
            self.assertEqual(log.latest().detail, "n4")

    def test_latest_is_none_before_anything_happens(self):
        self.assertIsNone(incidents.IncidentLog().latest())

    def test_an_unwritable_log_does_not_lose_the_in_memory_incident(self):
        """Persistence is best effort. A read-only home must not cost the user
        the incident the GUI is about to show them."""
        with TemporaryDirectory() as tmp:
            unwritable = Path(tmp) / "nope" / "i.jsonl"   # parent does not exist
            with patch.object(incidents, "INCIDENT_FILE", unwritable), \
                    patch.object(incidents, "ensure_user_dirs", lambda: None):
                log = incidents.IncidentLog()
                log.add(_incident())
            self.assertEqual(len(log.all()), 1)


class OnDiskLog(unittest.TestCase):
    def test_history_survives_a_corrupt_line(self):
        """The file is appended to by a long-running daemon; a truncated write
        must cost that one line, not the whole history."""
        with TemporaryDirectory() as tmp:
            f = Path(tmp) / "i.jsonl"
            f.write_text('{"kind": "a"}\n{not json\n{"kind": "b"}\n')
            with patch.object(incidents, "INCIDENT_FILE", f):
                got = incidents.IncidentLog().load_history()
            self.assertEqual([r["kind"] for r in got], ["a", "b"])

    def test_history_is_empty_when_there_is_no_file(self):
        with TemporaryDirectory() as tmp, \
                patch.object(incidents, "INCIDENT_FILE", Path(tmp) / "absent"):
            self.assertEqual(incidents.IncidentLog().load_history(), [])

    def test_history_returns_the_newest_entries(self):
        with TemporaryDirectory() as tmp:
            f = Path(tmp) / "i.jsonl"
            f.write_text("".join(f'{{"kind": "k{i}"}}\n' for i in range(10)))
            with patch.object(incidents, "INCIDENT_FILE", f):
                got = incidents.IncidentLog().load_history(limit=3)
            self.assertEqual([r["kind"] for r in got], ["k7", "k8", "k9"])

    def test_an_oversized_log_is_trimmed_to_the_newest_entries(self):
        with TemporaryDirectory() as tmp:
            f = Path(tmp) / "i.jsonl"
            f.write_text("".join(f'{{"n": {i}}}\n' for i in range(1000)))
            with patch.object(incidents, "INCIDENT_FILE", f), \
                    patch.object(incidents, "ensure_user_dirs", lambda: None), \
                    patch.object(incidents.IncidentLog, "MAX_BYTES", 100), \
                    patch.object(incidents.IncidentLog, "MAX_KEEP", 10):
                incidents.IncidentLog().add(_incident())
            lines = f.read_text().splitlines()
            # the newest 10 that were there, plus the one just written
            self.assertEqual(len(lines), 11)
            self.assertIn('"n": 999', lines[-2])


class Thinning(unittest.TestCase):
    def test_short_input_is_returned_untouched(self):
        rows = [{"i": i} for i in range(5)]
        self.assertEqual(incidents._thin(rows, target=20), rows)

    def test_the_last_row_always_survives(self):
        """The incident happened at the END of the window. Dropping the last
        sample would remove the moment being diagnosed."""
        rows = [{"i": i} for i in range(1000)]
        thinned = incidents._thin(rows, target=20)
        self.assertEqual(len(thinned), 20)
        self.assertEqual(thinned[-1], rows[-1])
        self.assertEqual(thinned[0], rows[0])

    def test_thinning_preserves_order(self):
        rows = [{"i": i} for i in range(97)]
        thinned = incidents._thin(rows, target=10)
        self.assertEqual(thinned, sorted(thinned, key=lambda r: r["i"]))


class LlmPayload(unittest.TestCase):
    """What leaves the machine when a user exports an incident."""

    def _payload(self, incident) -> dict:
        with patch.object(incidents, "_system_info", lambda: {"distro": "test"}):
            text = incidents.build_llm_payload(incident)
        body = text.split("```json\n", 1)[1].rsplit("\n```", 1)[0]
        return json.loads(body)

    def test_the_home_path_is_redacted_from_the_detail(self):
        payload = self._payload(_incident(detail="crash in /home/alice/games/x"))
        self.assertNotIn("alice", json.dumps(payload))
        self.assertIn("<user>", payload["trigger"]["detail"])

    def test_the_home_path_is_redacted_from_the_log_tail(self):
        """The tail is raw Proton output - the likeliest place a username
        appears, and the easiest place to forget to redact."""
        payload = self._payload(
            _incident(logs_tail=["err: /home/alice/.steam/x.dll not found"]))
        self.assertNotIn("alice", json.dumps(payload))

    def test_only_the_last_twenty_log_lines_are_included(self):
        payload = self._payload(_incident(logs_tail=[f"line {i}" for i in range(50)]))
        self.assertEqual(len(payload["logs_tail"]), 20)
        self.assertIn("line 49", payload["logs_tail"][-1])

    def test_the_metric_window_is_thinned(self):
        payload = self._payload(
            _incident(metrics_window=[{"i": i} for i in range(500)]))
        self.assertEqual(len(payload["metrics_window"]), 20)

    def test_the_payload_is_a_fenced_json_block_after_the_prompt(self):
        with patch.object(incidents, "_system_info", lambda: {}):
            text = incidents.build_llm_payload(_incident())
        self.assertTrue(text.startswith(incidents.SYSTEM_PROMPT))
        self.assertIn("```json", text)
        self.assertTrue(text.rstrip().endswith("```"))

    def test_the_schema_is_declared(self):
        self.assertEqual(self._payload(_incident())["schema"], incidents.SCHEMA)

    def test_a_user_note_is_included_only_when_given(self):
        with patch.object(incidents, "_system_info", lambda: {}):
            without = incidents.build_llm_payload(_incident())
            with_note = incidents.build_llm_payload(_incident(), model_hint="stutters")
        self.assertNotIn("user_note", without)
        self.assertIn("stutters", with_note)


if __name__ == "__main__":
    unittest.main()
