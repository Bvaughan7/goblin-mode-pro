"""The Observer - passive process monitor.

A polling loop (driven by the daemon's GLib main loop) that checks every
``poll_interval`` seconds for target game executables. It keeps per-game state so
:class:`~goblinmode.payload.PerformancePayload` is invoked exactly once on launch
and once on exit.
"""

from __future__ import annotations

import functools
import logging
import re
from dataclasses import dataclass
from typing import Callable

import psutil

from goblinmode import gamedetect
from goblinmode.config import GameProfile, Settings
from goblinmode.gamedetect import GameCandidate

log = logging.getLogger(__name__)

# Wine/Proton wrapper processes we never want to treat as "the game" when
# hunting for the PID to renice.
_WINE_INFRA = {
    "wine", "wine64", "wineserver", "wine-preloader", "wine64-preloader",
    "start.exe", "services.exe", "explorer.exe", "rpcss.exe", "plugplay.exe",
    "winedevice.exe", "conhost.exe", "svchost.exe", "tabtip.exe",
    "steam.exe", "steamwebhelper.exe", "gameoverlayui.exe",
    "proton", "python3", "pv-bwrap", "srt-bwrap", "reaper",
}


@dataclass
class GameEvent:
    profile: GameProfile | None
    pid: int | None
    running: bool  # True = launched, False = exited
    auto: bool = False                       # found by the auto-detector, no profile yet
    candidate: GameCandidate | None = None


MatchResult = tuple[GameProfile, int]

#: longest string a user regex is run against - a backtracking guard, since
#: Python's ``re`` has no timeout and the poll runs on the daemon's main loop
_MAX_HAYSTACK = 4096


@functools.lru_cache(maxsize=64)
def _compiled(pattern: str):
    try:
        return re.compile(pattern[:128])
    except re.error:
        return None


def _basename(s: str) -> str:
    """Basename that also splits Windows-style paths (``C:\\dir\\Game.exe``)."""
    if not s:
        return ""
    s = s.strip().strip('"').strip("'")
    for sep in ("\\", "/"):
        if sep in s:
            s = s.rsplit(sep, 1)[-1]
    return s


def _candidate_names(name: str, exe: str, cmdline: list[str]) -> set[str]:
    out: set[str] = set()
    if name:
        out.add(name)
    if exe:
        out.add(_basename(exe))
    for tok in cmdline:
        out.add(_basename(tok))
    return {c for c in out if c}


def _matches(
    profile: GameProfile, name: str, exe: str, cmdline: list[str] | str
) -> bool:
    if isinstance(cmdline, str):  # tolerate the old joined-string form
        cmdline = cmdline.split()
    target = profile.exe
    candidates = _candidate_names(name, exe, cmdline)

    if profile.match_mode == "exact":
        # Windows exes are case-insensitive; process comm is capped at 15 chars.
        tl = target.lower()
        for c in candidates:
            cl = c.lower()
            if cl == tl or (len(c) >= 15 and tl.startswith(cl)):
                return True
        return False
    if profile.match_mode == "substring":
        hay = (name + " " + exe + " " + " ".join(cmdline)).lower()[:_MAX_HAYSTACK]
        return target.lower() in hay
    if profile.match_mode == "regex":
        pat = _compiled(target)
        if pat is None:
            return False
        hay = (name + " " + exe + " " + " ".join(cmdline))[:_MAX_HAYSTACK]
        return bool(pat.search(hay))
    return False


class Observer:
    def __init__(
        self,
        settings: Settings,
        on_event: Callable[[GameEvent], None],
    ) -> None:
        self.settings = settings
        self._on_event = on_event
        self._running: dict[str, int] = {}  # exe -> pid currently detected

    def update_settings(self, settings: Settings) -> None:
        self.settings = settings

    # -- one poll --------------------------------------------------------
    def poll(self) -> None:
        if not self.settings.master_enabled and not self._running:
            return

        procs = list(psutil.process_iter(["pid", "name", "exe", "cmdline", "ppid"]))

        # found: exe -> (pid, GameProfile|None, GameCandidate|None)
        found: dict[str, tuple[int, GameProfile | None, GameCandidate | None]] = {}

        for profile in self.settings.enabled_profiles():
            pid = self._find_pid(profile, procs)
            if pid is not None:
                found[profile.exe] = (pid, profile, None)

        # The auto-detect sweep is the expensive part (per-process /proc/*/maps
        # and fdinfo reads). Skip it entirely while a profiled game is already
        # matched - detecting a *second* concurrent game is a rare case not worth
        # the cost on every poll during normal play.
        if self.settings.auto_detect and self.settings.master_enabled and not found:
            ignored = {g.lower() for g in self.settings.ignored_games}
            try:
                for cand in gamedetect.detect_games(procs=procs):
                    key = cand.exe
                    if key in found or cand.exe.lower() in ignored:
                        continue
                    if self.settings.profile_for_exe(key):
                        continue  # a disabled profile exists - respect the user's choice
                    found[key] = (cand.pid, None, cand)
            except Exception:  # noqa: BLE001
                log.exception("auto-detect sweep failed")

        for exe, (pid, profile, cand) in found.items():
            if exe not in self._running:
                self._running[exe] = pid
                if profile is not None:
                    log.info("game launched: %s (pid %d)", exe, pid)
                    self._emit(GameEvent(profile, pid, running=True))
                else:
                    log.info("game auto-detected: %s (%s, pid %d)", cand.display_name, cand.source, pid)
                    self._emit(GameEvent(None, pid, running=True, auto=True, candidate=cand))
            else:
                self._running[exe] = pid

        for exe in list(self._running):
            if exe not in found:
                self._running.pop(exe, None)
                profile = self.settings.profile_for_exe(exe)
                log.info("game exited: %s", exe)
                self._emit(GameEvent(profile, None, running=False))

    # -- helpers --------------------------------------------------------
    def _find_pid(self, profile: GameProfile, procs: list[psutil.Process]) -> int | None:
        """Return the PID to optimise - the real game process, not a wrapper."""
        candidates: list[tuple[int, str, float]] = []
        for p in procs:
            try:
                info = p.info
                name = info.get("name") or ""
                exe = info.get("exe") or ""
                cmd = list(info.get("cmdline") or [])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            if not _matches(profile, name, exe, cmd):
                continue
            # Only the process *comm* disqualifies a match - not /proc/exe, which
            # for a Wine/Proton game points at the wine loader itself.
            if name.lower() in _WINE_INFRA:
                continue
            try:
                rss = p.memory_info().rss
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                rss = 0
            candidates.append((info["pid"], name.lower(), rss))

        if not candidates:
            return None
        # Prefer the fattest matching process (the actual game, not a launcher).
        candidates.sort(key=lambda c: c[2], reverse=True)
        return candidates[0][0]

    def _emit(self, event: GameEvent) -> None:
        try:
            self._on_event(event)
        except Exception:  # noqa: BLE001 - never let a handler kill the loop
            log.exception("observer event handler failed")

    @property
    def active_exes(self) -> list[str]:
        return sorted(self._running)
