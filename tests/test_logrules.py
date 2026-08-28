from goblinmode import logrules
from goblinmode import logwatch


def test_live_patterns_are_a_subset_of_rules():
    live_ids = {r.id for r in logrules.RULES if r.live}
    assert live_ids  # at least some
    # logwatch consumes them
    assert logwatch._PATTERNS is logrules.LIVE_PATTERNS


def test_analyze_matches_known_failures():
    text = "\n".join([
        "err:module:import_dll Library MSVCP140.dll not found",
        "esync: up to 512 handles",
        "VK_ERROR_DEVICE_LOST",
        "some unrelated line",
        "VK_ERROR_DEVICE_LOST again",
    ])
    found = {f.rule_id: f for f in logrules.analyze_text(text)}
    assert "vcrun" in found
    assert "esync_fd" in found
    assert found["device_lost"].count == 2
    assert found["device_lost"].severity == "error"


def test_analyze_orders_errors_first():
    text = "EasyAntiCheat not supported on Linux\nstd::bad_alloc thrown"
    out = logrules.analyze_text(text)
    assert out[0].severity == "error"  # host_oom before anticheat(warn)


def test_analyze_clean_log_returns_nothing():
    assert logrules.analyze_text("info: everything is fine\nloaded shaders ok") == []
