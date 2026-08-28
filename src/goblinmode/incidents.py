"""Incident log + LLM export payload.

An *incident* is a single noteworthy event during gameplay: a thermal/power
throttle onset, a GPU driver fault spotted in the Wine/Proton log, etc. They are
kept in a small in-memory ring and appended to
``~/.local/share/goblin-mode-pro/incidents.jsonl``.

``build_llm_payload`` packages one incident (plus the surrounding metric window,
log tail and the tweaks that were active) into a structured object wrapped in a
fixed system prompt, ready to drop into an external LLM.
"""

from __future__ import annotations

import json
import logging
import platform
import shutil
import subprocess
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from goblinmode.paths import INCIDENT_FILE, ensure_user_dirs

log = logging.getLogger(__name__)

SCHEMA = "gmp.incident.v1"

SYSTEM_PROMPT = (
    "You are a Linux gaming performance diagnostician. The following JSON was "
    "produced by Goblin Mode Pro during a game session (system details are in the "
    "'system' object). Given the incident, the metric window leading up to it, "
    "the log tail and the performance tweaks that were active, identify the most "
    "likely bottleneck - thermal, power-limit (RAPL PL1/PL2), GPU driver, VRAM "
    "exhaustion / host-memory fallback, PCIe link down-training, VKD3D/DXVK "
    "pipeline caching, CPU, or I/O - and give concrete remediation steps for that "
    "distro, ordered by expected impact. Use gpu_state if present. Be concise."
)


@dataclass
class Incident:
    kind: str                      # thermal_throttle | power_limit | gpu_throttle | gpu_fault
    detail: str
    game: str = ""
    game_pid: int | None = None
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    mono: float = field(default_factory=time.monotonic)
    metrics_window: list[dict] = field(default_factory=list)
    logs_tail: list[str] = field(default_factory=list)
    active_tweaks: dict[str, Any] = field(default_factory=dict)
    gpu_state: dict[str, Any] = field(default_factory=dict)
    fps_trace: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        d = {
            "kind": self.kind,
            "detail": self.detail,
            "game": self.game,
            "game_pid": self.game_pid,
            "ts": self.ts,
            "metrics_window": self.metrics_window,
            "logs_tail": self.logs_tail,
            "active_tweaks": self.active_tweaks,
        }
        if self.gpu_state:
            d["gpu_state"] = self.gpu_state
        if self.fps_trace:
            d["fps_trace"] = self.fps_trace
        return d


def _dmi(field: str) -> str:
    try:
        return Path(f"/sys/class/dmi/id/{field}").read_text().strip()
    except OSError:
        return ""


def _distro() -> str:
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if line.startswith("PRETTY_NAME="):
                return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return platform.system()


def _system_info() -> dict[str, str]:
    info = {
        "distro": _distro(),
        "kernel": platform.release(),
        "python": platform.python_version(),
    }
    chassis = " ".join(x for x in (_dmi("sys_vendor"), _dmi("product_name")) if x)
    if chassis:
        info["chassis"] = chassis
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                info["cpu"] = line.split(":", 1)[1].strip()
                break
    except OSError:
        pass
    if shutil.which("nvidia-smi"):
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"],
                capture_output=True,
                text=True,
                timeout=4,
            ).stdout.strip()
            if out:
                name, _, drv = out.partition(",")
                info["gpu"] = name.strip()
                info["nvidia_driver"] = drv.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    return info


class IncidentLog:
    def __init__(self, maxlen: int = 100) -> None:
        self._ring: deque[Incident] = deque(maxlen=maxlen)

    def add(self, incident: Incident) -> None:
        self._ring.append(incident)
        self._persist(incident)
        log.info("incident: %s - %s", incident.kind, incident.detail)

    #: keep the on-disk log bounded - trim to the newest MAX_KEEP when it grows
    MAX_BYTES = 2 * 1024 * 1024
    MAX_KEEP = 200

    def _persist(self, incident: Incident) -> None:
        try:
            ensure_user_dirs()
            self._rotate_if_big()
            with open(INCIDENT_FILE, "a") as fh:
                fh.write(json.dumps(incident.as_dict()) + "\n")
        except OSError as exc:
            log.warning("could not persist incident: %s", exc)

    def _rotate_if_big(self) -> None:
        try:
            if not INCIDENT_FILE.exists() or INCIDENT_FILE.stat().st_size < self.MAX_BYTES:
                return
            tail = INCIDENT_FILE.read_text().splitlines()[-self.MAX_KEEP:]
            INCIDENT_FILE.write_text("\n".join(tail) + "\n")
            log.info("trimmed incidents.jsonl to %d lines", len(tail))
        except OSError as exc:
            log.warning("could not trim incidents.jsonl: %s", exc)

    def latest(self) -> Incident | None:
        return self._ring[-1] if self._ring else None

    def all(self) -> list[Incident]:
        return list(self._ring)

    def load_history(self, limit: int = 100) -> list[dict]:
        if not INCIDENT_FILE.exists():
            return []
        lines = INCIDENT_FILE.read_text().splitlines()[-limit:]
        out = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out


def _thin(rows: list, target: int = 20) -> list:
    if len(rows) <= target:
        return rows
    step = len(rows) / target
    out = [rows[int(i * step)] for i in range(target)]
    out[-1] = rows[-1]
    return out


def build_llm_payload(incident: Incident, model_hint: str = "") -> str:
    from goblinmode.logrules import redact

    payload = {
        "schema": SCHEMA,
        "timestamp": incident.ts,
        "system": _system_info(),
        "game": {"exe": incident.game, "pid": incident.game_pid},
        "trigger": {"type": incident.kind, "detail": redact(incident.detail)},
        "metrics_window": _thin(incident.metrics_window, 20),
        "logs_tail": [redact(str(x)) for x in incident.logs_tail[-20:]],
        "active_tweaks": incident.active_tweaks,
    }
    if incident.gpu_state:
        payload["gpu_state"] = incident.gpu_state
    if incident.fps_trace:
        payload["fps_trace"] = _thin(incident.fps_trace, 30)
    if model_hint:
        payload["user_note"] = model_hint
    return (
        SYSTEM_PROMPT
        + "\n\n```json\n"
        + json.dumps(payload, indent=2)
        + "\n```\n"
    )


def copy_to_clipboard(text: str) -> bool:
    """Best-effort clipboard copy from a headless context (Wayland/X11)."""
    for cmd in (["wl-copy"], ["xclip", "-selection", "clipboard"]):
        if shutil.which(cmd[0]):
            try:
                subprocess.run(cmd, input=text, text=True, check=True, timeout=5)
                return True
            except (OSError, subprocess.SubprocessError):
                continue
    log.warning("no clipboard tool (wl-copy/xclip) available")
    return False
