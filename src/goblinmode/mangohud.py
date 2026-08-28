"""MangoHud configurator.

Reads and writes ``~/.config/MangoHud/MangoHud.conf`` (or a per-game
``~/.config/MangoHud/<exe>.conf``) as an order-preserving list of lines so any
keys the user set by hand survive a round trip. Goblin Mode Pro only ever
touches the keys it manages and records which ones it added so ``revert`` can
remove exactly those.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from goblinmode.config import GameProfile
from goblinmode.paths import MANGOHUD_CONF, MANGOHUD_DIR, MANGOHUD_LOG_DIR

log = logging.getLogger(__name__)

_MANAGED_KEYS = ("no_display", "fps", "cpu_temp", "gpu_temp", "ram", "frame_timing")

_GMP_BEGIN = "### goblin-mode-pro begin"
_GMP_END = "### goblin-mode-pro end"


@dataclass
class _Conf:
    lines: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "_Conf":
        if not path.exists():
            return cls([])
        return cls(path.read_text().splitlines())

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = "\n".join(self.lines).rstrip("\n") + "\n"
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text)
        tmp.replace(path)

    def strip_gmp_block(self) -> None:
        out: list[str] = []
        inside = False
        for line in self.lines:
            if line.strip() == _GMP_BEGIN:
                inside = True
                continue
            if line.strip() == _GMP_END:
                inside = False
                continue
            if not inside:
                out.append(line)
        self.lines = out

    def set_gmp_block(self, entries: list[str]) -> None:
        self.strip_gmp_block()
        while self.lines and not self.lines[-1].strip():
            self.lines.pop()
        block = [_GMP_BEGIN, *entries, _GMP_END]
        if self.lines:
            self.lines.append("")
        self.lines.extend(block)


def _target_path(profile: GameProfile) -> Path:
    if profile.per_game_mangohud:
        from goblinmode.config import slug

        return MANGOHUD_DIR / f"{slug(profile.exe)}.conf"
    return MANGOHUD_CONF


def _entries_for(profile: GameProfile) -> list[str]:
    m = profile.mangohud
    entries: list[str] = []

    if m.get("enabled"):
        entries.append("no_display=0")
        for key in ("fps", "cpu_temp", "gpu_temp", "ram", "frame_timing"):
            if m.get(key):
                entries.append(key)
    else:
        entries.append("no_display=1")

    # Frame-rate watchdog: continuous CSV logging that goblinmode.fpswatch tails.
    # Works regardless of no_display.
    if profile.fps_watchdog:
        entries += [
            f"output_folder={MANGOHUD_LOG_DIR}",
            "log_interval=200",
            "autostart_log=1",
            "log_duration=0",
        ]

    # MangoHud only reads its config at launch. Pin the in-game hotkeys so the
    # user always has a live escape hatch for changes made mid-session.
    if profile.mangohud.get("enabled") or profile.fps_watchdog:
        entries += [
            "toggle_hud=Shift_R+F12",
            "toggle_logging=Shift_L+F2",
            "reload_cfg=Shift_L+F4",
        ]
    return entries


#: in-game keys written into every managed block (shown in the GUI)
HOTKEYS = {
    "Shift_R + F12": "show / hide the overlay",
    "Shift_L + F2": "start / stop the frame-rate log",
    "Shift_L + F4": "reload this config",
}


def apply(profile: GameProfile) -> Path:
    """Write the managed block for *profile*; return the file touched."""
    path = _target_path(profile)
    conf = _Conf.load(path)
    conf.set_gmp_block(_entries_for(profile))
    conf.save(path)
    log.info("MangoHud config updated: %s", path)
    return path


def revert(profile: GameProfile) -> None:
    """Remove only the block Goblin Mode Pro added."""
    for path in {_target_path(profile), MANGOHUD_CONF}:
        if not path.exists():
            continue
        conf = _Conf.load(path)
        before = list(conf.lines)
        conf.strip_gmp_block()
        if conf.lines != before:
            conf.save(path)
            log.info("MangoHud config reverted: %s", path)
