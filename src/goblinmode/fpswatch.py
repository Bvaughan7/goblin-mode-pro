"""Frame-rate watchdog.

MangoHud writes a CSV frame log (one row every ``log_interval`` ms) when the
profile enables the watchdog - see :mod:`goblinmode.mangohud`. This module tails
the newest log for the running game and flags a *sustained extreme dip*: the
kind of 10-15 FPS cliff that a game restart clears and thermal throttling does
not explain.

Time comes from the CSV's own ``elapsed`` column (a virtual clock), so detection
works the same whether rows arrive one-per-poll or in a burst after the file
appears. MangoHud doesn't say what unit ``elapsed`` is in, so the unit is
inferred once per log from the median row spacing and the frame rate (see
:func:`_infer_divisor`) and then applied unconditionally - never re-guessed per
row, where any delta that isn't the steady cadence reads as the wrong unit.
History is a 6000-row deque, which at the 200 ms cadence :mod:`goblinmode.mangohud`
asks for is ~20 minutes of virtual time - longer than the longest window read
off it (``_MAX_DIP_S``, then ``_BASELINE_S``).

Detection is a two-state machine (healthy / dipping) driven off the virtual
clock, not off how often ``poll`` runs:

* A sample is *low* when the trailing ~3 s mean FPS is at/under ``dip_floor``
  **or** under ``dip_ratio`` x the baseline.
* It only becomes a **dip** once low samples have run continuously for
  ``_MIN_DIP_DURATION_S`` - a 1-3 s dip is a menu, a zone load or shader
  compilation, not the "a restart clears it" cliff this is here to catch.
* The baseline is *frozen* at the pre-dip median for the whole episode, so a
  dip that persists can't drag a rolling median down and fake its own recovery.
* **Recovery** needs the mean to climb back to ``_RECOVERY_FRAC`` of that frozen
  baseline (and clear ``dip_floor`` with hysteresis) - "recovered to 24 FPS" is
  not a recovery.
* A window that isn't rendering at all (alt-tab / minimised -> FPS ~0 against a
  healthy baseline) is not a performance dip and is ignored.
* A dip that never bounces back within ``_MAX_DIP_S`` is taken as the new
  normal (a heavier zone, a settings change) and the baseline is relearned.
"""

from __future__ import annotations

import logging
import math
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from goblinmode.paths import MANGOHUD_LOG_DIR

log = logging.getLogger(__name__)

REMIND_SECONDS = 120
_RECENT_S = 3.0
_BASELINE_S = 30.0
#: low FPS has to persist this long (virtual seconds) before it's an incident
_MIN_DIP_DURATION_S = 4.0
#: recovery = trailing mean back to this fraction of the frozen pre-dip baseline
_RECOVERY_FRAC = 0.85
#: ...and clear the absolute floor by this margin, so it can't flap on the line
_EXIT_HYSTERESIS = 1.15
#: FPS at/under this against a healthy baseline = the window isn't rendering
#: (alt-tab / minimised), not a performance problem
_NOT_RENDERING_FPS = 5.0
#: a dip that never recovers within this long is the new normal, not a dip
_MAX_DIP_S = 120.0


@dataclass
class FpsEvent:
    kind: str            # "dip" | "recovered"
    fps: float
    baseline: float
    duration_s: float = 0.0


#: divisors that turn a MangoHud ``elapsed`` value into seconds, by unit
_UNIT_DIVISORS = (1.0, 1e3, 1e6, 1e9)          # s, ms, us, ns
#: a row can't sit closer to the previous one than one frame, less this much
#: slack for jitter - used to rule out units that imply sub-frame row spacing
_SUB_FRAME_SLACK = 0.5
#: deltas to see before committing to a unit (median, so a gap can't sway it)
_UNIT_SAMPLE_N = 8
#: ...but settle for this many at the end of a poll, so a slow log still runs
_UNIT_MIN_DELTAS = 3
#: and give up waiting entirely after this many buffered rows
_UNIT_MAX_PENDING = 64
#: unit named in the column label, if MangoHud ever starts naming one
_UNIT_BY_SUFFIX = {"ns": 1e9, "us": 1e6, "\u00b5s": 1e6, "ms": 1e3, "sec": 1.0, "s": 1.0}


def _divisor_from_label(label: str) -> float | None:
    """Read the unit off an ``elapsed`` column label, if it carries one.

    MangoHud writes a bare ``elapsed`` today, so this normally returns None and
    the unit is inferred from the data instead.
    """
    tail = label.strip().lower().strip(")]").replace("(", "_").replace("[", "_")
    for sep in ("_", "-", " ", "/"):
        tail = tail.replace(sep, "_")
    token = tail.rsplit("_", 1)[-1]
    return _UNIT_BY_SUFFIX.get(token)


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def _infer_divisor(samples: list[tuple[float, float]]) -> float:
    """Pick the unit for this log's ``elapsed`` column, once, from its data.

    The unit is decided **once per log** off the median delta, never per row: a
    per-row magnitude test misreads any delta that isn't the steady cadence. A
    30 s stall in a ms-unit log looks like a us-unit cadence and would
    under-advance the clock 1000x; a 1 ms frame in a ns-unit log looks like a
    us-unit stall and would over-advance it by the same factor. Both corrupt
    every window measured off the virtual clock - including the dip-duration
    bars that decide whether a stall is reported at all.

    The median delta alone can still be ambiguous, because the units are a
    factor of 1000 apart and so are plausible row cadences: 1e6 is a 1 ms
    cadence in ns and a 1 s cadence in us, and nothing about the number says
    which. The *frame rate* breaks the tie. MangoHud emits a row either every
    ``log_interval`` or every frame, so the row cadence can never be much
    shorter than one frame - a unit implying sub-frame spacing is impossible,
    and of what's left the truth is the reading closest to the frame time.

    `samples` is (raw delta, fps) per row; rows with no delta are ignored.
    """
    usable = [(d, f) for d, f in samples if d > 0 and f > 0]
    if not usable:
        return 1e9                       # MangoHud's own unit, as a last resort
    median = _median([d for d, _ in usable])
    frame_s = 1.0 / _median([f for _, f in usable])
    floor = frame_s * _SUB_FRAME_SLACK
    plausible = [d for d in _UNIT_DIVISORS if median / d >= floor]
    return min(
        plausible or list(_UNIT_DIVISORS),
        key=lambda d: abs(math.log((median / d) / frame_s)),
    )


class FpsWatcher:
    def __init__(self, dip_floor: float = 22.0, dip_ratio: float = 0.5) -> None:
        self.dip_floor = dip_floor
        self.dip_ratio = dip_ratio
        self._path: Path | None = None
        self._pos = 0
        self._fps_col: int | None = None
        self._elapsed_col: int | None = None
        self._last_elapsed: float | None = None
        #: seconds-per-unit for this log, decided once (see _infer_divisor)
        self._unit_div: float | None = None
        self._unit_deltas: list[tuple[float, float]] = []
        self._pending: list[tuple[float, float]] = []   # (raw delta, fps)
        self._vclock = 0.0
        #: (vclock, fps). 6000 rows at the 200 ms cadence mangohud.py asks for
        #: is ~20 min of virtual time - comfortably longer than the longest
        #: window read off it (_MAX_DIP_S, then _BASELINE_S).
        self._hist: deque[tuple[float, float]] = deque(maxlen=6000)
        self._state = "healthy"           # "healthy" | "dipping"
        self._dip_started = 0.0
        self._frozen_baseline = 0.0       # pre-dip median, held for the episode
        self._dip_announced = False       # did we emit the "dip" for this episode
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
            self._unit_div = None
            self._unit_deltas.clear()
            self._pending.clear()
            self._vclock = 0.0
            self._hist.clear()
            self._state = "healthy"
            self._dip_announced = False
            log.info("fps watchdog following %s", newest.name)

    # -- parsing ----------------------------------------------------
    def _ingest(self, line: str) -> None:
        cells = line.split(",")
        if self._fps_col is None:
            low = [c.strip().lower() for c in cells]
            if "fps" in low:
                self._fps_col = low.index("fps")
                self._elapsed_col = next(
                    (i for i, c in enumerate(low) if c.split("_")[0].split("(")[0]
                     .strip() == "elapsed"),
                    None,
                )
                if self._elapsed_col is not None:
                    self._unit_div = _divisor_from_label(low[self._elapsed_col])
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
        if et is None:
            self._vclock += 0.2  # nominal cadence when there's no elapsed column
            self._hist.append((self._vclock, fps))
            return

        delta = 0.0
        if self._last_elapsed is not None and et > self._last_elapsed:
            delta = et - self._last_elapsed
        self._last_elapsed = et

        if self._unit_div is None:
            # Hold the row back until the unit is known - the divisor has to be
            # applied to every delta including this log's first ones.
            self._pending.append((delta, fps))
            if delta > 0:
                self._unit_deltas.append((delta, fps))
            if (len(self._unit_deltas) >= _UNIT_SAMPLE_N
                    or len(self._pending) >= _UNIT_MAX_PENDING):
                self._settle_unit()
            return

        self._vclock += delta / self._unit_div
        self._hist.append((self._vclock, fps))

    def _settle_unit(self) -> None:
        """Commit to a unit and replay the rows held back while deciding."""
        if self._unit_div is None:
            self._unit_div = _infer_divisor(self._unit_deltas)
            log.debug("fps watchdog: elapsed unit = 1/%g s", self._unit_div)
        for delta, fps in self._pending:
            self._vclock += delta / self._unit_div
            self._hist.append((self._vclock, fps))
        self._pending.clear()
        self._unit_deltas.clear()

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
            with open(self._path, errors="replace") as fh:
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
        if self._unit_div is None and len(self._unit_deltas) >= _UNIT_MIN_DELTAS:
            self._settle_unit()
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
            "in_dip": self._state == "dipping",
        }

    def recent_trace(self, seconds: int = 90) -> list[dict]:
        cut = self._vclock - seconds
        return [{"t": round(t, 2), "fps": round(f, 1)} for t, f in self._hist if t >= cut]

    # -- dip logic ------------------------------------------------
    def _is_low(self, fps: float, baseline: float) -> bool:
        if fps <= self.dip_floor:
            return True
        return baseline > 5 and fps <= baseline * self.dip_ratio

    def _low_run_seconds(self, baseline: float) -> float:
        """Virtual seconds of unbroken *low* samples at the tail of history."""
        run = 0.0
        last_t: float | None = None
        for t, f in reversed(self._hist):
            if not self._is_low(f, baseline):
                break
            if last_t is not None:
                run += last_t - t
            last_t = t
        return run

    def _evaluate(self) -> FpsEvent | None:
        recent = self._window(_RECENT_S)
        base = self._window(_BASELINE_S)
        if len(recent) < 3 or len(base) < 12:
            return None
        fps = sum(recent) / len(recent)
        now = self._vclock

        prev_baseline = self._frozen_baseline
        idle_window = fps <= _NOT_RENDERING_FPS and prev_baseline > 30

        # the baseline is relearned only while healthy and actually rendering;
        # it's frozen for the length of a dip episode so the episode can't drag
        # a rolling median down and fake its own recovery
        if self._state == "healthy" and not idle_window:
            self._frozen_baseline = sorted(base)[len(base) // 2]
        baseline = self._frozen_baseline

        low = self._is_low(fps, baseline) and not idle_window

        if self._state != "dipping":
            run = self._low_run_seconds(baseline) if low else 0.0
            if run < _MIN_DIP_DURATION_S:
                self._state = "healthy"
                return None
            self._state = "dipping"
            self._dip_started = now - run
            if now - self._last_emit >= REMIND_SECONDS:
                self._last_emit = now
                self._dip_announced = True
                return FpsEvent("dip", round(fps, 1), round(baseline, 1))
            self._dip_announced = False
            return None

        # in a confirmed dip
        recovered = fps >= max(self.dip_floor * _EXIT_HYSTERESIS,
                               baseline * _RECOVERY_FRAC)
        if recovered:
            dur = now - self._dip_started
            self._state = "healthy"
            if self._dip_announced:
                self._dip_announced = False
                return FpsEvent("recovered", round(fps, 1), round(baseline, 1),
                                round(dur, 1))
            return None
        if now - self._dip_started >= _MAX_DIP_S:
            # never bounced back — treat it as the new normal and relearn
            self._state = "healthy"
            self._dip_announced = False
        return None
