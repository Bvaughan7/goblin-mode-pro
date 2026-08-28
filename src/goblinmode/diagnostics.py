"""The Diagnostic Engine - real-time metric sampling + throttle detection.

Only runs while the Observer reports an active game. Everything here is read
from sysfs / ``nvidia-smi`` and needs no privileges.

Sources
-------
* CPU package temp  : ``coretemp`` hwmon (resolved by name, index is dynamic)
* Per-core load     : ``psutil.cpu_percent(percpu=True)``
* Package power     : RAPL ``energy_uj`` delta over the sample interval
* PL1 / PL2         : RAPL ``constraint_{0,1}_power_limit_uw``
* GPU               : ``nvidia-smi --query-gpu=...``
* Throttling        : ``cpu*/thermal_throttle/{core,package}_throttle_count``
"""

from __future__ import annotations

import glob
import logging
import shutil
import subprocess
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import psutil

log = logging.getLogger(__name__)

_HWMON = Path("/sys/class/hwmon")
_RAPL = Path("/sys/class/powercap/intel-rapl/intel-rapl:0")
_CPU = Path("/sys/devices/system/cpu")

HISTORY_SECONDS = 300


@dataclass
class Sample:
    t: float
    cpu_temp: float | None
    cpu_load: float               # aggregate %
    per_core: list[float]
    pkg_power_w: float | None
    pl1_w: float | None
    pl2_w: float | None
    gpu_load: float | None
    gpu_temp: float | None
    gpu_throttle_reasons: str
    cpu_throttled: bool           # thermal/package throttle count rose this sample
    disk_read_mbps: float | None = None

    def as_dict(self) -> dict:
        return {
            "t": round(self.t, 2),
            "cpu_temp": self.cpu_temp,
            "cpu_load": round(self.cpu_load, 1),
            "per_core": [round(x, 1) for x in self.per_core],
            "pkg_power_w": round(self.pkg_power_w, 1) if self.pkg_power_w else None,
            "pl1_w": self.pl1_w,
            "pl2_w": self.pl2_w,
            "gpu_load": self.gpu_load,
            "gpu_temp": self.gpu_temp,
            "gpu_throttle_reasons": self.gpu_throttle_reasons,
            "cpu_throttled": self.cpu_throttled,
            "disk_read_mbps": self.disk_read_mbps,
        }


def _resolve_coretemp_input() -> Path | None:
    for name_file in _HWMON.glob("hwmon*/name"):
        try:
            if name_file.read_text().strip() == "coretemp":
                hwmon = name_file.parent
                # Prefer the "Package id 0" label; fall back to temp1_input.
                for label in hwmon.glob("temp*_label"):
                    if "package" in label.read_text().strip().lower():
                        return hwmon / label.name.replace("_label", "_input")
                cand = hwmon / "temp1_input"
                return cand if cand.exists() else None
        except OSError:
            continue
    return None


def _read_int(path: Path) -> int | None:
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def _throttle_count() -> int:
    total = 0
    for p in glob.glob(str(_CPU / "cpu[0-9]*" / "thermal_throttle" / "package_throttle_count")):
        v = _read_int(Path(p))
        if v is not None:
            total += v
    return total


def _nvidia_smi() -> tuple[float | None, float | None, str]:
    if not shutil.which("nvidia-smi"):
        return None, None, ""
    try:
        out = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu,temperature.gpu,clocks_event_reasons.active",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=4,
        ).stdout.strip().splitlines()
    except (OSError, subprocess.SubprocessError):
        return None, None, ""
    if not out:
        return None, None, ""
    parts = [p.strip() for p in out[0].split(",")]
    try:
        load = float(parts[0])
        temp = float(parts[1])
    except (ValueError, IndexError):
        return None, None, ""
    reasons = parts[2] if len(parts) > 2 else ""
    return load, temp, reasons


class DiagnosticEngine:
    def __init__(self, sample_interval: float = 1.0) -> None:
        self.sample_interval = sample_interval
        self.history: deque[Sample] = deque(maxlen=int(HISTORY_SECONDS / max(sample_interval, 0.2)))
        self._coretemp = _resolve_coretemp_input()
        self._last_energy: tuple[float, int] | None = None  # (t, energy_uj)
        self._last_throttle = _throttle_count()
        self._incident_seen: dict[str, float] = {}  # kind -> last emitted (monotonic)
        self._last_disk: tuple[float, int] | None = None  # (t, read_bytes)
        psutil.cpu_percent(percpu=True)  # prime the counter

    # -- one sample ---------------------------------------------------------
    def sample(self) -> Sample:
        now = time.monotonic()

        cpu_temp = None
        if self._coretemp:
            raw = _read_int(self._coretemp)
            cpu_temp = raw / 1000.0 if raw is not None else None

        per_core = psutil.cpu_percent(percpu=True)
        agg = sum(per_core) / len(per_core) if per_core else 0.0

        pkg_power = self._package_power(now)
        pl1 = _read_int(_RAPL / "constraint_0_power_limit_uw")
        pl2 = _read_int(_RAPL / "constraint_1_power_limit_uw")

        gpu_load, gpu_temp, gpu_reasons = _nvidia_smi()

        tc = _throttle_count()
        cpu_throttled = tc > self._last_throttle
        self._last_throttle = tc

        s = Sample(
            t=now,
            disk_read_mbps=self._disk_read(now),
            cpu_temp=round(cpu_temp, 1) if cpu_temp is not None else None,
            cpu_load=agg,
            per_core=list(per_core),
            pkg_power_w=pkg_power,
            pl1_w=round(pl1 / 1_000_000, 1) if pl1 else None,
            pl2_w=round(pl2 / 1_000_000, 1) if pl2 else None,
            gpu_load=gpu_load,
            gpu_temp=gpu_temp,
            gpu_throttle_reasons=gpu_reasons,
            cpu_throttled=cpu_throttled,
        )
        self.history.append(s)
        return s

    def _disk_read(self, now: float) -> float | None:
        try:
            io = psutil.disk_io_counters()
        except (RuntimeError, OSError):
            return None
        if io is None:
            return None
        prev = self._last_disk
        self._last_disk = (now, io.read_bytes)
        if prev is None or now <= prev[0]:
            return None
        return round((io.read_bytes - prev[1]) / (now - prev[0]) / 1_000_000, 1)

    def _package_power(self, now: float) -> float | None:
        raw = _read_int(_RAPL / "energy_uj")
        if raw is None:
            return None
        prev = self._last_energy
        self._last_energy = (now, raw)
        if prev is None:
            return None
        dt = now - prev[0]
        de = raw - prev[1]
        if dt <= 0:
            return None
        if de < 0:  # counter wrapped
            return None
        return round(de / dt / 1_000_000, 1)

    # -- throttle assessment ---------------------------------------------
    # NVML clock event-reason bits worth alerting on. SwPowerCap (0x4) is
    # deliberately excluded - a laptop dGPU is power-capped under any real load,
    # that's normal and not an incident.
    _GPU_BAD_BITS = {
        0x8: "GPU HW slowdown",
        0x20: "GPU SW thermal slowdown",
        0x40: "GPU HW thermal slowdown",
        0x80: "GPU HW power-brake slowdown",
    }
    # Don't re-raise the same kind of incident more than once per this window.
    REMIND_SECONDS = 180

    def _parse_gpu_reasons(self, raw: str) -> int:
        raw = (raw or "").strip()
        try:
            return int(raw, 16) if raw.lower().startswith("0x") else int(raw or "0")
        except ValueError:
            return 0

    def _current_issues(self, s: Sample) -> dict[str, str]:
        issues: dict[str, str] = {}
        if s.cpu_throttled:
            hot = f" ({s.cpu_temp:.0f}°C)" if s.cpu_temp else ""
            issues["thermal_throttle"] = f"CPU package thermal throttling{hot}"
        if (
            s.pkg_power_w is not None
            and s.pl1_w is not None
            and s.cpu_load > 60
            and s.pkg_power_w >= s.pl1_w * 0.98
        ):
            issues["power_limit"] = (
                f"CPU package power pinned at PL1 ({s.pl1_w:.0f} W) under load"
            )
        bits = self._parse_gpu_reasons(s.gpu_throttle_reasons)
        bad = [label for bit, label in self._GPU_BAD_BITS.items() if bits & bit]
        if bad:
            hot = f" ({s.gpu_temp:.0f}°C)" if s.gpu_temp else ""
            issues["gpu_throttle"] = ", ".join(bad) + hot
        return issues

    def assess(self, s: Sample) -> tuple[str, str] | None:
        """Return (kind, detail) once per *episode* of a throttle condition.

        Fires on onset, then at most every ``REMIND_SECONDS`` while it persists;
        a kind that clears is forgotten so its next occurrence is a fresh onset.
        """
        issues = self._current_issues(s)
        now = time.monotonic()

        for kind in list(self._incident_seen):
            if kind not in issues:
                del self._incident_seen[kind]

        for kind, detail in issues.items():
            last = self._incident_seen.get(kind)
            if last is None or (now - last) >= self.REMIND_SECONDS:
                self._incident_seen[kind] = now
                return (kind, detail)
        return None

    def recent(self, seconds: int = 60) -> list[Sample]:
        cutoff = time.monotonic() - seconds
        return [s for s in self.history if s.t >= cutoff]

    def recent(self, seconds: int = 60) -> list[Sample]:
        cutoff = time.monotonic() - seconds
        return [s for s in self.history if s.t >= cutoff]
