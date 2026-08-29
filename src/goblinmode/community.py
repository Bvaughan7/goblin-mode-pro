"""Community profile sync.

A small, server-less way to share known-good per-game settings: profile JSON
files live in the project repo under ``profiles/`` and are fetched over HTTPS
straight from ``raw.githubusercontent.com``. There is no account, no telemetry
and no write path -- this module only ever does an anonymous GET, and only from
that one host.

The fetched JSON is filtered to the shareable profile fields and handed back for
the GUI to preview; nothing is applied without the user confirming, and the
daemon re-validates every field through :class:`~goblinmode.config.GameProfile`
before it is saved.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

log = logging.getLogger(__name__)

#: the only host this module will talk to
_ALLOWED_HOST = "raw.githubusercontent.com"
_BASE = f"https://{_ALLOWED_HOST}/Bvaughan7/goblin-mode-pro/main/profiles"
_TIMEOUT = 6
_MAX_BYTES = 64 * 1024
_UA = "goblin-mode-pro/community-sync"

#: fields a community profile is allowed to carry (mirrors page_games._SHARE_KEYS)
SHAREABLE = {
    "exe", "display_name", "match_mode", "renice_enabled", "nice_value",
    "core_pin", "tearing_enabled", "adaptive_sync_enabled", "governor_boost",
    "focus_mode", "power_limit_enabled", "pl1_w", "pl2_w", "per_game_mangohud",
    "mangohud", "fps_watchdog", "fps_dip_floor", "fps_dip_ratio", "runner_vars",
    "gamescope_enabled", "gamescope", "note",
}


class CommunityError(RuntimeError):
    pass


def _get(url: str) -> bytes:
    if not url.startswith(_BASE + "/"):
        raise CommunityError("refusing to fetch outside the profiles directory")
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:  # noqa: S310 - host pinned above
            if resp.status != 200:
                raise CommunityError(f"HTTP {resp.status}")
            if resp.url.split("/")[2] != _ALLOWED_HOST:
                raise CommunityError("redirected off the allowed host")
            return resp.read(_MAX_BYTES + 1)
    except (urllib.error.URLError, OSError) as exc:
        raise CommunityError(str(exc)) from exc


def _get_json(url: str):
    raw = _get(url)
    if len(raw) > _MAX_BYTES:
        raise CommunityError("response too large")
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise CommunityError(f"not valid JSON: {exc}") from exc


def _safe_slug(slug: str) -> str:
    s = "".join(c for c in slug if c.isalnum() or c in "._-")[:64]
    if not s or ".." in s or s[0] in ".-":
        raise CommunityError("bad profile id")
    return s


def fetch_index() -> list[dict]:
    """The catalogue: ``[{"slug", "exe", "display_name", "note"}, ...]``."""
    data = _get_json(f"{_BASE}/index.json")
    if not isinstance(data, list):
        raise CommunityError("index is not a list")
    out = []
    for entry in data:
        if isinstance(entry, dict) and entry.get("slug") and entry.get("exe"):
            out.append({
                "slug": _safe_slug(str(entry["slug"])),
                "exe": str(entry["exe"])[:128],
                "display_name": str(entry.get("display_name") or entry["exe"])[:200],
                "note": str(entry.get("note") or "")[:280],
            })
    return out


def fetch_profile(slug: str) -> dict:
    """One community profile, filtered to the shareable fields."""
    data = _get_json(f"{_BASE}/{_safe_slug(slug)}.json")
    if not isinstance(data, dict) or not data.get("exe"):
        raise CommunityError("profile has no exe")
    return {k: v for k, v in data.items() if k in SHAREABLE}
