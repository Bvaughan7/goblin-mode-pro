"""Filesystem locations used across the daemon, helper and GUI.

Everything is derived from the XDG base directory spec so the daemon (a systemd
*user* service), the GUI and the ``goblin-run`` wrapper all agree on where state
lives without any of them hard-coding ``/home/<user>``.
"""

from __future__ import annotations

import os
from pathlib import Path

APP_DIRNAME = "goblin-mode-pro"


def _xdg(env: str, default: Path) -> Path:
    raw = os.environ.get(env, "").strip()
    base = Path(raw).expanduser() if raw else default
    return base / APP_DIRNAME


HOME = Path.home()

CONFIG_DIR = _xdg("XDG_CONFIG_HOME", HOME / ".config")
STATE_DIR = _xdg("XDG_STATE_HOME", HOME / ".local" / "state")
DATA_DIR = _xdg("XDG_DATA_HOME", HOME / ".local" / "share")
CACHE_DIR = _xdg("XDG_CACHE_HOME", HOME / ".cache")

CONFIG_FILE = CONFIG_DIR / "config.json"

# Where the runner wrapper tees Wine/Proton stderr, and where the log watcher
# tails from.
GAME_LOG_DIR = DATA_DIR / "logs"
INCIDENT_FILE = DATA_DIR / "incidents.jsonl"

# Per-game session summaries for regression tracking (goblinmode.sessions).
SESSION_FILE = DATA_DIR / "sessions.jsonl"

# MangoHud CSV frame logs (the FPS watchdog tails the newest one here).
MANGOHUD_LOG_DIR = DATA_DIR / "mangohud"

# Written by payload.py so revert knows exactly what to undo on the user side
# (the privileged snapshot lives in the helper's runtime dir instead).
APPLIED_STATE_FILE = STATE_DIR / "applied.json"
#: touched once the first-run wizard has been completed / skipped
ONBOARDED_MARKER = STATE_DIR / "onboarded"

# MangoHud (not namespaced under APP_DIRNAME - it is MangoHud's own location).
MANGOHUD_DIR = (
    Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config")).expanduser()
    / "MangoHud"
)
MANGOHUD_CONF = MANGOHUD_DIR / "MangoHud.conf"

# Generated launch wrapper.
LOCAL_BIN = HOME / ".local" / "bin"
RUNNER_WRAPPER = LOCAL_BIN / "goblin-run"

# Helper runtime state (root-owned, tmpfs).
HELPER_RUNTIME_DIR = Path("/run") / APP_DIRNAME
HELPER_STATE_FILE = HELPER_RUNTIME_DIR / "state.json"


def ensure_user_dirs() -> None:
    """Create the user-writable directories the daemon/GUI need."""
    for path in (
        CONFIG_DIR,
        STATE_DIR,
        DATA_DIR,
        GAME_LOG_DIR,
        MANGOHUD_LOG_DIR,
        MANGOHUD_DIR,
        LOCAL_BIN,
    ):
        path.mkdir(parents=True, exist_ok=True)
