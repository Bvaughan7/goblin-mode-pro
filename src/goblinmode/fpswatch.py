"""Frame-rate watchdog.

MangoHud writes a CSV frame log (one row every ``log_interval`` ms) when the
profile enables the watchdog - see :mod:`goblinmode.mangohud`. This module tails
the newest log for the running game and flags a *sustained extreme dip*: the
kind of 10-15 FPS cliff that a game restart clears and thermal throttling does
not explain.

Time comes from the CSV's own ``elapsed`` column (a virtual clock), so detection
works the same whether rows arrive one-per-poll or in a burst after the file
appears. Detection: a dip is when the trailing ~2.5 s mean FPS falls to/under
``dip_floor`` **or** under ``dip_ratio`` x the trailing 30 s median. Fires once
on onset (with duration on recovery), debounced.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from goblinmode.paths import MANGOHUD_LOG_DIR

log = logging.getLogger(__name__)

REMIND_SECONDS = 120
_RECENT_S = 2.5
_BASELINE_S = 30.0


@dataclass
class FpsEvent:
    kind: str            # "dip" | "recovered"
    fps: float
    baseline: float
    duration_s: float = 0.0


def _to_seconds(delta: float) -> float:
    """Auto-scale a MangoHud `elapsed` delta (ns / us / ms / s) to seconds."""
    if delta > 1e7:
        return delta / 1e9
    if delta > 1e4:
        return delta / 1e6
    if delta > 20:
        return delta / 1e3
    return delta


class FpsWatcher:
    def __init__(self, dip_floor: float = 22.0, dip_ratio: float = 0.5) -> None:
        self.dip_floor = dip_floor
        self.dip_ratio = dip_ratio
        self._path: Path | None = None
        self._pos = 0
        self._fps_col: int | None = None
        self._elapsed_col: int | None = None
        self._last_elapsed: float | None = None
        self._vclock = 0.0
        self._hist: deque[tuple[float, float]] = deque(maxlen=6000)  # (vclock, fps)
        self._in_dip = False
        self._dip_started = 0.0
        self._dip_baseline = 0.0
        self._last_emit = -1e9

    def update(self, dip_floor: float, dip_ratio: float) -> None:
        self.dip_floor = dip_floor
        self.dip_ratio = dip_ratio

    # -- file handling -----------------------------------------------
    def _newest_log(self) -> Path | None:
        if not MANGOHUD_LOG_DIR.exists():
            return None
        logs = sorted(
            MANGOHUD_LOG_DIR.glob("*.csv"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        return logs[0] if logs else None

    def _rotate(self) -> None:
        newest = self._newest_log()
        if newest and newest != self._path:
            self._path = newest
            self._pos = 0
            self._fps_col = self._elapsed_col = None
            self._last_elapsed = None
            self._vclock = 0.0
            self._hist.clear()
            self._in_dip = False
            log.info("fps watchdog following %s", newest.name)

    # -- parsing ----------------------------------------------------
    def _ingest(self, line: str) -> None:
        cells = line.split(",")
        if self._fps_col is None:
            low = [c.strip().lower() for c in cells]
            if "fps" in low:
                self._fps_col = low.index("fps")
                self._elapsed_col = low.index("elapsed") if "elapsed" in low else None
            return
        if len(cells) <= self._fps_col:
            return
        try:
            fps = float(cells[self._fps_col])
        except ValueError:
            return
        if not (0 < fps < 1000):
            return

        et = None
        if self._elapsed_col is not None and len(cells) > self._elapsed_col:
            try:
                et = float(cells[self._elapsed_col])
            except ValueError:
                et = None
        if et is not None:
            if self._last_elapsed is not None and et > self._last_elapsed:
                self._vclock += _to_seconds(et - self._last_elapsed)
            self._last_elapsed = et
        else:
            self._vclock += 0.2  # nominal cadence when there's no elapsed column

        self._hist.append((self._vclock, fps))

    #: never read more than this per poll - keeps a poll cheap even if the daemon
    #: fell behind and MangoHud wrote megabytes in the meantime (MangoHud logs
    #: ~1 KB/s, so this is ~4 minutes of catch-up)
    _MAX_READ = 256 * 1024

    def poll(self) -> FpsEvent | None:
        self._rotate()
        if not self._path or not self._path.exists():
            return None
        try:
            size = self._path.stat().st_size
            if size < self._pos:            # file was truncated / replaced
                self._pos = 0
            with open(self._path, "r", errors="replace") as fh:
                if size - self._pos > self._MAX_READ:
                    # jump near the end; realign to the next full line
                    fh.seek(size - self._MAX_READ)
                    fh.readline()
                else:
                    fh.seek(self._pos)
                chunk = fh.read(self._MAX_READ)
                self._pos = fh.tell()
        except OSError:
            return None
        for raw in chunk.splitlines():
            raw = raw.strip()
            if raw:
                self._ingest(raw)
        return self._evaluate()

    # -- windows / stats ------------------------------------------
    def _window(self, seconds: float) -> list[float]:
        cut = self._vclock - seconds
        return [f for t, f in self._hist if t >= cut]

    def current_fps(self) -> float | None:
        w = self._window(_RECENT_S)
        return round(sum(w) / len(w), 1) if w else None

    def stats(self) -> dict:
        w = self._window(60)
        if not w:
            return {}
        s = sorted(w)
        return {
            "fps_avg": round(sum(w) / len(w), 1),
            "fps_min": round(min(w), 1),
            "fps_1low": round(s[max(0, len(s) // 100)], 1),
            "in_dip": self._in_dip,
        }

    def recent_trace(self, seconds: int = 90) -> list[dict]:
        cut = self._vclock - seconds
        return [{"t": round(t, 2), "fps": round(f, 1)} for t, f in self._hist if t >= cut]

    # -- dip logic ------------------------------------------------
    def _evaluate(self) -> FpsEvent | None:
        recent = self._window(_RECENT_S)
        base = self._window(_BASELINE_S)
        if len(recent) < 3 or len(base) < 12:
            return None
        mean_recent = sum(recent) / len(recent)
        med_base = sorted(base)[len(base) // 2]
        now = self._vclock

        is_dip = mean_recent <= self.dip_floor or (
            med_base > 5 and mean_recent <= med_base * self.dip_ratio
        )

        if is_dip and not self._in_dip:
            self._in_dip = True
            self._dip_started = now
            self._dip_baseline = med_base
            if now - self._last_emit >= REMIND_SECONDS:
                self._last_emit = now
                return FpsEvent("dip", round(mean_recent, 1), round(med_base, 1))
        elif not is_dip and self._in_dip:
            self._in_dip = False
            dur = now - self._dip_started
            if dur >= 0.5:
                return FpsEvent(
                    "recovered", round(mean_recent, 1),
                    round(self._dip_baseline, 1), round(dur, 1),
                )
        return None
