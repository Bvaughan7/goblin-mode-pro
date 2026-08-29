"""Per-game session history and regression detection.

When a game exits, this summarises its MangoHud frame log (present whenever the
watchdog or the overlay was on) into a compact record -- average / median / 1%
low FPS, how long you played, the tweaks that were active -- appends it to
``~/.local/share/goblin-mode-pro/sessions.jsonl``, and compares it against the
recent history for the *same* game.

The point is to catch the slow regressions that no single incident shows: a
Proton bump, a driver update or a config drift that quietly took 15% off your
1% lows. A session whose 1% low is meaningfully below the recent median comes
back flagged.

FPS values come from the CSV's ``fps`` column; ``cpu_temp`` / ``gpu_temp`` are
averaged when MangoHud logged them. Time is wall-clock from game start to exit.
"""

from __future__ import annotations

import json
import logging
import platform
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from goblinmode.paths import MANGOHUD_LOG_DIR, SESSION_FILE, ensure_user_dirs

log = logging.getLogger(__name__)

#: how many recent prior sessions form the comparison baseline
BASELINE_SESSIONS = 6
#: need at least this many priors (with FPS stats) before flagging anything
BASELINE_MIN = 3
#: fractional change from the baseline that counts as a regression / improvement
REGRESSION_FRAC = 0.10
#: ignore CSV files older than this (seconds) relative to session start
CSV_GRACE_BEFORE = 15.0
#: minimum FPS samples before the stats are considered meaningful
MIN_SAMPLES = 30

_MAX_BYTES = 512 * 1024
_MAX_KEEP = 600


@dataclass
class SessionSummary:
    exe: str
    game: str
    started: str
    ended: str
    duration_s: float
    fps_avg: float | None = None
    fps_median: float | None = None
    fps_1low: float | None = None
    fps_min: float | None = None
    samples: int = 0
    cpu_temp_avg: float | None = None
    gpu_temp_avg: float | None = None
    kernel: str = ""
    tweaks: list[str] = field(default_factory=list)

    # populated only for benchmark runs
    benchmark: bool = False
    fps_01low: float | None = None       # 0.1% low
    fps_p95: float | None = None
    frametime_ms_avg: float | None = None
    frametime_stutter_pct: float | None = None   # % of frames > 2x the median frametime
    cpu_temp_max: float | None = None
    gpu_temp_max: float | None = None

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Regression:
    metric: str          # "1% low" | "average FPS"
    direction: str       # "regression" | "improvement"
    change_pct: float    # signed, relative to baseline (negative == slower)
    baseline: float
    current: float
    sessions_compared: int

    def as_dict(self) -> dict:
        return asdict(self)

    def headline(self, game: str) -> str:
        verb = "dropped" if self.direction == "regression" else "gained"
        return (
            f"{game}: {self.metric} {verb} {abs(self.change_pct):.0f}% "
            f"vs your recent average ({self.current:.0f} vs {self.baseline:.0f} fps)"
        )


@dataclass
class _Open:
    exe: str
    game: str
    tweaks: list[str]
    started_wall: str
    started_mono: float


# ---------------------------------------------------------------------------
# CSV parsing
# ---------------------------------------------------------------------------
def _percentile(values: list[float], q: float) -> float:
    """Nearest-rank percentile of a *sorted* list; q in [0, 1]."""
    if not values:
        return 0.0
    idx = max(0, min(len(values) - 1, int(round(q * (len(values) - 1)))))
    return values[idx]


def _parse_csv(path: Path):
    """Parse one MangoHud CSV. Returns ``(fps, cpu_temp, gpu_temp)`` for the
    common case; call :func:`_parse_csv_full` when you also need frame times."""
    fps, cpu, gpu, _ft = _parse_csv_full(path)
    return fps, cpu, gpu


def _parse_csv_full(path: Path):
    """``(fps, cpu_temp, gpu_temp, frametime_ms)`` from one MangoHud CSV."""
    fps: list[float] = []
    cpu: list[float] = []
    gpu: list[float] = []
    ft: list[float] = []
    fps_i = cpu_i = gpu_i = ft_i = None
    try:
        with open(path, "r", errors="replace") as fh:
            for raw in fh:
                cells = raw.strip().split(",")
                if fps_i is None:
                    low = [c.strip().lower() for c in cells]
                    if "fps" in low:
                        fps_i = low.index("fps")
                        cpu_i = low.index("cpu_temp") if "cpu_temp" in low else None
                        gpu_i = low.index("gpu_temp") if "gpu_temp" in low else None
                        ft_i = low.index("frametime") if "frametime" in low else None
                    continue
                if len(cells) <= fps_i:
                    continue
                try:
                    v = float(cells[fps_i])
                except ValueError:
                    continue
                if not (0 < v < 1000):
                    continue
                fps.append(v)
                for col, sink, lo, hi in ((cpu_i, cpu, 0, 200), (gpu_i, gpu, 0, 200),
                                          (ft_i, ft, 0, 2000)):
                    if col is not None and len(cells) > col:
                        try:
                            x = float(cells[col])
                        except ValueError:
                            continue
                        if lo < x < hi:
                            sink.append(x)
    except OSError as exc:
        log.warning("session: could not read %s: %s", path, exc)
    return fps, cpu, gpu, ft


def _logs_for_window(start_mono: float) -> list[Path]:
    if not MANGOHUD_LOG_DIR.exists():
        return []
    # translate the monotonic start into a wall-clock cutoff for mtime compares
    cutoff = time.time() - (time.monotonic() - start_mono) - CSV_GRACE_BEFORE
    out = []
    for p in MANGOHUD_LOG_DIR.glob("*.csv"):
        try:
            if p.stat().st_mtime >= cutoff:
                out.append(p)
        except OSError:
            continue
    return sorted(out, key=lambda p: p.stat().st_mtime)


# ---------------------------------------------------------------------------
class SessionTracker:
    def __init__(self) -> None:
        self._open: dict[str, _Open] = {}

    # -- lifecycle ----------------------------------------------------
    def start(self, exe: str, game: str, tweaks: list[str]) -> None:
        self._open[exe] = _Open(
            exe=exe,
            game=game or exe,
            tweaks=list(tweaks),
            started_wall=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            started_mono=time.monotonic(),
        )

    def cancel(self, exe: str) -> None:
        self._open.pop(exe, None)

    def end(self, exe: str, *, benchmark: bool = False
            ) -> tuple[SessionSummary, Regression | None] | None:
        """Finalise the session for *exe*: summarise, persist, compare.

        Returns ``None`` if there was no open session or it was too short
        (< 60 s, or < 30 s for a benchmark)."""
        op = self._open.pop(exe, None)
        if op is None:
            return None
        duration = max(0.0, time.monotonic() - op.started_mono)
        if duration < (30 if benchmark else 60):
            return None

        fps: list[float] = []
        cpu: list[float] = []
        gpu: list[float] = []
        ft: list[float] = []
        for path in _logs_for_window(op.started_mono):
            f, c, g, t = _parse_csv_full(path)
            fps += f
            cpu += c
            gpu += g
            ft += t

        summary = SessionSummary(
            exe=op.exe,
            game=op.game,
            started=op.started_wall,
            ended=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            duration_s=round(duration, 1),
            kernel=platform.release(),
            tweaks=op.tweaks,
            benchmark=benchmark,
        )
        if len(fps) >= MIN_SAMPLES:
            s = sorted(fps)
            summary.samples = len(s)
            summary.fps_avg = round(sum(s) / len(s), 1)
            summary.fps_median = round(_percentile(s, 0.5), 1)
            summary.fps_1low = round(_percentile(s, 0.01), 1)
            summary.fps_min = round(s[0], 1)
            if benchmark:
                summary.fps_01low = round(_percentile(s, 0.001), 1)
                summary.fps_p95 = round(_percentile(s, 0.95), 1)
        if cpu:
            summary.cpu_temp_avg = round(sum(cpu) / len(cpu), 1)
            summary.cpu_temp_max = round(max(cpu), 1) if benchmark else None
        if gpu:
            summary.gpu_temp_avg = round(sum(gpu) / len(gpu), 1)
            summary.gpu_temp_max = round(max(gpu), 1) if benchmark else None
        if benchmark and len(ft) >= MIN_SAMPLES:
            fts = sorted(ft)
            med = _percentile(fts, 0.5) or 1.0
            summary.frametime_ms_avg = round(sum(ft) / len(ft), 2)
            summary.frametime_stutter_pct = round(
                100 * sum(1 for x in ft if x > 2 * med) / len(ft), 2)

        prior = self._history_for(op.exe)
        self._persist(summary)
        return summary, _detect_regression(summary, prior)

    # -- history ----------------------------------------------------
    def history(self, exe: str | None = None, limit: int = 40) -> list[dict]:
        rows = _load_all()
        if exe is not None:
            rows = [r for r in rows if r.get("exe") == exe]
        return rows[-limit:]

    def _history_for(self, exe: str) -> list[dict]:
        return [r for r in _load_all() if r.get("exe") == exe]

    # -- persistence ----------------------------------------------
    def _persist(self, summary: SessionSummary) -> None:
        try:
            ensure_user_dirs()
            self._rotate_if_big()
            with open(SESSION_FILE, "a") as fh:
                fh.write(json.dumps(summary.as_dict()) + "\n")
        except OSError as exc:
            log.warning("could not persist session: %s", exc)

    def _rotate_if_big(self) -> None:
        try:
            if not SESSION_FILE.exists() or SESSION_FILE.stat().st_size < _MAX_BYTES:
                return
            tail = SESSION_FILE.read_text().splitlines()[-_MAX_KEEP:]
            SESSION_FILE.write_text("\n".join(tail) + "\n")
        except OSError as exc:
            log.warning("could not trim sessions.jsonl: %s", exc)


def _load_all() -> list[dict]:
    if not SESSION_FILE.exists():
        return []
    out = []
    for line in SESSION_FILE.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _detect_regression(cur: SessionSummary, prior: list[dict]) -> Regression | None:
    """Compare this session's 1% low (then average) against the recent baseline."""
    for metric, key in (("1% low", "fps_1low"), ("average FPS", "fps_avg")):
        current = getattr(cur, key)
        if current is None or current <= 0:
            continue
        history = [r[key] for r in prior[-BASELINE_SESSIONS:]
                   if isinstance(r.get(key), (int, float)) and r[key] > 0]
        if len(history) < BASELINE_MIN:
            continue
        history.sort()
        baseline = history[len(history) // 2]
        if baseline <= 0:
            continue
        frac = (current - baseline) / baseline
        if abs(frac) < REGRESSION_FRAC:
            return None  # this metric is stable -> nothing to report
        return Regression(
            metric=metric,
            direction="regression" if frac < 0 else "improvement",
            change_pct=round(frac * 100, 1),
            baseline=round(baseline, 1),
            current=round(current, 1),
            sessions_compared=len(history),
        )
    return None
