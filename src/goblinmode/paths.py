"""Filesystem locations used across the daemon, helper and GUI.

Everything is derived from the XDG base directory spec so the daemon (a systemd
*user* service), the GUI and the ``goblin-run`` wrapper all agree on where state
lives without any of them hard-coding ``/home/<user>``.
"""

from __future__ import annotations

import os
from pathlib import Path

APP_DIRNAME = "goblin-mode-pro"


def _xdg_base(env: str, default: Path) -> Path:
    """The XDG base directory `env` names, or `default`.

    A variable that is set but empty counts as unset, which is what the spec
    says and what an environment that clears a variable rather than unsetting
    it produces. Whitespace-only is treated the same way: it is not a path
    anybody meant, and the alternative is a directory literally named " ".
    """
    raw = os.environ.get(env, "").strip()
    if not raw:
        return default
    # Only this user's own home expands. `~someone` is refused rather than
    # honoured for two reasons: pathlib RAISES for a user that does not exist,
    # and this module is imported by the daemon, the GUI, the CLI and the
    # launch wrapper - so an exotic variable would stop all four from starting
    # rather than being ignored. And pointing an XDG base at another account's
    # home is not something to obey even when the account is real.
    if raw.startswith("~") and raw != "~" and not raw.startswith("~/"):
        return default
    try:
        return Path(raw).expanduser()
    except RuntimeError:
        return default


def _xdg(env: str, default: Path) -> Path:
    return _xdg_base(env, default) / APP_DIRNAME


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
#
# Goes through the same base resolution as everything above. It used to read
# XDG_CONFIG_HOME directly, which meant a variable that was set but EMPTY -
# the spec's way of saying "unset", and what several launchers produce - gave
# `Path("")`, so this became the relative path `MangoHud`. The visible effect
# was that MANGOHUD_CONFIGFILE was exported to the game as a relative path and
# the per-game overlay config silently never applied, plus a stray MangoHud/
# directory created in whatever the daemon's working directory happened to be.
MANGOHUD_DIR = _xdg_base("XDG_CONFIG_HOME", HOME / ".config") / "MangoHud"
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
