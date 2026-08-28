import time

from goblinmode.diagnostics import DiagnosticEngine, Sample


def _sample(**kw):
    base = dict(
        t=time.monotonic(),
        cpu_temp=100.0,
        cpu_load=40.0,
        per_core=[40.0] * 12,
        pkg_power_w=None,
        pl1_w=107.0,
        pl2_w=107.0,
        gpu_load=99.0,
        gpu_temp=85.0,
        gpu_throttle_reasons="0x0000000000000000",
        cpu_throttled=False,
    )
    base.update(kw)
    return Sample(**base)


def _engine():
    eng = DiagnosticEngine.__new__(DiagnosticEngine)
    eng._incident_seen = {}
    eng.REMIND_SECONDS = 180
    return eng


def test_thermal_throttle_fires_once_per_episode():
    eng = _engine()
    hot = _sample(cpu_throttled=True)
    # onset -> fires
    assert eng.assess(hot) == ("thermal_throttle", "CPU package thermal throttling (100°C)")
    # still throttling next second -> suppressed
    assert eng.assess(_sample(cpu_throttled=True)) is None
    assert eng.assess(_sample(cpu_throttled=True)) is None
    # condition clears...
    assert eng.assess(_sample(cpu_throttled=False)) is None
    # ...then recurs -> fresh onset fires again
    assert eng.assess(_sample(cpu_throttled=True))[0] == "thermal_throttle"


def test_gpu_sw_power_cap_is_not_an_incident():
    eng = _engine()
    # 0x4 = SwPowerCap - normal under load, must NOT alert
    assert eng.assess(_sample(gpu_throttle_reasons="0x0000000000000004")) is None


def test_gpu_thermal_slowdown_is_an_incident():
    eng = _engine()
    # 0x20 = SW thermal slowdown
    kind, detail = eng.assess(_sample(gpu_throttle_reasons="0x0000000000000020"))
    assert kind == "gpu_throttle"
    assert "thermal" in detail.lower()


def test_reminder_after_window():
    eng = _engine()
    eng.REMIND_SECONDS = 0  # immediate re-remind
    assert eng.assess(_sample(cpu_throttled=True)) is not None
    assert eng.assess(_sample(cpu_throttled=True)) is not None
