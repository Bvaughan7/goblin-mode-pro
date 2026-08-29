"""Proton / Wine build discovery and shader-cache accounting.

Read-only helpers for the GUI: which custom Proton/Wine builds are installed,
and how much disk the DXVK / VKD3D / Steam shader caches are using per game.
Nothing here needs the daemon or any privilege - it's all under ``$HOME``.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

_HOME = Path.home()

#: Steam library roots to probe (native, flatpak, snap, and the classic path).
_STEAM_ROOTS = [
    _HOME / ".steam/steam",
    _HOME / ".local/share/Steam",
    _HOME / ".var/app/com.valvesoftware.Steam/data/Steam",
    _HOME / "snap/steam/common/.local/share/Steam",
]

_COMPAT_DIRS = [r / "compatibilitytools.d" for r in _STEAM_ROOTS] + [
    _HOME / ".local/share/lutris/runners/proton",
]

_CACHE_DIRS = {
    "DXVK state cache": [_HOME / ".cache/dxvk", _HOME / ".local/share/dxvk"],
    "Steam shader cache": [r / "steamapps/shadercache" for r in _STEAM_ROOTS],
    "NVIDIA GL cache": [_HOME / ".cache/nvidia/GLCache"],
    "Mesa shader cache": [_HOME / ".cache/mesa_shader_cache",
                          _HOME / ".cache/mesa_shader_cache_db"],
    "VKD3D cache": [_HOME / ".cache/vkd3d-proton"],
}


def _dir_size(path: Path) -> int:
    total = 0
    try:
        for root, _dirs, files in os.walk(path, onerror=lambda _e: None):
            for f in files:
                try:
                    total += os.stat(os.path.join(root, f)).st_size
                except OSError:
                    continue
    except OSError:
        pass
    return total


def installed_builds() -> list[dict]:
    """Custom Proton/Wine builds the user has dropped in, newest first."""
    seen: dict[str, dict] = {}
    for d in _COMPAT_DIRS:
        if not d.is_dir():
            continue
        for entry in sorted(d.iterdir()):
            if not entry.is_dir():
                continue
            name = entry.name
            # a valid build has a proton/wine launcher or a version file
            kind = ("Proton" if (entry / "proton").exists()
                    else "Wine" if (entry / "bin/wine").exists()
                    else "Proton" if (entry / "version").exists() else None)
            if kind is None:
                continue
            try:
                mtime = entry.stat().st_mtime
            except OSError:
                mtime = 0.0
            seen.setdefault(name, {"name": name, "kind": kind,
                                   "path": str(entry), "mtime": mtime})
    return sorted(seen.values(), key=lambda b: b["mtime"], reverse=True)


def shader_caches() -> list[dict]:
    """Each shader-cache location that exists, with its size in bytes.
    Symlinked / duplicate Steam roots are collapsed by real path."""
    seen: dict[str, dict] = {}
    for label, paths in _CACHE_DIRS.items():
        for p in paths:
            if not p.is_dir():
                continue
            try:
                real = str(p.resolve())
            except OSError:
                real = str(p)
            if real in seen:
                continue
            seen[real] = {"label": label, "path": str(p), "bytes": _dir_size(p)}
    return sorted(seen.values(), key=lambda c: c["bytes"], reverse=True)


def clear_cache(path: str) -> tuple[bool, str]:
    """Delete the *contents* of one shader-cache dir (kept from :func:`shader_caches`).

    The path must be one we listed - this never touches an arbitrary directory.
    """
    known = {c["path"] for c in shader_caches()}
    if path not in known:
        return False, "not a known shader-cache path"
    target = Path(path)
    removed = 0
    try:
        for child in target.iterdir():
            if child.is_dir():
                import shutil
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
            removed += 1
    except OSError as exc:
        return False, str(exc)
    log.info("cleared %d entries from %s", removed, path)
    return True, f"cleared {removed} entries"
