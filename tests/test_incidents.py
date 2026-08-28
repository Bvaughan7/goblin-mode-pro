import json

from goblinmode import incidents
from goblinmode.incidents import Incident, IncidentLog, build_llm_payload


def test_build_llm_payload_is_valid_json_block():
    inc = Incident(
        kind="gpu_fault",
        detail="VKD3D: Device lost",
        game="Wow.exe",
        game_pid=4242,
        metrics_window=[{"t": 1.0, "cpu_temp": 92}],
        logs_tail=["err:...", "VKD3D: Device lost"],
        active_tweaks={"governor": "performance"},
    )
    out = build_llm_payload(inc, model_hint="prefers RTX 2060 tips")
    assert incidents.SYSTEM_PROMPT.split(".")[0] in out
    body = out.split("```json", 1)[1].rsplit("```", 1)[0]
    parsed = json.loads(body)
    assert parsed["schema"] == "gmp.incident.v1"
    assert parsed["trigger"]["type"] == "gpu_fault"
    assert parsed["game"]["pid"] == 4242
    assert parsed["user_note"] == "prefers RTX 2060 tips"
    assert parsed["system"]["chassis"] == "Dell G7"


def test_incident_log_persists_and_reloads(tmp_path, monkeypatch):
    f = tmp_path / "incidents.jsonl"
    monkeypatch.setattr(incidents, "INCIDENT_FILE", f)
    monkeypatch.setattr(incidents, "ensure_user_dirs", lambda: None)

    logbook = IncidentLog(maxlen=5)
    logbook.add(Incident(kind="thermal_throttle", detail="hot"))
    logbook.add(Incident(kind="power_limit", detail="pl1"))

    assert logbook.latest().kind == "power_limit"
    history = logbook.load_history()
    assert len(history) == 2
    assert history[0]["kind"] == "thermal_throttle"


def test_ring_buffer_caps_length():
    logbook = IncidentLog(maxlen=3)
    for i in range(10):
        logbook._ring.append(Incident(kind="x", detail=str(i)))
    assert len(logbook.all()) == 3
