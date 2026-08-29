"""CPU affinity ("core pinning") for a game's process tree.

Pinning a game to the fast cores of a hybrid CPU, or to a single CCD on a
chiplet Ryzen, keeps its threads off the slow cores / the cross-CCD latency
penalty. Because a process may set the affinity of *its own* children without
privilege, this needs no root helper - the daemon runs as the same user as the
game.

:func:`target_cpus` turns a profile's ``core_pin`` mode + the detected core
layout into a concrete CPU set; :func:`pin` / :func:`restore` apply and undo it
across every thread of a PID (and its direct children).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)


def target_cpus(mode: str, layout: dict) -> list[int] | None:
    """The CPU list for *mode*, or ``None`` when it does not apply here."""
    if mode == "performance":
        cpus = layout.get("performance")
        return list(cpus) if cpus else None
    if mode == "cache0":
        groups = layout.get("cache_groups") or []
        return list(groups[0]) if groups else None
    return None


def _tids(pid: int) -> list[int]:
    try:
        return [int(p.name) for p in Path(f"/proc/{pid}/task").iterdir()]
    except OSError:
        return [pid]


def _child_pids(pid: int) -> list[int]:
    out: list[int] = []
    try:
        for tid in _tids(pid):
            children = Path(f"/proc/{pid}/task/{tid}/children")
            out += [int(x) for x in children.read_text().split()]
    except OSError:
        pass
    return out


def current_affinity(pid: int) -> list[int] | None:
    try:
        return sorted(os.sched_getaffinity(pid))
    except (OSError, AttributeError):
        return None


def pin(pid: int, cpus: list[int]) -> bool:
    """Set the affinity of every thread of *pid* (and its children) to *cpus*.

    Returns True if at least the main PID was pinned.
    """
    if not cpus or not hasattr(os, "sched_setaffinity"):
        return False
    mask = set(cpus)
    ok = False
    targets = {pid, *_child_pids(pid)}
    for target in targets:
        for tid in _tids(target):
            try:
                os.sched_setaffinity(tid, mask)
                ok = ok or (tid == pid)
            except OSError as exc:
                log.debug("sched_setaffinity(%d) failed: %s", tid, exc)
    if ok:
        log.info("pinned pid %d (+%d children) to CPUs %s",
                 pid, len(targets) - 1, sorted(mask))
    return ok


def restore(pid: int, cpus: list[int]) -> None:
    """Put *pid*'s threads back on *cpus* (the affinity captured before pinning)."""
    if not cpus or not hasattr(os, "sched_setaffinity"):
        return
    mask = set(cpus)
    for target in {pid, *_child_pids(pid)}:
        for tid in _tids(target):
            try:
                os.sched_setaffinity(tid, mask)
            except OSError:
                pass
