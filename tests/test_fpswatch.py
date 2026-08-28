import os
import time

from goblinmode import fpswatch
from goblinmode.fpswatch import FpsWatcher

_HEADER = (
    "os,cpu,gpu,ram,kernel,driver,cpuscheduler\n"
    "CachyOS,i7-10750H,RTX 2060,32GB,7.2.0,nvidia 610,none\n"
    "\n"
    "fps,frametime,cpu_load,gpu_load,cpu_temp,gpu_temp,gpu_core_clock,"
    "gpu_mem_clock,gpu_vram_used,gpu_power,ram_used,elapsed\n"
)

_INTERVAL_NS = 200_000_000  # 200 ms


def _csv(fps_seq):
    rows = []
    for i, fps in enumerate(fps_seq):
        rows.append(
            f"{fps:.1f},{1000.0 / fps:.2f},35,90,70,80,1800,7000,3400,95,12000,{i * _INTERVAL_NS}\n"
        )
    return _HEADER + "".join(rows)


def _watcher(tmp_path, monkeypatch, fps_seq):
    d = tmp_path / "mangohud"
    d.mkdir()
    (d / "WoW_run.csv").write_text(_csv(fps_seq))
    monkeypatch.setattr(fpswatch, "MANGOHUD_LOG_DIR", d)
    return FpsWatcher(dip_floor=22, dip_ratio=0.5), d / "WoW_run.csv"


def test_no_dip_at_steady_fps(tmp_path, monkeypatch):
    w, _ = _watcher(tmp_path, monkeypatch, [120] * 80)
    assert w.poll() is None
    assert w.stats()["fps_avg"] == 120.0
    assert w.stats()["in_dip"] is False


def test_detects_extreme_dip_then_recovery(tmp_path, monkeypatch):
    w, path = _watcher(tmp_path, monkeypatch, [120] * 60 + [12] * 20)
    ev = w.poll()
    assert ev is not None and ev.kind == "dip"
    assert ev.fps < 20 and ev.baseline > 100
    assert w.stats()["in_dip"] is True

    start = 80
    with open(path, "a") as fh:
        fh.write("".join(
            f"115.0,8.70,35,90,70,80,1800,7000,3400,95,12000,{(start + i) * _INTERVAL_NS}\n"
            for i in range(15)
        ))
    ev2 = w.poll()
    assert ev2 is not None and ev2.kind == "recovered"
    assert ev2.duration_s > 0


def test_floor_triggers_without_high_baseline(tmp_path, monkeypatch):
    w, _ = _watcher(tmp_path, monkeypatch, [30] * 40 + [14] * 15)
    ev = w.poll()
    assert ev is not None and ev.kind == "dip"


def test_debounce_one_incident_per_episode(tmp_path, monkeypatch):
    w, _ = _watcher(tmp_path, monkeypatch, [120] * 60 + [11] * 30)
    assert w.poll().kind == "dip"
    # polling again with no new rows -> still in dip, no new event
    assert w.poll() is None


def test_rotates_to_newest_csv(tmp_path, monkeypatch):
    w, _ = _watcher(tmp_path, monkeypatch, [90] * 30)
    w.poll()
    d = tmp_path / "mangohud"
    newer = d / "WoW_run2.csv"
    newer.write_text(_csv([60] * 30))
    os.utime(newer, (time.time() + 10, time.time() + 10))
    w.poll()
    assert w._path == newer
