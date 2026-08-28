"""Wine/Proton stderr log watcher.

The daemon is not the parent of the game process, so it cannot read Proton's
stderr directly. Instead the ``goblin-run`` wrapper tees stderr into
``~/.local/share/goblin-mode-pro/logs/``; this module tails the newest such file
while a game is active and raises a ``gpu_fault`` incident on a critical match.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

from goblinmode.logrules import LIVE_PATTERNS as _PATTERNS
from goblinmode.paths import GAME_LOG_DIR

log = logging.getLogger(__name__)

CONTEXT_LINES = 12


@dataclass
class LogHit:
    label: str
    line: str
    context: list[str]


class LogWatcher:
    def __init__(self, cooldown: float = 30.0) -> None:
        self._path: Path | None = None
        self._pos = 0
        self._recent: list[str] = []
        self._cooldown = cooldown
        self._last_hit_at = 0.0

    def _newest_log(self) -> Path | None:
        if not GAME_LOG_DIR.exists():
            return None
        logs = sorted(
            GAME_LOG_DIR.glob("*.log"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        return logs[0] if logs else None

    def _rotate_if_needed(self) -> None:
        newest = self._newest_log()
        if newest and newest != self._path:
            self._path = newest
            self._pos = 0
            self._recent.clear()
            log.info("log watcher following %s", newest)

    def tail_tail(self, n: int = CONTEXT_LINES) -> list[str]:
        return list(self._recent[-n:])

    def poll(self) -> LogHit | None:
        """Read new lines; return the first critical hit (respecting cooldown)."""
        self._rotate_if_needed()
        if not self._path or not self._path.exists():
            return None
        try:
            with open(self._path, "r", errors="replace") as fh:
                fh.seek(self._pos)
                new = fh.read()
                self._pos = fh.tell()
        except OSError:
            return None

        hit: LogHit | None = None
        for raw in new.splitlines():
            line = raw.rstrip()
            self._recent.append(line)
            if len(self._recent) > 200:
                self._recent = self._recent[-200:]
            if hit is not None:
                continue
            now = time.monotonic()
            if now - self._last_hit_at < self._cooldown:
                continue
            for pattern, label in _PATTERNS:
                if pattern.search(line):
                    self._last_hit_at = now
                    hit = LogHit(
                        label=label,
                        line=line,
                        context=list(self._recent[-CONTEXT_LINES:]),
                    )
                    break
        return hit
