"""Read-only lookups against a couple of public Linux-gaming datasets.

* **ProtonDB** - a game's community compatibility tier (Platinum / Gold / …).
* **AreWeAntiCheatYet** - whether a game's anti-cheat works on Linux.

Both are anonymous HTTPS GETs to a fixed host allowlist, run in the **GUI**
process (never the daemon), size-capped, and cached to disk so we don't hammer
either service. Nothing is uploaded.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from pathlib import Path

from goblinmode.paths import CACHE_DIR

log = logging.getLogger(__name__)

_ALLOWED_HOSTS = {"www.protondb.com", "raw.githubusercontent.com"}
_UA = "goblin-mode-pro/webdata"
_TIMEOUT = 8

_PROTONDB = "https://www.protondb.com/api/v1/reports/summaries/{appid}.json"
_AWACY = ("https://raw.githubusercontent.com/AreWeAntiCheatYet/"
          "AreWeAntiCheatYet/HEAD/games.json")

_ANTICHEAT_CACHE = CACHE_DIR / "anticheat.json"
_ANTICHEAT_TTL = 24 * 3600
_PROTONDB_TTL = 6 * 3600


class WebDataError(RuntimeError):
    pass


def _get(url: str, *, max_bytes: int) -> bytes:
    host = url.split("/", 3)[2] if "://" in url else ""
    if host not in _ALLOWED_HOSTS or not url.startswith("https://"):
        raise WebDataError(f"host not allowed: {host}")
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:  # noqa: S310
            if resp.status != 200:
                raise WebDataError(f"HTTP {resp.status}")
            if resp.url.split("/", 3)[2] not in _ALLOWED_HOSTS:
                raise WebDataError("redirected off the allowed hosts")
            data = resp.read(max_bytes + 1)
    except (urllib.error.URLError, OSError) as exc:
        raise WebDataError(str(exc)) from exc
    if len(data) > max_bytes:
        raise WebDataError("response too large")
    return data


def _cached_json(path: Path, ttl: int):
    try:
        if path.exists() and time.time() - path.stat().st_mtime < ttl:
            return json.loads(path.read_text())
    except (OSError, ValueError):
        pass
    return None


# --------------------------------------------------------------------------
# ProtonDB
# --------------------------------------------------------------------------
def protondb_tier(app_id: str) -> dict:
    """``{tier, score, total, confidence, trendingTier}`` for a Steam AppID."""
    app_id = "".join(c for c in str(app_id) if c.isdigit())[:12]
    if not app_id:
        raise WebDataError("no Steam AppID")
    cache = CACHE_DIR / f"protondb-{app_id}.json"
    hit = _cached_json(cache, _PROTONDB_TTL)
    if hit is not None:
        return hit
    raw = _get(_PROTONDB.format(appid=app_id), max_bytes=8 * 1024)
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise WebDataError(f"bad JSON: {exc}") from exc
    if not isinstance(data, dict) or "tier" not in data:
        raise WebDataError("game not on ProtonDB")
    out = {k: data.get(k) for k in
           ("tier", "score", "total", "confidence", "trendingTier", "bestReportedTier")}
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(out))
    except OSError:
        pass
    return out


# --------------------------------------------------------------------------
# AreWeAntiCheatYet
# --------------------------------------------------------------------------
_STATUS_RANK = {  # worst first for the "is it playable?" read
    "denied": 0, "broken": 1, "unknown": 2, "planned": 3,
    "running": 4, "supported": 5,
}


def _anticheat_db() -> list[dict]:
    hit = _cached_json(_ANTICHEAT_CACHE, _ANTICHEAT_TTL)
    if hit is not None:
        return hit
    raw = _get(_AWACY, max_bytes=4 * 1024 * 1024)
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise WebDataError(f"bad JSON: {exc}") from exc
    if not isinstance(data, list):
        raise WebDataError("unexpected AWACY payload")
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _ANTICHEAT_CACHE.write_text(json.dumps(data))
    except OSError:
        pass
    return data


def anticheat_status(name: str = "", app_id: str = "") -> dict | None:
    """Look a game up in AreWeAntiCheatYet by AppID (preferred) or name.

    Returns ``{name, status, anticheats, reference}`` or ``None`` if not listed.
    """
    app_id = "".join(c for c in str(app_id) if c.isdigit())
    nlow = name.strip().lower()
    if not app_id and not nlow:
        return None
    for game in _anticheat_db():
        if not isinstance(game, dict):
            continue
        store = game.get("storeIds") or {}
        if app_id and str(store.get("steam") or "").strip() == app_id:
            pass
        elif nlow and str(game.get("name") or "").strip().lower() == nlow:
            pass
        else:
            continue
        acs = game.get("anticheats") or []
        return {
            "name": game.get("name") or name,
            "status": str(game.get("status") or "Unknown"),
            "anticheats": [a for a in acs if isinstance(a, str)][:6],
            "reference": game.get("reference") or "",
        }
    return None
