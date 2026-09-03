"""Persistent configuration: global settings + per-game profiles.

Stored as JSON (not GSettings) so the daemon, the GUI and the ``goblin-run``
wrapper script can all read it with nothing but the standard library. Writes are
atomic (temp file + ``os.replace``).
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from goblinmode.paths import CONFIG_FILE, ensure_user_dirs

SCHEMA_VERSION = 1

#: An ``exe`` may hold an exact name, a substring, or (match_mode="regex") a
#: pattern, so metacharacters are allowed - but never a path separator, ``..``,
#: NUL or a control character, and length is bounded (regex ReDoS guard).
#: Callers must pass a plain name; ``\`` / ``/`` paths are the caller's job to
#: split (see ``_win_basename`` in observer/gamedetect).
#:
#: One consequence worth knowing, because it is not obvious from the rule: since
#: a backslash is rejected, a ``match_mode="regex"`` pattern can contain no
#: escape sequence at all - no ``\.``, no ``\d``, no backreference. So a regex
#: cannot express "a literal dot", and the pattern ``Wow.exe`` also matches
#: ``WowXexe``. That is a real limit of the regex mode, not an oversight in the
#: pattern: allowing backslashes here would let a profile name a path.
_EXE_BAD = re.compile(r"[/\\\x00-\x1f\x7f]|\.\.")


def sanitize_exe(value: str) -> str:
    """Validate a profile's ``exe`` token, or raise ``ValueError``."""
    value = (value or "").strip().strip("\"'")
    if not value or len(value) > 128 or value in (".", "..") or _EXE_BAD.search(value):
        raise ValueError(f"invalid game executable name: {value!r}")
    return value


def slug(value: str) -> str:
    """A filesystem-safe token derived from a name (per-game config files)."""
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-")
    return s[:80] or "game"


# The five runner-variable toggles from the project brief. Value = the env
# assignment applied when the toggle is on.
RUNNER_VARS: dict[str, dict[str, str]] = {
    "nvapi": {"PROTON_ENABLE_NVAPI": "1", "DXVK_ENABLE_NVAPI": "1"},
    "fsync": {"WINEFSYNC": "1"},
    "no_esync": {"PROTON_NO_ESYNC": "1"},
    "dxvk_async": {"DXVK_ASYNC": "1"},
}

#: Vendor-specific graphics-driver tuning env vars. Keyed by vendor so the GUI
#: only shows what applies. RADV_PERFTEST values are comma-joined if several are
#: on (see ``env_assignments``).
GPU_TUNING_VARS: dict[str, dict[str, tuple[str, dict[str, str]]]] = {
    "nvidia": {
        "threaded_gl": ("Threaded GL optimizations",
                        {"__GL_THREADED_OPTIMIZATIONS": "1"}),
        "shader_cache": ("Unlimited shader disk cache",
                         {"__GL_SHADER_DISK_CACHE": "1",
                          "__GL_SHADER_DISK_CACHE_SKIP_CLEANUP": "1"}),
        "force_gsync": ("Force G-SYNC / VRR compatible",
                        {"__GL_GSYNC_ALLOWED": "1", "__GL_VRR_ALLOWED": "1"}),
        "max_fps_none": ("No driver frame cap",
                         {"__GL_SYNC_TO_VBLANK": "0"}),
    },
    "amd": {
        "glthread": ("Mesa glthread (extra CPU thread for GL)",
                     {"mesa_glthread": "true"}),
        "radv_gpl": ("RADV pipeline library — faster shader compile",
                     {"RADV_PERFTEST": "gpl"}),
        "radv_nggc": ("RADV NGG culling",
                      {"RADV_PERFTEST": "nggc"}),
        "radv_rt": ("RADV ray-tracing (experimental)",
                    {"RADV_PERFTEST": "rt"}),
    },
    "intel": {
        "anv_gpl": ("ANV pipeline library",
                    {"ANV_GPL": "true"}),
        "glthread": ("Mesa glthread",
                     {"mesa_glthread": "true"}),
    },
}

# MangoHud toggle key -> the MangoHud.conf token(s) it controls.
MANGOHUD_TOGGLES: dict[str, tuple[str, ...]] = {
    "enabled": ("no_display",),  # inverted: enabled -> no_display=0
    "fps": ("fps",),
    "cpu_temp": ("cpu_temp",),
    "gpu_temp": ("gpu_temp",),
    "ram": ("ram",),
    "frame_timing": ("frame_timing",),
}

MATCH_MODES = ("exact", "substring", "regex")


def _default_mangohud() -> dict[str, bool]:
    return {
        "enabled": False,
        "fps": True,
        "cpu_temp": True,
        "gpu_temp": True,
        "ram": False,
        "frame_timing": False,
    }


def _default_runner_vars() -> dict[str, bool]:
    # dxvk_async off by default: upstream DXVK dropped the async patch years
    # ago, so DXVK_ASYNC=1 is a no-op on stock DXVK and only does anything on
    # the dxvk-async / gplasync forks (Proton-GE moved to
    # DXVK_GPLASYNCCACHE). Leaving it on just sets a dead env var.
    return {"nvapi": True, "fsync": True, "no_esync": False, "dxvk_async": False}


GAMESCOPE_UPSCALERS = ("off", "fsr", "nis", "integer")

#: CPU-affinity modes for a game's process tree. "performance" = the fast cores
#: on a hybrid CPU; "cache0" = the first L3 domain (one CCD on Ryzen).
CORE_PIN_MODES = ("off", "performance", "cache0")

#: A sched_ext scheduler's short name must look like one. The value ends up in
#: a D-Bus call to a *root* service and a profile can arrive from an imported
#: file or a community fetch, so it is validated at this boundary rather than
#: trusted - but by *shape*, not against a fixed list: new scx schedulers ship
#: regularly and an allowlist here would silently reject them the day they
#: appear. Whether the name is a scheduler this machine actually has is
#: settled at switch time, against scx_loader's own SupportedSchedulers, and a
#: name that isn't raises a visible incident instead of failing quietly.
SCX_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,31}\Z")
#: scx_loader's tuning modes - see goblinmode.scx.SCHED_MODES
SCX_MODES = ("auto", "gaming", "lowlatency", "powersave", "server")


def _default_gamescope() -> dict:
    return {"w": 0, "h": 0, "refresh": 0, "upscale": "off", "hdr": False,
            "borderless": True, "steam_overlay": True}


@dataclass
class GameProfile:
    exe: str
    display_name: str = ""
    enabled: bool = True
    match_mode: str = "exact"  # exact | substring | regex (matched on basename)

    # Phase B - The Performance Payload
    auto_created: bool = False   # added by the game auto-detector, not the user
    renice_enabled: bool = True
    nice_value: int = -5
    # Wrap the game with `gamemoderun` in the launch wrapper (when gamemode is
    # installed). Default on. Turn off to resolve the GameMode <-> ananicy-cpp
    # niceness conflict without uninstalling gamemode.
    use_gamemode: bool = True
    core_pin: str = "off"
    #: sched_ext scheduler to run while this game is up, short name
    #: ("lavd", "bpfland", ...). Empty = leave the kernel's scheduler alone,
    #: which is the default: swapping the system scheduler is a bigger lever
    #: than anything else here and should be opted into per game.
    scx_scheduler: str = ""
    #: which of scx_loader's tuning modes to ask for (see scx.SCHED_MODES)
    scx_mode: str = "gaming"        # off | performance | cache0  (see CORE_PIN_MODES)
    tearing_enabled: bool = True
    # Cap the internal panel's refresh rate for this game (Hz); 0 = leave it
    # alone. Mainly for handhelds (Deck 40/50/60, Ally up to 120...) where a
    # lower refresh rate trades smoothness for battery life on a per-game
    # basis. No-ops if no matching mode is advertised - see compositor.py.
    refresh_rate_hz: int = 0
    adaptive_sync_enabled: bool = False
    # Which output(s) to enable VRR on; empty = every VRR-capable output (the
    # old behavior). KDE only - see compositor.py's module docstring for why
    # Hyprland has no per-output equivalent to restrict to.
    vrr_outputs: list[str] = field(default_factory=list)
    governor_boost: bool = True
    focus_mode: bool = False   # quiet the desktop: pause the file indexer, DND, inhibit idle

    # Optional RAPL power-limit override (watts). 0 = leave the firmware value.
    power_limit_enabled: bool = False
    pl1_w: int = 0
    pl2_w: int = 0
    # On-battery override (watts); 0 = use pl1_w/pl2_w unchanged on battery too.
    # Only consulted when the machine reports it's actually on battery.
    battery_pl1_w: int = 0
    battery_pl2_w: int = 0
    # Re-apply the user's /etc/intel-undervolt.conf offsets on launch (suspend
    # and thermald can reset them). We never choose the values.
    undervolt_reapply: bool = False
    # Same idea for AMD: re-apply Curve Optimizer offsets from
    # /etc/goblin-mode-pro/amd-undervolt.conf (the user's own file - GMP
    # never chooses these either).
    amd_undervolt_reapply: bool = False
    # Preemptive fan spin-up on launch, where the EC exposes a writable pwm
    # control (most don't - see capabilities.py's "fan_control" probe).
    fan_spinup_enabled: bool = False

    # Phase C - MangoHud
    per_game_mangohud: bool = False
    mangohud: dict[str, bool] = field(default_factory=_default_mangohud)

    # Frame-rate watchdog: log FPS via MangoHud and raise an incident on a
    # sustained extreme dip (captures deep GPU state - VRAM, PCIe link, clocks).
    fps_watchdog: bool = False
    fps_dip_floor: int = 22        # fps at/below this is a dip regardless of baseline
    fps_dip_ratio: float = 0.5     # ...or below this fraction of the recent median
    #: keep a 30 s replay buffer running (gpu-screen-recorder) and save a clip
    #: when the watchdog fires or a GPU fault shows up - footage for a bug report
    clip_on_incident: bool = False

    # Runner env vars (Proton/Wine)
    runner_vars: dict[str, bool] = field(default_factory=_default_runner_vars)

    # gamescope: a micro-compositor that gives a rock-solid frame limiter,
    # FSR/NIS upscaling and clean alt-tab. Off by default.
    gamescope_enabled: bool = False
    gamescope: dict = field(default_factory=_default_gamescope)

    # Vendor GPU-driver tuning (see GPU_TUNING_VARS). Flat {key: bool}; the GUI
    # only shows keys for the detected GPU vendor.
    gpu_tuning: dict[str, bool] = field(default_factory=dict)

    # Steam AppID, if known - powers the ProtonDB / anti-cheat lookups.
    steam_app_id: str = ""
    # Free-form user note (also carried by shared / community profiles).
    notes: str = ""

    def __post_init__(self) -> None:
        self.exe = sanitize_exe(self.exe)
        if not self.display_name:
            self.display_name = self.exe
        self.display_name = self.display_name[:200]
        if self.match_mode not in MATCH_MODES:
            self.match_mode = "exact"
        if self.core_pin not in CORE_PIN_MODES:
            self.core_pin = "off"
        if self.scx_scheduler:
            # accept either "lavd" or "scx_lavd"; store the short form
            name = self.scx_scheduler.strip().removeprefix("scx_")
            if SCX_NAME_RE.match(name):
                self.scx_scheduler = name
            else:
                logging.getLogger(__name__).warning(
                    "invalid sched_ext scheduler name %r - ignoring it",
                    self.scx_scheduler)
                self.scx_scheduler = ""
        if self.scx_mode not in SCX_MODES:
            self.scx_mode = "gaming"
        self.nice_value = max(-10, min(19, int(self.nice_value)))
        self.pl1_w = max(0, min(500, int(self.pl1_w)))
        self.pl2_w = max(0, min(500, int(self.pl2_w)))
        self.battery_pl1_w = max(0, min(500, int(self.battery_pl1_w)))
        self.battery_pl2_w = max(0, min(500, int(self.battery_pl2_w)))
        self.refresh_rate_hz = max(0, min(1000, int(self.refresh_rate_hz)))
        self.fps_dip_floor = max(5, min(120, int(self.fps_dip_floor)))
        self.fps_dip_ratio = max(0.1, min(0.9, float(self.fps_dip_ratio)))
        self.vrr_outputs = [str(o)[:64] for o in (self.vrr_outputs or [])][:16]
        # Fill in any keys added in newer versions.
        for k, v in _default_mangohud().items():
            self.mangohud.setdefault(k, v)
        for k, v in _default_runner_vars().items():
            self.runner_vars.setdefault(k, v)
        for k, v in _default_gamescope().items():
            self.gamescope.setdefault(k, v)
        for k in ("w", "h", "refresh"):
            self.gamescope[k] = max(0, min(10000, int(self.gamescope.get(k, 0) or 0)))
        if self.gamescope.get("upscale") not in GAMESCOPE_UPSCALERS:
            self.gamescope["upscale"] = "off"
        # steam_app_id must be a bare number if set
        self.steam_app_id = re.sub(r"\D", "", str(self.steam_app_id or ""))[:12]
        self.notes = str(self.notes or "")[:500]
        self.gpu_tuning = {k: bool(v) for k, v in dict(self.gpu_tuning or {}).items()
                           if isinstance(k, str) and len(k) < 40}

    def env_assignments(self) -> dict[str, str]:
        """Resolve the enabled runner + GPU-tuning toggles into concrete env vars."""
        out: dict[str, str] = {}
        for key, on in self.runner_vars.items():
            if on and key in RUNNER_VARS:
                out.update(RUNNER_VARS[key])
        # GPU tuning: RADV_PERFTEST is a comma-list, so collect then join.
        radv: set[str] = set()
        for vendor in GPU_TUNING_VARS.values():
            for key, (_label, env) in vendor.items():
                if not self.gpu_tuning.get(key):
                    continue
                for var, val in env.items():
                    if var == "RADV_PERFTEST":
                        radv.add(val)
                    else:
                        out[var] = val
        if radv:
            out["RADV_PERFTEST"] = ",".join(sorted(radv))
        return out


@dataclass
class Settings:
    schema_version: int = SCHEMA_VERSION
    master_enabled: bool = True
    poll_interval: int = 7  # seconds; brief calls for 5-10
    diagnostics_enabled: bool = True
    diagnostics_sample_interval: float = 1.0
    llm_model_hint: str = ""  # free-form note included in the export payload
    auto_detect: bool = True                       # detect any game, not just the profile list
    ignored_games: list[str] = field(default_factory=list)  # exe names the user said "ignore"
    prometheus_textfile: str = ""  # path to write a node_exporter textfile collector .prom; "" = off
    profiles: list[GameProfile] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.poll_interval = max(3, min(30, int(self.poll_interval)))
        self.profiles = [
            p if isinstance(p, GameProfile) else GameProfile(**p)
            for p in self.profiles
        ]

    # -- lookup -----------------------------------------------------------
    def profile_for_exe(self, exe: str) -> GameProfile | None:
        for p in self.profiles:
            if p.exe == exe:
                return p
        return None

    def enabled_profiles(self) -> list[GameProfile]:
        if not self.master_enabled:
            return []
        return [p for p in self.profiles if p.enabled]


def new_profile(exe: str, display_name: str = "", *, auto_created: bool = False,
                handheld: str = "") -> GameProfile:
    """A fresh profile with sensible defaults. On a handheld (``handheld`` is
    the detected model - "steamdeck" / "rog_ally" / "legion_go" /
    "other_handheld"), gamescope is on (fixed panel), the power-limit section
    starts enabled with that model's starter TDP preset instead of one
    generic value, and the FPS-dip floor is lower."""
    p = GameProfile(
        exe=exe, display_name=display_name or exe, auto_created=auto_created,
        match_mode="exact" if exe.lower().endswith(".exe") else "substring",
    )

    # ananicy-cpp (CachyOS default) is itself a niceness manager; a third
    # writer on top of it + GameMode is the exact conflict the CachyOS wiki
    # warns about. Start new profiles with renice off when it's running - the
    # user can still turn it on.
    try:
        from goblinmode.capabilities import ananicy_active

        if ananicy_active():
            p.renice_enabled = False
    except Exception as exc:  # noqa: BLE001 - never let a probe block profile creation
        logging.getLogger(__name__).debug("ananicy probe failed: %s", exc)

    if handheld:
        from goblinmode.capabilities import HANDHELD_TDP_PRESETS, HANDHELD_TDP_PRESETS_BATTERY

        p.gamescope_enabled = True
        p.gamescope["borderless"] = False       # fullscreen on a handheld
        p.power_limit_enabled = True
        pl1, pl2 = HANDHELD_TDP_PRESETS.get(handheld, HANDHELD_TDP_PRESETS["other_handheld"])
        p.pl1_w, p.pl2_w = pl1, pl2
        bpl1, bpl2 = HANDHELD_TDP_PRESETS_BATTERY.get(
            handheld, HANDHELD_TDP_PRESETS_BATTERY["other_handheld"])
        p.battery_pl1_w, p.battery_pl2_w = bpl1, bpl2
        p.fps_dip_floor = 28
    return p


def default_settings() -> Settings:
    """Ship sane defaults for the two games named in the brief."""
    return Settings(
        profiles=[
            GameProfile(
                exe="Wow.exe",
                display_name="World of Warcraft",
                match_mode="exact",
            ),
            GameProfile(
                exe="rs2client",
                display_name="RuneScape (native)",
                match_mode="substring",
                per_game_mangohud=False,
            ),
        ]
    )


# -- persistence --------------------------------------------------------------
def load() -> Settings:
    if not CONFIG_FILE.exists():
        settings = default_settings()
        save(settings)
        return settings
    try:
        raw = json.loads(CONFIG_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return default_settings()
    return _from_dict(raw)


#: What a hand-broken config throws. AttributeError belongs here as much as
#: the other two: a profile whose ``exe`` is a number, or whose ``mangohud``
#: is a list, reaches ``.strip()`` / ``.setdefault()`` on the wrong type. It
#: used to escape _from_dict entirely and take down whatever was loading the
#: file - daemon, GUI and the launch wrapper alike - which is the opposite of
#: what the loop below is for.
_CORRUPT = (ValueError, TypeError, AttributeError)


def _from_dict(raw: dict[str, Any]) -> Settings:
    if not isinstance(raw, dict):
        return default_settings()
    raw = dict(raw)
    raw.pop("schema_version", None)
    raw_profiles = raw.pop("profiles", []) or []
    if not isinstance(raw_profiles, list):
        raw_profiles = []
    try:
        settings = Settings(**{k: v for k, v in raw.items() if k in _SETTINGS_FIELDS})
    except _CORRUPT:
        # A settings value of the wrong shape entirely (poll_interval as a
        # list, say). Losing the global settings is bad; refusing to start is
        # worse, and the profiles below are the part users actually curate.
        settings = Settings()

    profiles: list[GameProfile] = []
    for p in raw_profiles:
        if not isinstance(p, dict):
            continue
        try:
            profiles.append(GameProfile(**{k: v for k, v in p.items() if k in _PROFILE_FIELDS}))
        except _CORRUPT:
            continue  # drop a corrupt / hand-broken entry rather than fail to start
    settings.profiles = profiles
    try:
        settings.__post_init__()
    except _CORRUPT:
        settings = Settings(profiles=profiles)
    return settings


def save(settings: Settings) -> None:
    ensure_user_dirs()
    settings.schema_version = SCHEMA_VERSION
    payload = asdict(settings)
    fd, tmp = tempfile.mkstemp(
        dir=str(CONFIG_FILE.parent), prefix=".config-", suffix=".json"
    )
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, CONFIG_FILE)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


_SETTINGS_FIELDS = {f.name for f in dataclasses.fields(Settings)}
_PROFILE_FIELDS = {f.name for f in dataclasses.fields(GameProfile)}
