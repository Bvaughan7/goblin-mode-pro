"""Detect a running game without a hardcoded executable list.

A scored signal stack, most reliable first:

1. **Launcher wrappers** - Steam writes ``reaper SteamLaunch AppId=<N>`` into the
   process command line (name looked up from ``appmanifest_<N>.acf``); Lutris
   writes ``lutris-wrapper <name> …``; Heroic leaves its own trail.
2. **DRM fdinfo** - ``/proc/<pid>/fdinfo/*`` reports active render-engine time or
   held VRAM for any GPU client (AMD, Intel, NVIDIA with a recent driver).
   Compositors also hold a DRM fd, so only *active rendering* counts.
3. **Linked libraries** - ``/proc/<pid>/maps`` contains ``libSDL2`` / ``libwine``
   / VKD3D / DXVK.
4. **Blocklist** - browsers, editors, terminals, the desktop environment.

A launcher-tagged process scoring at/above :data:`GAME_SCORE`, or a generic
process corroborated by two independent signals, is treated as a game.
"""

from __future__ import annotations

import glob
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import psutil

log = logging.getLogger(__name__)

GAME_SCORE = 5

_STEAM_ROOTS = [
    Path.home() / ".steam/steam/steamapps",
    Path.home() / ".local/share/Steam/steamapps",
    Path.home() / ".var/app/com.valvesoftware.Steam/data/Steam/steamapps",
]

# exact process names that are never games
_BLOCKLIST = {
    "firefox", "chrome", "chromium", "brave", "code", "electron", "obs",
    "discord", "spotify", "steam", "steamwebhelper", "lutris", "heroic",
    "bottles", "telegram-desktop", "thunderbird", "blender", "gimp",
    "python3", "python", "gjs", "node", "nautilus", "dolphin", "konsole",
    "alacritty", "kitty", "wezterm-gui", "ghostty", "Xorg", "Xwayland",
    "pipewire", "wireplumber", "pulseaudio", "systemd", "dbus-daemon",
}
# name/exe substrings that mark a desktop-environment / system process
_BLOCK_STEMS = (
    "kwin", "plasma", "startplasma", "kded", "kactivity", "ksmserver",
    "org_kde", "org.kde", "kaccess", "kwalletd", "kiod", "kioworker",
    "krunner", "kdeconnect", "kglobalaccel", "polkit", "xdg-desktop-portal",
    "xdg-document", "xdg-permission", "gmenudbus", "xembed", "baloo",
    "tracker-", "gnome-shell", "gnome-session", "mutter", "gsd-", "gvfs",
    "gdm", "sddm", "packagekit", "flatpak", "fwupd", "colord", "geoclue",
    "-portal", "greetd", "waybar", "swaync", "hyprpaper", "goblin-mode",
)

# wine/proton scaffolding - matched, but never chosen as "the game" pid
_WINE_INFRA = {
    "wine", "wine64", "wineserver", "wine-preloader", "wine64-preloader",
    "start.exe", "services.exe", "explorer.exe", "rpcss.exe", "plugplay.exe",
    "winedevice.exe", "conhost.exe", "svchost.exe", "tabtip.exe", "xalia.exe",
    "steam.exe", "steamwebhelper.exe", "gameoverlayui.exe", "iexplore.exe",
    "proton", "python3", "pv-bwrap", "srt-bwrap", "reaper", "steam-runtime-launcher-service",
    "wineboot.exe", "rundll32.exe", "Agent.exe", "Battle.net.exe",
    "Battle.net Helper.exe", "crashpad_handler", "SteamLaunch",
}

# libraries that actually mark a game / game runtime (NOT libGL/libEGL/libvulkan
# alone - modern KDE/GTK link those)
_LIB_HINTS = ("libSDL2-", "libSDL3", "libwine.so", "libopenxr", "steamclient.so",
              "libvkd3d", "libdxvk", "libFAudio")


@dataclass
class GameCandidate:
    pid: int
    exe: str                      # basename
    display_name: str
    score: int
    source: str                   # steam | lutris | heroic | generic
    cmdline: list[str] = field(default_factory=list)
    app_id: str = ""


# --------------------------------------------------------------------------
# launcher parsing
# --------------------------------------------------------------------------
def _steam_app_name(appid: str) -> str | None:
    for root in _STEAM_ROOTS:
        acf = root / f"appmanifest_{appid}.acf"
        try:
            txt = acf.read_text(errors="replace")
        except OSError:
            continue
        m = re.search(r'"name"\s*"([^"]+)"', txt)
        if m:
            return m.group(1)
    return None


def _steam_appid_from_cmd(cmd: str) -> str | None:
    m = re.search(r"SteamLaunch\s+AppId=(\d+)", cmd)
    if m:
        return m.group(1)
    m = re.search(r"AppId=(\d+)", cmd)
    return m.group(1) if m and "steam" in cmd.lower() else None


def _lutris_name_from_cmd(cmd: str) -> str | None:
    m = re.search(r"lutris-wrapper[\"']?\s+[\"']?([^\"'\s]+(?:\s+[^\"'\s0-9][^\"'\s]*)?)", cmd)
    if m:
        return m.group(1).strip().strip('"').strip("'")
    return None


def _win_basename(s: str) -> str:
    if not s:
        return ""
    s = s.strip().strip('"').strip("'")
    for sep in ("\\", "/"):
        if sep in s:
            s = s.rsplit(sep, 1)[-1]
    return s


# --------------------------------------------------------------------------
# per-process signals
# --------------------------------------------------------------------------
def _gpu_load(pid: int) -> int:
    """0 = no GPU client, 1 = holds a DRM fd, 2 = actively rendering (engine time
    accumulating) or holding >64 MB of VRAM. Compositors/Xwayland land at 1."""
    level = 0
    for fdinfo in glob.glob(f"/proc/{pid}/fdinfo/*"):
        try:
            with open(fdinfo, "r", errors="ignore") as fh:
                blob = fh.read(8192)
        except OSError:
            continue
        if "drm-driver" not in blob:
            continue
        level = max(level, 1)
        if re.search(r"drm-engine-(render|gfx|compute|3d)\w*:\s*[1-9]\d{6,}", blob):
            return 2
        mm = re.search(r"drm-(total|resident)-(vram|memory|local)\w*:\s*(\d+)", blob)
        if mm and int(mm.group(3)) > 64 * 1024 * 1024:
            return 2
    return level


def _links_game_libs(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/maps", "r", errors="ignore") as fh:
            for line in fh:
                if any(h in line for h in _LIB_HINTS):
                    return True
    except OSError:
        pass
    return False


# --------------------------------------------------------------------------
# main entry
# --------------------------------------------------------------------------
def _blocked(name: str, base: str) -> bool:
    n, b = name.lower(), base.lower()
    if n in _BLOCKLIST or b in _BLOCKLIST:
        return True
    return any(s in n or s in b for s in _BLOCK_STEMS)


def _score(name: str, exe: str, cmd: str, cmd_list: list[str], pid: int):
    """Return (score, source, display_name, app_id) for a single process."""
    base = (_win_basename(exe) or name)
    if _blocked(name, base):
        return None

    score, source, display, appid = 0, "generic", base, ""

    appid = _steam_appid_from_cmd(cmd) or ""
    if appid:
        score += 5
        source = "steam"
        display = _steam_app_name(appid) or f"Steam app {appid}"
    lname = _lutris_name_from_cmd(cmd)
    if lname:
        score += 5
        source = "lutris"
        display = lname
    if re.search(r"\bheroic\b|legendary --|gogdl |/nile ", cmd):
        score += 4
        source = "heroic"

    infra = base.lower() in _WINE_INFRA or name.lower() in _WINE_INFRA
    if not infra:
        gpu = _gpu_load(pid)
        score += {0: 0, 1: 0, 2: 3}[gpu]      # only *active* rendering counts
        if _links_game_libs(pid):
            score += 2

    try:
        rss = psutil.Process(pid).memory_info().rss
        if rss > 700 * 1024 * 1024:
            score += 1
    except (psutil.Error, OSError):
        pass

    # a generic (no-launcher) hit needs corroboration from two independent signals
    if source == "generic" and score < 6:
        return None
    return score, source, display, appid


def detect_games(
    min_score: int = GAME_SCORE,
    procs: list["psutil.Process"] | None = None,
) -> list[GameCandidate]:
    """One sweep of the process table -> scored game candidates.

    Pass *procs* (from a ``psutil.process_iter`` the caller already did) to avoid
    a second full walk. When a launcher tags a whole Proton tree, the reported
    pid is the fattest non-infrastructure descendant, not the wrapper.
    """
    if procs is None:
        procs = list(psutil.process_iter(["pid", "name", "exe", "cmdline", "ppid"]))
    by_pid = {p.info["pid"]: p for p in procs}

    hits: dict[int, GameCandidate] = {}
    for p in procs:
        info = p.info
        pid = info["pid"]
        name = info.get("name") or ""
        exe = info.get("exe") or ""
        cmd_list = info.get("cmdline") or []
        cmd = " ".join(cmd_list)

        scored = _score(name, exe, cmd, cmd_list, pid)
        if not scored:
            continue
        score, source, display, appid = scored
        if score < min_score:
            continue

        target_pid, target_exe = _pick_real_pid(pid, by_pid)
        hits[target_pid] = GameCandidate(
            pid=target_pid, exe=target_exe, display_name=display,
            score=score, source=source, cmdline=cmd_list, app_id=appid,
        )
    return list(hits.values())


def _pick_real_pid(pid: int, by_pid: dict) -> tuple[int, str]:
    """Walk descendants; return the fattest non-infra process (the game itself)."""
    root = by_pid.get(pid)
    if root is None:
        return pid, ""
    stack = [root]
    seen = set()
    best_pid, best_exe, best_rss = pid, "", -1
    children = {}
    for p in by_pid.values():
        children.setdefault(p.info.get("ppid"), []).append(p)
    while stack:
        cur = stack.pop()
        cpid = cur.info["pid"]
        if cpid in seen:
            continue
        seen.add(cpid)
        stack.extend(children.get(cpid, []))
        cname = cur.info.get("name") or ""
        cbase = (_win_basename(cur.info.get("exe") or "") or cname)
        if cbase.lower() in _WINE_INFRA or cname.lower() in _WINE_INFRA:
            continue
        try:
            rss = cur.memory_info().rss
        except (psutil.Error, OSError):
            rss = 0
        if rss > best_rss:
            best_pid, best_exe, best_rss = cpid, cbase, rss
    return best_pid, best_exe or (by_pid[pid].info.get("name") or "")
