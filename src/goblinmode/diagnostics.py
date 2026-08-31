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
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

import psutil

log = logging.getLogger(__name__)

_HWMON = Path("/sys/class/hwmon")
_POWERCAP = Path("/sys/class/powercap")
_CPU = Path("/sys/devices/system/cpu")

HISTORY_SECONDS = 300

#: hwmon "name" values that expose a whole-package CPU temperature, best first.
#: coretemp = Intel; k10temp / zenpower = AMD; others are ARM/embedded.
_CPU_HWMON_NAMES = ("coretemp", "k10temp", "zenpower", "cpu_thermal")


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


def _resolve_cpu_temp_input() -> Path | None:
    """The hwmon file that reports package/CPU temperature - Intel (coretemp),
    AMD (k10temp / zenpower) or a generic cpu_thermal zone. Resolved by name so
    the dynamic hwmon index doesn't matter."""
    found: dict[str, Path] = {}
    for name_file in _HWMON.glob("hwmon*/name"):
        try:
            name = name_file.read_text().strip()
        except OSError:
            continue
        if name in _CPU_HWMON_NAMES and name not in found:
            found[name] = name_file.parent

    for name in _CPU_HWMON_NAMES:               # honour the preference order
        hwmon = found.get(name)
        if hwmon is None:
            continue
        try:
            # Prefer a "package"/"Tctl"/"Tdie" label, else temp1_input.
            for label in sorted(hwmon.glob("temp*_label")):
                lbl = label.read_text().strip().lower()
                if any(k in lbl for k in ("package", "tctl", "tdie", "tccd")):
                    cand = hwmon / label.name.replace("_label", "_input")
                    if cand.exists():
                        return cand
        except OSError:
            pass
        cand = hwmon / "temp1_input"
        if cand.exists():
            return cand
    return None


def _resolve_rapl_zone() -> Path | None:
    """The powercap RAPL zone for the CPU package - ``intel-rapl:0`` on most
    boxes, but resolved by the zone's ``name`` (``package-0``) so it also works
    where enumeration differs or the AMD driver is in use."""
    for zone in sorted(_POWERCAP.glob("*:*")):
        try:
            if zone.name.count(":") == 1 and zone.joinpath("name").read_text().strip().startswith("package-"):
                return zone
        except OSError:
            continue
    legacy = _POWERCAP / "intel-rapl" / "intel-rapl:0"
    return legacy if legacy.exists() else None


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
    """One cheap nvidia-smi query. Blocks on a subprocess - the daemon injects a
    non-blocking cached getter instead (see ``DiagnosticEngine`` / ``GpuMonitor``);
    this direct form is kept for tests and one-off use."""
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
    def __init__(
        self,
        sample_interval: float = 1.0,
        gpu_probe: Callable[[], tuple[float | None, float | None, str]] | None = None,
    ) -> None:
        self.sample_interval = sample_interval
        self._gpu_probe = gpu_probe or _nvidia_smi
        self.history: deque[Sample] = deque(maxlen=int(HISTORY_SECONDS / max(sample_interval, 0.2)))
        self._cpu_temp_input = _resolve_cpu_temp_input()
        self._rapl = _resolve_rapl_zone()
        self._last_energy: tuple[float, int] | None = None  # (t, energy_uj)
        self._last_throttle = _throttle_count()
        self._incident_seen: dict[str, float] = {}  # kind -> last emitted (monotonic)
        self._issue_last_seen: dict[str, float] = {}  # kind -> last observed (monotonic)
        self._throttle_hits: deque[tuple[float, bool]] = deque(maxlen=128)
        self._last_disk: tuple[float, int] | None = None  # (t, read_bytes)
        psutil.cpu_percent(percpu=True)  # prime the counter

    # -- one sample ---------------------------------------------------------
    def sample(self) -> Sample:
        now = time.monotonic()

        cpu_temp = None
        if self._cpu_temp_input:
            raw = _read_int(self._cpu_temp_input)
            cpu_temp = raw / 1000.0 if raw is not None else None

        per_core = psutil.cpu_percent(percpu=True)
        agg = sum(per_core) / len(per_core) if per_core else 0.0

        pkg_power = self._package_power(now)
        pl1 = _read_int(self._rapl / "constraint_0_power_limit_uw") if self._rapl else None
        pl2 = _read_int(self._rapl / "constraint_1_power_limit_uw") if self._rapl else None

        gpu_load, gpu_temp, gpu_reasons = self._gpu_probe()

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
        if self._rapl is None:
            return None
        raw = _read_int(self._rapl / "energy_uj")
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
    _GPU_BAD_BITS: ClassVar[dict[int, str]] = {
        0x8: "GPU HW slowdown",
        0x20: "GPU SW thermal slowdown",
        0x40: "GPU HW thermal slowdown",
        0x80: "GPU HW power-brake slowdown",
    }
    # Don't re-raise the same kind of incident more than once per this window.
    REMIND_SECONDS = 180
    #: Some conditions are chronic on a thermally-marginal laptop. Remind far
    #: less often for those so a warm three-hour raid isn't a stream of popups.
    _REMIND_BY_KIND: ClassVar[dict[str, int]] = {"thermal_throttle": 900}
    #: An episode is only "over" after this long with no recurrence. Without the
    #: grace window a single throttle-free sample resets the episode and the very
    #: next counter tick reads as a brand-new onset -> notification spam.
    EPISODE_GRACE_SECONDS = 90
    #: CPU package thermal throttling only counts as an issue once the throttle
    #: counter has ticked in at least this many samples across the trailing
    #: window. Comet Lake / Tiger Lake laptops nick the counter under any turbo
    #: load; an isolated tick costs no measurable frame rate and isn't worth a
    #: popup (this is exactly the "throttling but performance was fine" case).
    _THROTTLE_WINDOW_SECONDS = 20.0
    _THROTTLE_MIN_HITS = 5

    def _parse_gpu_reasons(self, raw: str) -> int:
        raw = (raw or "").strip()
        try:
            return int(raw, 16) if raw.lower().startswith("0x") else int(raw or "0")
        except ValueError:
            return 0

    def _current_issues(self, s: Sample) -> dict[str, str]:
        issues: dict[str, str] = {}

        self._throttle_hits.append((s.t, s.cpu_throttled))
        cutoff = s.t - self._THROTTLE_WINDOW_SECONDS
        while self._throttle_hits and self._throttle_hits[0][0] < cutoff:
            self._throttle_hits.popleft()
        hits = sum(1 for _, hit in self._throttle_hits if hit)
        if hits >= self._THROTTLE_MIN_HITS:
            hot = f" ({s.cpu_temp:.0f}°C)" if s.cpu_temp else ""
            issues["thermal_throttle"] = (
                f"CPU package thermal throttling{hot} — {hits} throttle events "
                f"in the last {int(self._THROTTLE_WINDOW_SECONDS)}s"
            )
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

        Fires on onset, then at most every ``REMIND_SECONDS`` (per-kind, see
        ``_REMIND_BY_KIND``) while it persists. An episode ends only after
        ``EPISODE_GRACE_SECONDS`` with no recurrence, so a momentary gap in a
        chronic condition doesn't read as a fresh onset and re-notify.
        """
        issues = self._current_issues(s)
        now = s.t

        for kind in issues:
            self._issue_last_seen[kind] = now

        for kind in list(self._incident_seen):
            if now - self._issue_last_seen.get(kind, 0.0) >= self.EPISODE_GRACE_SECONDS:
                del self._incident_seen[kind]
                self._issue_last_seen.pop(kind, None)

        for kind, detail in issues.items():
            last = self._incident_seen.get(kind)
            remind = self._REMIND_BY_KIND.get(kind, self.REMIND_SECONDS)
            if last is None or (now - last) >= remind:
                self._incident_seen[kind] = now
                return (kind, detail)
        return None

    def recent(self, seconds: int = 60) -> list[Sample]:
        cutoff = time.monotonic() - seconds
        return [s for s in self.history if s.t >= cutoff]
