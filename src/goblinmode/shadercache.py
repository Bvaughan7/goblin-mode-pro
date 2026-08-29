"""Shader pre-warm: force Steam's downloaded Fossilize shader-cache archive
for a game through ``fossilize_replay`` immediately, instead of waiting on
Steam's own background scheduler to get to it.

Background: with "Allow background processing of Vulkan shaders" on in
Steam, Steam downloads a per-AppID Fossilize pipeline-cache archive into
``steamapps/shadercache/<appid>/`` and processes it into the driver's own
pipeline cache in the background, on its own schedule - sometimes well after
the game's first launch, which is exactly when the stutter it's meant to
avoid actually happens. ``fossilize_replay`` (shipped inside Steam's Linux
runtime) can be pointed at that same archive directly, which is the whole of
what this module does.

This is unofficial - there's no public API for it, just files and a binary
Steam already ships. It's a no-op, not an error, whenever nothing has been
downloaded yet (the game was never installed via Steam, background
processing is off, or Valve hasn't published a cache for it) - never treat a
False return here as something gone wrong.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from goblinmode.proton import STEAM_ROOTS

log = logging.getLogger(__name__)


def _steam_root() -> Path | None:
    for p in STEAM_ROOTS:
        if p.is_dir():
            return p
    return None


def _fossilize_replay() -> str | None:
    root = _steam_root()
    if root is not None:
        for candidate in root.glob("*/fossilize_replay"):
            if candidate.is_file() and candidate.stat().st_mode & 0o111:
                return str(candidate)
    return shutil.which("fossilize_replay")


def _shader_archives(steam_app_id: str) -> list[Path]:
    root = _steam_root()
    if root is None or not steam_app_id:
        return []
    cache_dir = root / "steamapps" / "shadercache" / steam_app_id
    if not cache_dir.is_dir():
        return []
    return sorted(cache_dir.rglob("*.foz"))


def prewarm_shader_cache(steam_app_id: str, timeout: int = 120) -> tuple[bool, str]:
    """Best-effort: replay whatever Fossilize archive Steam has already
    downloaded for ``steam_app_id`` into the driver's pipeline cache right
    now. Returns ``(ok, message)`` - ``ok=False`` covers both "nothing to do"
    and a real failure, distinguished only by the message, since neither is
    actionable by the caller."""
    if not steam_app_id:
        return False, "no Steam AppID on this profile"
    replay = _fossilize_replay()
    if not replay:
        return False, "fossilize_replay not found (no Steam install detected)"
    archives = _shader_archives(steam_app_id)
    if not archives:
        return False, "no downloaded shader-cache archive for this AppID yet"
    try:
        cp = subprocess.run(
            [replay, "--num-threads", "2", *[str(a) for a in archives]],
            capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("fossilize_replay failed for AppID %s: %s", steam_app_id, exc)
        return False, str(exc)
    if cp.returncode != 0:
        log.warning("fossilize_replay exited %d for AppID %s: %s",
                    cp.returncode, steam_app_id, cp.stderr[:500])
        return False, f"fossilize_replay exited {cp.returncode}"
    log.info("pre-warmed %d shader-cache archive(s) for AppID %s", len(archives), steam_app_id)
    return True, f"replayed {len(archives)} archive(s)"
