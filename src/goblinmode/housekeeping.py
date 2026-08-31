"""Keep the daemon's log directories from growing without bound.

The ``goblin-run`` wrapper writes a fresh stderr log per launch into
``GAME_LOG_DIR`` and MangoHud writes a CSV per session into
``MANGOHUD_LOG_DIR``. Incidents cap their own file (see ``incidents.py``), but
nothing prunes these two - a heavy user quietly accumulates gigabytes. This
module is called on daemon start and after every session ends.
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

#: Per-directory ceilings, whichever is hit first. Oldest files go first.
KEEP_NEWEST = 40
MAX_BYTES = 500 * 1024 * 1024


def prune(directory: Path, keep_newest: int = KEEP_NEWEST,
          max_bytes: int = MAX_BYTES, pattern: str = "*") -> int:
    """Delete the oldest files matching *pattern* in *directory* until at most
    *keep_newest* remain and their combined size is under *max_bytes*. Returns
    how many were removed. Subdirectories are never touched, and a file that
    vanishes or won't unlink is skipped rather than fatal."""
    try:
        stats = {}
        for p in directory.glob(pattern):
            try:
                if p.is_file():
                    stats[p] = p.stat()
            except OSError:
                pass
    except OSError:
        return 0

    newest_first = sorted(stats, key=lambda p: stats[p].st_mtime, reverse=True)

    removed = 0
    running = 0
    for i, p in enumerate(newest_first):
        running += stats[p].st_size
        if i < keep_newest and running <= max_bytes:
            continue
        try:
            p.unlink()
            removed += 1
        except OSError as exc:
            log.debug("could not prune %s: %s", p, exc)
    if removed:
        log.info("pruned %d old file(s) from %s", removed, directory)
    return removed


def prune_all() -> None:
    """Prune every directory the daemon is responsible for."""
    from goblinmode.paths import GAME_LOG_DIR, MANGOHUD_LOG_DIR

    prune(GAME_LOG_DIR, pattern="*.log")
    prune(MANGOHUD_LOG_DIR, pattern="*.csv")
