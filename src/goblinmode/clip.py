"""Record the 30 seconds around a problem.

When a game's profile opts in, the daemon starts ``gpu-screen-recorder`` in
replay-buffer mode while the game runs. If the frame-rate watchdog fires or a
GPU fault shows up in the log, the buffer is flushed to a file and the path is
attached to the incident — so a bug report can include footage of exactly what
happened.

Needs ``gpu-screen-recorder`` (AUR / COPR). Everything no-ops without it.
"""

from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

log = logging.getLogger(__name__)

_TOOL = "gpu-screen-recorder"
_OUT_DIR = Path.home() / "Videos" / "Goblin Mode Pro"
_REPLAY_SECONDS = 30


def available() -> bool:
    return shutil.which(_TOOL) is not None


class ClipBuffer:
    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._last_save = 0.0

    def start(self) -> bool:
        if not available() or (self._proc and self._proc.poll() is None):
            return False
        try:
            _OUT_DIR.mkdir(parents=True, exist_ok=True)
            # -w screen : whole screen · -c mp4 · -r : replay length · -ro : replay dir
            self._proc = subprocess.Popen(
                [_TOOL, "-w", "screen", "-f", "60", "-c", "mp4",
                 "-r", str(_REPLAY_SECONDS), "-ro", str(_OUT_DIR)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, start_new_session=True,
            )
        except OSError as exc:
            log.warning("could not start the replay buffer: %s", exc)
            self._proc = None
            return False
        log.info("replay buffer started (%d s)", _REPLAY_SECONDS)
        return True

    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def save(self) -> str | None:
        """Flush the buffer to a file. Debounced to one save per 20 s."""
        if not self.running() or time.monotonic() - self._last_save < 20:
            return None
        before = set(_OUT_DIR.glob("*.mp4")) if _OUT_DIR.is_dir() else set()
        try:
            self._proc.send_signal(signal.SIGUSR1)
        except (OSError, ProcessLookupError):
            return None
        self._last_save = time.monotonic()
        # gpu-screen-recorder writes asynchronously; give it a beat to appear
        for _ in range(20):
            time.sleep(0.15)
            now = set(_OUT_DIR.glob("*.mp4")) if _OUT_DIR.is_dir() else set()
            new = now - before
            if new:
                path = str(sorted(new, key=os.path.getmtime)[-1])
                log.info("clip saved: %s", path)
                return path
        return None

    def stop(self) -> None:
        if self._proc is None:
            return
        try:
            self._proc.terminate()
            self._proc.wait(timeout=4)
        except (OSError, subprocess.TimeoutExpired):
            try:
                self._proc.kill()
            except OSError:
                pass
        self._proc = None
