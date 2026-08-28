# Goblin Mode Pro — Handoff

For the next agent/developer picking this up. Read this + `README.md` + the
approved plan at `~/.claude/plans/eventual-drifting-dragonfly.md`.

---

## Status: all 5 brief phases implemented, not yet installed/run for real

| Phase | State |
|---|---|
| 1 Scaffold | ✅ package tree, `pyproject.toml`, `install.sh`, systemd/polkit/D-Bus data files |
| 2 Observer | ✅ `observer.py` poll loop + per-game state machine + Proton inner-PID pick; `ipc/daemon_bridge.py` session-bus API |
| 3 Tray | ✅ `tray.py` pystray/appindicator, Pillow icon, degrades to headless if pystray missing |
| 4 GUI | ✅ `gui/` — `Adw.PreferencesWindow`, Dashboard/Games/Diagnostics, Cairo Temp-vs-Load graph |
| 5 System execution | ✅ `helper/goblin_helper.py` (root, polkit), `payload.py` orchestrator, `compositor.py`, `mangohud.py`, `runner.py` |
| "Next steps" | ✅ PL1/PL2 raise (UI + helper + payload), Adaptive Sync/VRR via `kscreen-doctor`, KDE/X11 real compositor suspend, graceful non-KDE no-op |

### Verified on this machine (CachyOS, Dell G7, RTX 2060, KDE Wayland)
- `python -m py_compile` clean across `src/` + `helper/` + `tests/`
- **26/26 tests pass** via the local harness (see below — real `pytest` not installed)
- Full GUI **constructs against real GTK4 4.22 / libadwaita 1.9** (headless smoke, no window presented)
- `helper` read fns work: governor `powersave`, RAPL `(107 W, 107 W)`, available governors `{performance, powersave}`
- Daemon boots headless, `get_status()` / `set_profile()` / `force_boost()` round-trip; degrades cleanly with no helper

### NOT yet done (needs a machine session + sudo)
- `./install.sh` has never been run — **first real install is the top priority**
- Helper has never run as a live D-Bus service (polkit path untested end-to-end)
- No real game launch tested
- `pytest` not runnable here (no pip, no `python-pytest`)

---

## Environment facts (already established — don't re-derive)

- **CachyOS** (Arch). Python **3.14**, **no pip / no ensurepip network**. Deps come from **pacman**.
- Installed: `python-gobject` 3.56, `python-psutil` 7.2, `python-pillow` 12.3, `gtk4` 4.22, `libadwaita` 1.9, `mangohud`, `gamemode` 1.8.2, `kscreen-doctor`, `kwriteconfig6`/`kreadconfig6`/`qdbus6`, `nvidia-smi` (RTX 2060, driver 610.57.04).
- **Missing**: `python-pystray` (in `extra`, installable), `wl-clipboard`, `pytest`, `pip`.
- CPU: `intel_pstate` **active** → governors are only `performance`/`powersave`; finer knob is `energy_performance_preference` (EPP).
- RAPL: `/sys/class/powercap/intel-rapl/intel-rapl:0/constraint_{0,1}_power_limit_uw` — `constraint_0` = PL1, `constraint_1` = PL2. Root-write only.
- `coretemp` hwmon index is **dynamic** (`hwmon6` today) — always resolve by reading `.../name`.
- Throttle detection without root: `/sys/devices/system/cpu/cpu*/thermal_throttle/package_throttle_count`.
- KDE Plasma 6 **Wayland**. KWin **cannot** suspend compositing on Wayland.
- Internal panel `eDP-1` is **`Vrr: incapable`** — VRR toggle only matters with an external display.
- `sudo` needs a password. `pkexec`, `polkit` present.

---

## Architecture (recap)

```
 goblin-mode-pro-daemon  (systemd --user, unprivileged)
   Observer · Diagnostics · LogWatch · Tray · Bridge
   owns session bus  com.goblinmode.Pro   <——>  GUI (Adw, pure client)
        │
        │ system bus  com.goblinmode.ProHelper  (polkit-gated)
        ▼
 goblin-mode-pro-helper  (systemd system, root)
   governor · EPP · renice · RAPL PL1/PL2
   snapshot -> /run/goblin-mode-pro/state.json
```

### File map (`src/goblinmode/`)

| File | Responsibility | Notes / watch-outs |
|---|---|---|
| `paths.py` | XDG dirs, all file locations | single source of truth |
| `config.py` | `Settings` + `GameProfile` dataclasses, JSON load/save (atomic) | `SCHEMA_VERSION=1`. New fields need a default + `setdefault` in `__post_init__` for forward-compat |
| `observer.py` | psutil poll, per-game ABSENT↔PRESENT state machine | `_find_pid` picks the fattest matching non-wrapper proc. `_WINE_INFRA` set filters launchers |
| `payload.py` | `apply(profile,pid)` / `revert(profile)` / `revert_all()` | globals (governor+EPP, PL1/PL2, tearing, VRR) are **refcounted** in `_recompute_global`. `FORCED_EXE` = tray "force boost" synthetic profile (skips MangoHud) |
| `compositor.py` | `Compositor` — tearing + adaptive sync + KDE/X11 suspend | all best-effort, never raises. `restore` is an alias of `restore_tearing` |
| `mangohud.py` | order-preserving `MangoHud.conf` round-trip | only touches its own `### goblin-mode-pro begin/end` block |
| `runner.py` | generates `~/.local/bin/goblin-run`, `--print-env-for` matching | wrapper uses bash process-substitution for the stderr tee |
| `diagnostics.py` | `DiagnosticEngine.sample()` — temps/load/power/GPU/throttle | `.assess()` returns `(kind, detail)` on a throttle event. Only ticked while a game runs |
| `logwatch.py` | tails newest `logs/*.log` for VKD3D/DXVK faults | 30 s cooldown between hits |
| `incidents.py` | ring buffer + JSONL + `build_llm_payload` + `copy_to_clipboard` | payload = system prompt + fenced JSON |
| `daemon.py` | wires everything on one `GLib.MainLoop`; implements the bridge handler; CLI subcommands | `--print-env-for`, `--write-wrapper`, `--revert` don't start the loop |
| `ipc/daemon_bridge.py` | `DaemonBridge` (daemon) + `BridgeClient` (GUI), JSON over D-Bus | |
| `ipc/helper_client.py` | `HelperClient` → `HelperUnavailable` when helper down → "limited mode" | |
| `gui/app.py` | `Adw.Application`, `_DaemonMissingWindow` fallback | |
| `gui/window.py` | `Adw.PreferencesWindow`, signal fan-out to pages | |
| `gui/page_games.py` | `Adw.ExpanderRow` per profile, nested expanders for MangoHud / Runner vars / Power limits | `_building` guard suppresses change signals during rebuild |
| `gui/widgets/graph.py` | Cairo dual-axis plot | |

### `helper/goblin_helper.py`
Stdlib + `gi` (Gio/GLib) only — **no `goblinmode` import**, so it survives a broken user env.
D-Bus iface `com.goblinmode.ProHelper.Manager`:
`GetGovernor`, `SetGovernor`, `SetEPP`, `Renice`, `GetPowerLimits`, `SetPowerLimits`,
`ResetPowerLimits` (PL-only, keeps state file), `RevertAll` (everything + deletes state file).
`_snapshot()` runs once on first mutation. `_check_authorized()` calls PolicyKit1
`CheckAuthorization` with subject kind `system-bus-name`.

---

## Running the tests without pytest

`pytest` isn't installed and there's no pip. Two options:

1. `sudo pacman -S python-pytest` then `cd ~/goblin-mode-pro && pytest`
2. `python3 scripts/run_tests.py` — a ~90-line stdlib harness in the repo that
   implements just enough `tmp_path` + `monkeypatch` to run `tests/`.

GUI construction smoke (needs `$WAYLAND_DISPLAY`; does **not** present a window):

```sh
python3 scripts/gui_smoke.py
```

---

## TODO / next steps, in priority order

1. **Run `./install.sh` for real** (needs sudo). Then:
   - `systemctl --user status goblin-mode-pro` — daemon up?
   - `systemctl status goblin-mode-pro-helper` — helper owns the bus?
   - `gdbus call --system --dest com.goblinmode.ProHelper --object-path /com/goblinmode/ProHelper --method com.goblinmode.ProHelper.Manager.GetGovernor` → `('powersave',)`
   - `...SetGovernor performance` → check `cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor`; confirm **no polkit prompt**
   - `...RevertAll` restores it
2. **Install `python-pystray` + `wl-clipboard`** (`install.sh` does this) and confirm the tray icon shows in the KDE tray and the menu works.
3. **End-to-end game test**: add a profile for a real game, set Steam launch opts to `goblin-run %command%`, launch, verify governor flips / renice (`ps -o ni,pid,comm`) / tearing / MangoHud, then quit and verify full revert. Check daemon RSS stays < ~40 MB idle.
4. **Verify the `goblin-run` stderr tee** actually produces `~/.local/share/goblin-mode-pro/logs/<exe>-*.log` under Steam's runtime sandbox (the bwrap layers may swallow it — if so, fall back to parsing `~/.steam/steam/logs/` or `journalctl --user`).
5. **`daemon._launch_gui`** spawns `goblin-mode-pro`; confirm the PATH shim from `install.sh` is found from inside a systemd user unit (may need the full `/usr/bin/goblin-mode-pro`).
6. **polkit `allow_active=yes`** — confirm this is acceptable to the user or switch the shipped policy to `auth_admin_keep`.
7. **`Adw.PreferencesWindow` is deprecated** in libadwaita ≥1.4 (works in 1.9). The brief explicitly asks for it; revisit only if it breaks. Same for `Adw.MessageDialog` in `page_games.py` (→ `Adw.AlertDialog`).
8. **Icon**: `data/icons/com.goblinmode.Pro.svg` is a crude placeholder. `page_*` use stock symbolic icon names that may be absent in some themes (cosmetic).
9. Consider a **PKGBUILD** so this installs like a normal Arch package instead of `install.sh` copying into site-packages.
10. `diagnostics.py` shells out to `nvidia-smi` every sample (~1 s) — fine, but `python-pynvml` would be lighter if it's ever packaged.

## Bugs found & fixed after first install (2026-08-27)

1. **Bus-name collision** — the bridge used `com.goblinmode.Pro`, the same name
   the GUI's `Adw.Application` registers. Launching the GUI produced
   `Failed to register: No such interface "org.gtk.Actions"` and the GUI never
   opened. Fixed: bridge now owns `com.goblinmode.Pro.Daemon` /
   `/com/goblinmode/Pro/Daemon` (constants `BRIDGE_BUS_NAME` /
   `BRIDGE_OBJECT_PATH` in `__init__.py`), `DO_NOT_QUEUE` instead of
   `ALLOW_REPLACEMENT|REPLACE`.
2. **Case-sensitive / Windows-path matching** — profile `Wow.exe` didn't match
   the real process `WoW.exe` (comm) at `C:\...\WoW.exe` (cmdline). `_matches`
   is now case-insensitive for `exact`, splits `\` paths, checks cmdline tokens,
   and tolerates the 15-char comm truncation. Same fix in `runner._basename` /
   `resolve_profile_for_argv`.
3. **Wine-loader false filter** — `_find_pid` filtered a match when
   `/proc/<pid>/exe` basename was in `_WINE_INFRA` (it's always `wine64-preloader`
   for a Proton game). Now filters on the process *comm* only.
4. **`PartOf=graphical-session.target`** removed from the user unit (was a likely
   cause of the daemon stopping ~1 min in). `StartLimitIntervalSec=0` added.
5. **Incident spam** — at sustained 100°C the CPU `package_throttle_count` rises
   every poll, and `assess()` had no debounce, so it wrote a ~17 KB incident to
   `incidents.jsonl` *every second* (11.7 MB / 668 records in one session).
   Also `gpu_throttle` fired on NVML bit `0x4` (SwPowerCap) which is normal under
   load. Fixed: `DiagnosticEngine.assess()` now fires once per *episode* (onset +
   re-remind every `REMIND_SECONDS`=180), GPU alerts only on HW/thermal/power-
   brake bits (0x8/0x20/0x40/0x80); incident `metrics_window` downsampled to 20
   points; `incidents.jsonl` self-trims past 2 MB; GUI decodes the GPU bitmask.

After pulling these, the installed copy must be refreshed:
`./install.sh` again, or just re-copy `src/goblinmode` into site-packages +
`systemctl --user daemon-reload && systemctl --user restart goblin-mode-pro`.

MangoHud note: GMP writes the *config* but does not *inject* the layer. The
wrapper now exports `MANGOHUD=1` (+ `MANGOHUD_CONFIGFILE` for per-game) when a
profile has the overlay enabled; `daemon.set_profile` calls `payload.reapply()`
so a live GUI toggle hits a running game. MangoHud still needs its reload key
(Shift_L+F4) or a relaunch to re-read the file. On Lutris the user must tick
"Enable MangoHud" (or set `MANGOHUD=1`) in the game's System options - GMP has no
hook into Lutris' launch path.

Lutris note: the `goblin-run` wrapper is Steam-launch-option shaped. For Lutris,
env injection / log capture needs the wrapper wired as a Lutris "Command prefix"
(`/home/<user>/.local/bin/goblin-run`) or a pre-launch script. **Detection,
governor, renice, MangoHud, tearing all work regardless of launcher** — they're
driven by the psutil poll, not the wrapper.

## Frame-rate watchdog (added 2026-08-28)

For diagnosing DX12-specific hard FPS cliffs (10-15 FPS, restart clears it, not thermal).

- **`fpswatch.py`** tails the newest MangoHud CSV in `~/.local/share/goblin-mode-pro/mangohud/`. Virtual clock from the CSV's `elapsed` column so detection is burst-safe. Dip = trailing 2.5 s mean FPS ≤ `fps_dip_floor` **or** ≤ `fps_dip_ratio` × trailing-30 s median. Debounced (once per episode + `recovered` event with duration).
- **`gpu.py`** — `deep_state()` (nvidia-smi: VRAM used/total/free, PCIe gen/width cur vs max, pstate, core/mem clocks cur vs max, event reasons, rx/tx MB/s) and `assess()` → ranked likely causes: VRAM near-exhaustion → host-memory fallback; PCIe link down-trained; GPU stuck in low pstate under load; core clock collapsed. `post_mortem()` after game exit: VRAM not freed (> 900 MB) = driver leak → reboot.
- **`mangohud.py`** writes `output_folder`/`log_interval=200`/`autostart_log=1`/`log_duration=0` to the managed block when `profile.fps_watchdog` (works with `no_display=1` too). `runner.py` adds `MANGOHUD=1` when watchdog OR overlay is on. `daemon.set_profile` writes the mangohud config eagerly so the *next* launch has it.
- **Incident kinds**: `fps_dip` (carries `gpu_state` + `fps_trace`), `fps_recovered`, `vram_not_freed`. `Incident` gained `gpu_state` / `fps_trace` fields; `build_llm_payload` includes them; system prompt now lists VRAM/PCIe/VKD3D causes.
- **GUI**: Dashboard has VRAM / PCIe link / core-clock / frame-rate rows (⚠ on down-train / near-full / in-dip); `FpsGraph` (widgets/graph.py) single-series FPS panel with dashed dip-threshold line on the Diagnostics page.
- **Config**: `GameProfile.fps_watchdog` (bool), `fps_dip_floor` (22), `fps_dip_ratio` (0.5).
- **Lutris**: works without the wrapper — GMP writes MangoHud.conf on detection, Lutris' own MangoHud toggle (or `MANGOHUD=1`) injects it. First launch after enabling won't log (MangoHud reads config at start, GMP writes it ~7 s later); relaunch once.
- Tests: `test_fpswatch.py`, `test_gpu.py` (11 new). Total **43**.

### MangoHud live-edit limitation (found 2026-08-28)

MangoHud reads its config **once at process start** and never re-reads the file
(confirmed: no `MANGOHUD_CONFIG` override in the user's Lutris env, file *is* the
source, mid-game edits ignored). So:

- `payload.reapply` **no longer touches MangoHud** - governor/tearing/VRR/PL only.
- `daemon.set_profile` now **coalesces** (`_flush_profiles`, 400 ms `GLib.timeout`)
  - a dragged `Adw.SpinRow` was firing it ~10x/s, each doing a `mangohud.apply` +
  `_recompute_global`. `shutdown()` flushes the pending write.
- `mangohud.py` writes explicit hotkeys into every managed block
  (`toggle_hud=Shift_R+F12`, `toggle_logging=Shift_L+F2`, `reload_cfg=Shift_L+F4`)
  and exposes `HOTKEYS`; the Games page MangoHud expander says "applies on next
  launch" and lists the keys.
- Still TODO if someone wants true live control: MangoHud's control socket
  (`control=<name>` config key -> `$XDG_RUNTIME_DIR/MangoHud/<name>`), protocol
  undocumented and `reload_cfg` *stops* logging - deliberately not shipped.
- User workflow: set MangoHud/watchdog options in the GUI, then **relaunch the
  game once** (or use the in-game keys). First launch after enabling the watchdog
  never logs (config written ~7 s after MangoHud already started).

## Gotchas already hit (don't rediscover)

- `Gio.DBusProxy.new_for_bus_sync` with `DO_NOT_AUTO_START` **does not raise** when the name is unowned — you must check `get_name_owner() is None`. Both clients do.
- polkit `CheckAuthorization` signature is `((sa{sv})sa{ss}us)` — subject tuple, action id, details, flags, cancellation_id. Getting this wrong fails silently as "not authorized".
- Helper `RevertAll` deletes the state file; `ResetPowerLimits` must **not** (governor may still be applied for another game). That split already exists — keep it.
- pystray + GLib: use `icon.run_detached()`, not `run()`, so it shares the daemon's loop. Tray callbacks are marshalled to the main loop via `GLib.idle_add` (`Daemon._on_main_thread`).
- `install.sh` copies `src/goblinmode` into `site.getsitepackages()[0]` and writes `/usr/bin` shims — there is no `pip install` path on this box.

---

## Roadmap build (2026-08-28) — items 2-6 shipped

New modules (all with tests; `scripts/run_tests.py` = 67 pass):

| Module | Purpose | Wired via |
|---|---|---|
| `gamedetect.py` | detect *any* game: Steam `reaper SteamLaunch AppId=`, Lutris `lutris-wrapper`, Heroic, DRM `/proc/<pid>/fdinfo` engine activity, `libSDL2`/`libwine` in maps, DE blocklist (stems). `detect_games()` → scored `GameCandidate`s; `_pick_real_pid` walks the tree to the fat non-infra process. | `observer.poll()` runs the sweep when `settings.auto_detect`; emits `GameEvent(profile=None, auto=True, candidate=…)`. `daemon._adopt_detected_game` creates an `auto_created=True` profile, `tray.notify`, `bridge.emit_detected`. `daemon.ignore_game/keep_game`. |
| `preflight.py` | 12 checks (vm.max_map_count, esync nofile, split_lock, nvidia-drm modeset, THP, compaction, swappiness, fsync kernel ver, gamemode, mangohud, vulkan ICD, swap). `run_all()`, `pending_sysctls()`, `sysctl_dropin_text()`. Data-driven — add a `Check` to `CHECKS`. | daemon `run_preflight` / `apply_preflight_fixes` (helper `SetSysctl`, allowlisted keys) / `page_preflight.py` (System Check page). |
| `logrules.py` | 16 Wine/Proton failure patterns → cause + fix. `RULES`, `LIVE_PATTERNS` (subset watched live — `logwatch` now imports these), `analyze_text()`. | `logwatch._PATTERNS` = `logrules.LIVE_PATTERNS`; daemon `analyze_log`; `report`. |
| `report.py` | `build_report()` gathers system info + preflight flags + last incident + `logrules.analyze_text` on newest log + tweaks. `as_markdown` / `as_llm_prompt` / `github_issue_url`. | daemon `build_report` (copies MD to clipboard); Diagnostics "Build a bug report" dialog. |
| `focus.py` | `FocusMode.enter/exit`: balooctl6/tracker3 suspend, `org.freedesktop.ScreenSaver` idle-inhibit, KDE DND via plasmanotifyrc. | refcounted global in `payload._recompute_global` (like tearing); `GameProfile.focus_mode`; Games-page toggle. |

New config: `Settings.auto_detect` (True), `Settings.ignored_games`, `GameProfile.auto_created`, `GameProfile.focus_mode`.
New bridge methods: `SetAutoDetect`, `IgnoreGame`, `KeepGame`, `RunPreflight`, `ApplyPreflightFixes`, `BuildReport`, `AnalyzeLog`; signal `GameDetected`.
New helper method: `SetSysctl(key,value)` — `SYSCTL_ALLOW` allowlist, numeric values only, polkit-gated.
GUI: new **System Check** page; Games page gained the **Auto-detect games** switch + per-profile **AUTO / Keep / Ignore**; Diagnostics gained **Build a bug report** + **Analyze the Proton log**.

Logo: `data/icons/com.goblinmode.Pro.svg` is now the cartoon goblin mascot
(app plate). `data/icons/goblin-mark.svg` is the transparent version. `install.sh`
still installs `com.goblinmode.Pro.svg` — no change needed there. Render-check with
`rsvg-convert` (installed) or headless `google-chrome-stable --screenshot`.

### Deferred roadmap — design notes for the next pass

- **Backend abstraction (`backends/`)** — for multi-distro. `backends/cpu.py`:
  detect `intel_pstate` / `amd_pstate` / generic cpufreq / `platform_profile`;
  unify `set_performance()` / `restore()`. `backends/privilege.py`: prefer
  `gamemoded` D-Bus (`com.feralinteractive.GameMode` RegisterGameByPID) →
  `power-profiles-daemon` (`net.hadess.PowerProfiles SetActiveProfile`) → `tuned`
  → the GMP helper, in that order. This lets the polkit helper become optional.
  `backends/gpu.py`: NVIDIA (nvidia-smi) / AMD (`/sys/class/drm/card*/device/*`,
  `gpu_busy_percent`, `power_dpm_force_performance_level`) / Intel; DRM fdinfo for
  per-process load everywhere.
- **Core pinning** — `systemd-run --scope -p AllowedCPUs=<p-cores> --uid=$USER`
  wrapping the game, or `taskset -pc` post-launch. P-core detection: read
  `/sys/devices/system/cpu/cpu*/topology/core_type` (Intel hybrid) or
  `cpu*/cache/index3/shared_cpu_list` for Ryzen CCD grouping. Per-profile
  `pin_cores: bool`. Needs `CAP_SYS_NICE`/cgroup write → helper method
  `PinToCpus(pid, mask)`.
- **TDP presets** — extend the existing PL1/PL2 UI with a segmented 15/25/30 W
  control + a "battery vs AC" auto-switch (watch `/sys/class/power_supply/AC*/online`).
  For AMD APUs add a `ryzenadj` backend (helper shells `ryzenadj --stapm-limit=…`).
- **Regression tracking (`bench.py`)** — the MangoHud CSVs already accumulate in
  `~/.local/share/goblin-mode-pro/mangohud/`. Parse each on game exit into a
  summary row (`{game, proton_ver, date, avg_fps, p1_low, frametime_var}`) in a
  `sessions.jsonl`; a Diagnostics "History" view diffs the last N sessions per
  game and flags a >10% 1%-low regression. Proton version from the game's
  `steamapps/compatdata/<appid>/version` or the Lutris yml.
- **Crowdsourced profiles** — define a portable `profile.gmp.json` (exe match,
  env, mangohud, gamescope opts, sysctls, proton hint). Client: "Export profile"
  / "Import from file or URL". Server is out of scope — design the API as a flat
  static host: `GET /profiles/<steam_appid>.json` from a community git repo (like
  ProtonDB's data), PRs add profiles. No auth needed for read.
- **gamescope integration** — per-profile `gamescope: {enabled, width, height,
  refresh, upscale: fsr|nis|off, hdr}`. The `goblin-run` wrapper prepends
  `gamescope -W … -H … -r … -f -- ` when enabled. Detect gamescope + its version
  for feature gating.
- **Anti-cheat status** — on adopt/launch, scan the game dir for
  `EasyAntiCheat*/`, `BattlEye/`, `*_EAC*.exe`; cross-ref a bundled
  `anticheat.json` (id → {linux: yes|no|config, note}) refreshed from
  areweanticheatyet.com's JSON. Show a pill on the profile row.
