# Changelog

All notable changes to Goblin Mode Pro. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); this project uses
[SemVer](https://semver.org/).

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
