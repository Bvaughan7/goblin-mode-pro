# Changelog

All notable changes to Goblin Mode Pro. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); this project uses
[SemVer](https://semver.org/).

## [1.1.0] — 2026-08-29

The roadmap release. Everything in `ROADMAP.md`'s 1.0 menu, shipped.

### For people new to Linux gaming
- **First-run wizard** — a one-minute guided setup on first launch: system
  check with one-click safe fixes → pick your launcher → exactly where to paste
  `goblin-run`.
- **System health score** — the System Check rolled into one 0–10 traffic-light
  number on the Dashboard, failing items one click away.
- **ⓘ "what does this do?" popovers** on every per-game toggle.
- **Undo for pre-flight fixes** — each applied sysctl gets an Undo that restores
  its previous value (via a new polkit-gated `RevertSysctl` helper method).
- **Distro-specific setup tips** on the Dashboard — copy-pasteable one-liners: a
  gaming kernel when yours is stock, the user-namespace fix on Debian/Ubuntu,
  RPM Fusion NVIDIA on Fedora.
- **Controller check** and a **GameMode** row (what `gamemoded` reports).

### For the community & power users
- **Benchmark mode** — arm a game, play, get a report card: avg / 1% / 0.1% low
  FPS, frame-time stutter %, peak temps. Feeds the regression tracker.
- **NVIDIA & AMD/RADV tuning presets** — per game, only the toggles for your GPU
  are shown (`__GL_THREADED_OPTIMIZATIONS`, unlimited shader cache, forced
  G-SYNC; `mesa_glthread`, `RADV_PERFTEST=gpl,nggc,rt`).
- **CLI** — `goblin-mode-pro-cli` (status / boost / health / benchmark /
  sessions / preflight / report / setup / games), a session-bus client for SSH
  and scripts.
- **ProtonDB tier + anti-cheat lookup** — by Steam AppID, from ProtonDB and
  AreWeAntiCheatYet, disk-cached, GUI-only, fixed two-host allowlist.
- **Proton / Wine version awareness** and **shader-cache management** — list
  custom builds and every DXVK/VKD3D/Steam/Mesa cache with sizes; one-click
  Clear behind a confirm.
- **Desktop notifications** — boost engaged / released, benchmark result,
  regression, driver fault matched to a cause.
- **Full setup export** — every profile, kernel flag, Proton build and cache
  size in one shareable Markdown file (paths/notes redacted).
- **Auto-clip** *(opt-in, `gpu-screen-recorder`)* — a 30-second replay buffer
  saved when the FPS watchdog or a GPU fault fires.
- **Undervolting** *(opt-in, Intel)* — re-applies the offsets from your
  `/etc/intel-undervolt.conf` on game start (suspend/thermald reset them). GMP
  never chooses the values.
- **i18n scaffolding** — gettext plumbing, `po/`, an extraction script, a first
  pass of wrapped strings.

### Handhelds
- **Handheld auto-profile** — Steam Deck / ROG Ally / Legion Go detected; new
  profiles start with TDP enabled, fullscreen gamescope, a lower FPS-dip floor.

### Project
- **`CONTRIBUTING.md`**, issue / PR templates, `good first issue` labels.
- **Docs site** — mkdocs-material → GitHub Pages.
- **`.deb` / `.rpm`** source-package directories (`packaging/debian/`,
  `packaging/rpm/`).
- **Integration tests** — the Observer state machine and the Payload
  apply/revert refcounting, with a fake helper. 90 tests.
- Flatpak: revisited and documented as *not provided, on purpose*
  (`packaging/README.md`).

### Fixed
- `_notify()` was called with keyword args it didn't accept — a `TypeError` on
  every GPU-fault / thermal-throttle / VRAM incident notification.
- `ClipBuffer` start/save/stop now lock every access to the recorder process.
- `GetHealth` is dispatched off the daemon's main loop (it can re-run the full
  pre-flight probe).
- Notifications keep a replace-id per category, so routine status can't
  overwrite a live incident bubble.
- helper: `RevertSysctl` logged the restored value as `None`.

## [1.0.0] — 2026-08-29

First public release. 🎉

Goblin Mode Pro detects a game launching, applies a set of system performance
changes, and reverts every one of them cleanly on exit — then watches thermals,
frame rate and the Proton/Wine log and turns a problem into a plain-language
explanation.

Thanks to everyone who ran early builds on their own rigs and filed the rough
edges — the AMD sensor fallbacks, the KDE icon-cache notes and the "smooth UI
under load" work all came out of that testing.

### What works in 1.0.0

**Detect & apply**
- Auto-detects any game (Steam / Lutris / Heroic launcher tags, DRM `fdinfo`
  GPU activity, linked game libraries, minus a desktop-environment blocklist) —
  not just a hardcoded list. New games get a default profile and a
  **Keep / Ignore** tray prompt.
- Per-game performance payload, applied once on launch and reverted once on
  exit; global tweaks (governor, tearing) are refcounted across concurrent
  games:
  - CPU governor → `performance` and the energy-performance hint (EPP) → performance
  - Process priority (`renice`, default −5) via the polkit-gated helper
  - **CPU power limit / TDP** — a preset picker (15 / 25 / 35 / 45 / 65 W).
    Intel via RAPL; AMD laptops via `ryzenadj` (experimental, opt-in)
  - **Core pinning** — pin the game's thread tree to the fast cores of a hybrid
    CPU or a single Ryzen CCD (no privilege needed)
  - KDE **Allow Tearing** + **Adaptive Sync / VRR**, restored on exit
  - **Focus mode** — pause the file indexer, turn on Do Not Disturb, inhibit idle
  - MangoHud config round-tripping (touches only its own managed block)
  - Proton/Wine runner variables (NVAPI, Fsync, async shaders)
  - **gamescope** — per-game resolution, FPS cap, FSR / NIS upscaling, HDR

**Diagnose**
- **System Check** — 14 pre-flight checks (`vm.max_map_count`, esync FD limit,
  split-lock, `nvidia-drm.modeset`, THP, memory compaction, kernel fsync
  support, **user namespaces** for the Steam Runtime + anti-cheat, Vulkan ICD,
  gamemode/MangoHud presence, …), each with a status and — where safe — a
  one-click fix plus the `sysctl.d` / kernel-param text to persist it.
- **Proton log analyzer** — 16 known Linux-gaming failure patterns → plain
  cause + fix.
- **Frame-rate watchdog** — logs FPS via MangoHud; on a sustained cliff it
  snapshots deep GPU state (VRAM, PCIe link, clocks, power state), classifies
  *withheld vs starved*, names the likely cause, and after the game exits
  checks whether VRAM was actually released.
- **Regression tracking** — every session is summarised (avg / median / 1% low
  FPS, duration, active tweaks) and compared to the recent history for that
  game; a >10 % swing is flagged.
- **Diagnostic engine** — CPU package temp, per-core load, package power vs
  PL1/PL2, GPU load/temp, throttle flags. Debounced incidents on throttle onset
  or a GPU fault in the log.
- **One-click bug report** and a structured **LLM export** for an incident.

**Share**
- Export / import a tuned profile as JSON.
- Download known-good starting profiles from the repo's `profiles/` directory
  (anonymous HTTPS GET, host-pinned, nothing uploaded).

### Architecture & safety

- Three processes: an unprivileged `systemd --user` daemon, a tiny root helper
  (`CAP_SYS_NICE` only, `PrivateDevices`, syscall filter, polkit on every call),
  and an on-demand GTK4 / libadwaita GUI that is a pure D-Bus client.
- The helper re-validates every argument (enum / allowlist / range / process
  ownership) regardless of the caller; RAPL clamped to the firmware max, TDP to
  4–120 W, sysctls a fixed allowlist with per-key ranges.
- The daemon and helper make **no network connections**; the only outbound
  request is the community-profile fetch, in the GUI, on an explicit click, to
  one pinned host.
- The launch wrapper imports env vars as strict `NAME=VALUE` — no `eval`.

### Platform support

Any Linux with **systemd**. Core features are vendor-neutral (Intel *and* AMD);
NVIDIA gets the deep GPU snapshot, AMD/Intel GPUs get temp + load. KDE Plasma
gets the compositor tweaks; other desktops get everything else. Developed and
tested on CachyOS, KDE Plasma / Wayland, Intel + NVIDIA.

### Known limitations

- The AMD `ryzenadj` TDP path is experimental and untested on real AMD hardware;
  it no-ops safely when `ryzenadj` is absent.
- No `.deb` / `.rpm` yet — Debian / Fedora / openSUSE use `install.sh`.
- Flatpak is not provided (a polkit + system-service tool doesn't fit the
  sandbox).

[1.0.0]: https://github.com/Bvaughan7/goblin-mode-pro/releases/tag/v1.0.0
