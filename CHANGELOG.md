# Changelog

All notable changes to Goblin Mode Pro. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); this project uses
[SemVer](https://semver.org/).

## [Unreleased]

### Changed
- GUI: the main window is now an `Adw.ApplicationWindow` with a proper
  header bar, a view switcher (that collapses to a bottom bar on a narrow
  window) and a primary menu — replacing the `Adw.PreferencesWindow`
  shell, which had no room for a menu. New: an **About** dialog, a
  **Keyboard Shortcuts** window, and accelerators (`Ctrl+W` close,
  `Alt+1..4` to jump between pages, `Ctrl+?` for the shortcuts).

## [1.2.3] — 2026-08-31

### Changed
- GUI: the 13 confirmation / detail dialogs moved from the deprecated
  `Adw.MessageDialog` to `Adw.AlertDialog` (libadwaita ≥ 1.5). Same look
  and behaviour; no more deprecation warnings, and the dialogs are proper
  child dialogs of the window rather than separate top-levels.
- An FPS dip that isn't a fault now says *why* instead of "no single
  cause stood out": a **GPU-bound scene** (card pegged at ≥ 92 % — the
  spot is heavier than the settings can sustain) or a **CPU-bound scene**
  (a core pegged at ≥ 95 % while the GPU has headroom — a single-threaded
  hotspot, i.e. a busy city or raid). Both are marked "not a fault" so
  they don't arm the post-game VRAM post-mortem.

## [1.2.2] — 2026-08-31

The diagnostics engine stops crying wolf. Three fixes to how it decides
something is worth telling you about — no new features, no config change.

### Fixed
- **FPS dips were classified against a stale GPU snapshot.** The dip
  handler read the cached `nvidia-smi` state (up to 5 s old) on the main
  loop, so by the time it looked the GPU was usually busy again and the
  dip was filed as "no obvious cause". It now takes a fresh snapshot on a
  worker thread — the same way the post-game VRAM check already does — so
  the "withheld vs starved" classifier actually has contemporaneous data.
  The unclassified-dip wording is also less alarming and names the usual
  culprit (zone load / shader compilation / background task).
- **The frame-rate watchdog cried wolf.** It flagged a dip the instant the
  2.5 s mean crossed the threshold, and — because its 30 s baseline median
  decayed as a dip persisted — it then reported a phantom "recovery" while
  the frame rate was still on the floor, often at an absurd number
  ("recovered to 24 FPS"). On a real session it emitted three to four
  times as many `fps_recovered` events as `fps_dip` ones. Rewritten as a
  proper state machine: low FPS must persist **4 s** before it's a dip,
  the baseline is **frozen** at its pre-dip value for the whole episode,
  recovery requires climbing back to **85 %** of that baseline (with
  hysteresis on the floor), a non-rendering window (alt-tab → ~0 FPS) is
  no longer mistaken for a dip, and a dip that never bounces back is
  relearned as the new baseline. Replayed over recorded sessions this
  roughly halves the dip incidents and removes the phantom recoveries
  entirely.

## [1.2.1] — 2026-08-30

Hardening and correctness pass over the 1.2.0 surface. No new features —
bug fixes, root-helper sandbox corrections, a quieter notification path,
and CI.

### Fixed — correctness
- **The `--revert` path was a no-op.** `goblin-mode-pro-daemon --revert`
  (the systemd `ExecStop` / crash-recovery hook) built a fresh payload
  whose "what's applied" flags were all empty, so it restored nothing. It
  is now state-driven: it reads `applied.json`, unconditionally reverts
  the helper's own `/run` snapshot, and cold-restores the compositor
  (tearing / VRR / refresh cap) and focus mode. The daemon also runs this
  on startup when it finds dirty state from a killed previous instance.
- **Power limits leaked with concurrent games.** When a game with a raised
  TDP exited while another kept the governor boosted, the limit was never
  reset. It now resets (`ResetPowerLimits` / `ResetTDP`) whenever the
  applied power no longer matches what any running game wants.
- `dxvk_async` now defaults **off** — `DXVK_ASYNC=1` is a dead env var on
  stock DXVK / current Proton-GE (existing profiles are untouched).

### Fixed — security / sandbox
- `SetNvidiaModeset`, the `user.max_user_namespaces` fix and the
  `kernel.unprivileged_userns_clone` fix all wrote to paths the hardened
  helper unit made read-only, so they failed on any correct install.
  `ReadWritePaths` corrected; a coverage test now fails the build if the
  allowlist and the code drift apart.
- `SpinUpFans` (previously promptless, no lower bound) now has a 40 % duty
  floor and its own prompting polkit action
  (`com.goblinmode.pro.manage-hardware-thermal`). The helper hands fan
  control back to the EC on startup after a dirty exit.
- `SetPowerLimits` refuses a write below a 6 W floor (it exists to raise
  the cap; driving PL1 to a few watts is a silent local slow-down).
- `Renice` now fails **closed** when the caller's uid can't be resolved
  (it was treated as root), and pins the target with a pidfd to close a
  PID-reuse window. `SetEPP` validates its argument against the kernel's
  advertised list.
- The launch wrapper's env-name guard now anchors the whole token, not
  just the first character.

### Fixed — operational
- **Thermal-throttle notification spam.** On a thermally-marginal laptop
  the CPU package throttle counter ticks under any turbo load, and the
  "one incident per episode" guard was reset by a single throttle-free
  sample — so the next tick read as a fresh onset and popped a new
  notification every few seconds. Throttling now has to recur across the
  trailing 20 s window before it's an incident at all, an episode only
  ends after 90 s clear, and the reminder cadence for a persisting
  throttle is 15 min (was 3 min). The popup is also normal urgency now,
  not critical — critical notifications on KDE are resident (ignore the
  expire timeout, bypass the per-app mute), which is how one got stuck
  on screen and forced a `plasmashell` restart. Only an actual GPU /
  driver fault stays critical.
- Log directories (`logs/`, `mangohud/`) are now pruned (40 files / 500 MB
  each, oldest first) on daemon start and after every session — they grew
  without limit before.
- **ananicy-cpp conflict.** It manages niceness, and so do GameMode and
  Goblin's `renice`. The System Check warns when it's running, new
  profiles start with `renice` off while it is, and `Wrap with GameMode`
  is now a per-game toggle.

### CI / docs
- `shellcheck` (including the generated launch wrapper), `ruff check`
  (pyflakes), and a polkit-policy ↔ helper consistency test added to CI.
- README version badge is now dynamic; privilege-model table and System
  Check list brought current; new "how this relates to GameMode /
  ananicy-cpp / MangoHud" section.

### Deferred to a follow-up
- The `daemon.py` → `daemon_api.py` and `page_games.py` → profile-editor
  extractions, and the GTK4 `Adw.PreferencesWindow` → `ApplicationWindow`
  restructure + `MessageDialog` → `AlertDialog` migration (Blocks 7–9 of
  the review plan) — better done as their own reviewed PRs than bundled
  into a hardening branch.

## [1.2.0] — 2026-08-29

The second roadmap release. Everything in `ROADMAP.md`'s post-1.1 menu,
shipped — 18 batches across all four sections.

### For people new to Linux gaming
- **Wizard part two** — a new "missing pieces" step lists whatever's missing
  (MangoHud, GameMode, a gaming kernel) with a copy-pasteable, distro-correct
  install command. Nothing is installed automatically or via a new privileged
  helper method — same trust boundary as the existing Dashboard setup tips.
- **Guided launch-failure fixes** — the Proton-log analyzer now offers a
  copyable `protontricks` command inline for the causes that have one
  (missing vcrun/mono), instead of only prose.
- **"Explain my score" panel** — expands the health pill into every
  failing/warn pre-flight check and what it actually breaks in-game.
- **Tray-only onboarding** — the tray menu shows the readiness score and a
  "Finish setup" nudge, so someone who never opens the window still sees it.
- **Full i18n** — every static UI string wrapped (289 msgids, up from 14),
  with real German, French, Spanish, Brazilian Portuguese and Chinese
  (Simplified) catalogues shipping instead of placeholders.

### For the community & power users
- **Benchmark comparison view** — diffs two sessions' FPS/frame-time/thermal
  metrics side by side, in the GUI (Diagnostics → Compare two sessions) and
  headlessly (`goblin-mode-pro-cli compare GAME`).
- **Shareable benchmark cards** — copy a session as JSON, or save a small
  Cairo-rendered report-card PNG to `~/Pictures/goblin-mode-pro/`.
  "Community submissions per GPU" is a documented PR flow
  (`community/benchmarks/README.md`), not a live upload service.
- **AMD Curve Optimizer undervolt** *(opt-in, ryzenadj)* — re-applies the
  offsets from `/etc/goblin-mode-pro/amd-undervolt.conf` on launch, mirroring
  the existing Intel undervolt path exactly. GMP never chooses the values.
  First "I understand the risk" confirm dialog in the app, also used by fan
  spin-up below.
- **GSP-firmware / `nvidia-drm.modeset` info** — read-only state on the
  Dashboard, with a button that writes the persistent modprobe.d config
  behind a plain-language "takes effect after reboot" confirm.
- **Shader pre-warm** *(best-effort, unofficial)* — forces Steam's already-
  downloaded per-game Fossilize shader-cache archive through
  `fossilize_replay` on launch instead of waiting on Steam's own background
  scheduler.
- **Per-output VRR** and **Hyprland compositor support** — VRR can now be
  restricted to specific monitors (KDE), and tearing/VRR both work under
  Hyprland (`hyprctl keyword`), not just KWin.
- **Gamescope session mode** — `goblin-mode-pro-cli gamescope-session`
  launches a standalone gamescope session (Steam Big Picture by default, or
  a specific game), with a matching app-menu entry, instead of only nesting
  gamescope inside one game's launch wrapper.
- **Prometheus textfile exporter** — the Dashboard's own metrics as a
  node_exporter textfile-collector `.prom` file, off by default.
- **Telemetry-free "works for me" reports** — a "Share what worked" button
  opens a pre-filled GitHub issue with an anonymized system + profile
  summary. No server, no account, no telemetry — the issue tracker is the
  database.
- **20 new starter profiles** — Elden Ring, Baldur's Gate 3, RDR2, Hogwarts
  Legacy, Star Citizen, Apex Legends, CS2, Destiny 2, FFXIV, Path of Exile 2,
  Diablo IV, Monster Hunter Wilds, Helldivers 2, No Man's Sky, Stardew
  Valley, Terraria, Valheim, Sea of Thieves, Warframe, God of War.

### Handhelds
- **Battery-vs-AC auto-switch** — a lower TDP preset applies automatically on
  battery and reverts on plug-in, mid-session, not just at next launch.
- **TDP presets per handheld model** — Steam Deck / ROG Ally / Legion Go /
  other each get their own starter AC and battery wattages instead of one
  generic value.
- **Per-game refresh-rate cap** on the internal panel, and **preemptive fan
  spin-up** on launch *(opt-in, where the EC exposes a writable pwm control —
  most don't)*.

### Project
- **`.deb` / `.rpm` built and attached by CI** on every published release
  (`release.yml`), signed with the maintainer's own GPG key when the repo
  secrets are set (`CONTRIBUTING.md` → "Signing releases") — unsigned,
  same as before, when they're not.
- **Flathub** — decision recorded as *not pursuing* (documented in
  `packaging/README.md` since 1.1.0; the roadmap item is now closed rather
  than left open).
- **Helper polkit-gate contract test** and a **GUI smoke test under Xvfb**
  (constructs the real `MainWindow` headlessly with a stubbed bridge) — 190
  tests total, up from 90.

### Notes on scope
A few items were narrowed from the roadmap's original wording rather than
built as literally described, each for a concrete reason:
- Wizard part two hands the user a command to copy instead of adding a new
  "run arbitrary command as root" helper method — the existing helper's
  methods are all fixed, single-purpose actions, and a generic one would be
  a real privilege-escalation surface.
- Shader pre-warm, per-output VRR on Hyprland, fan spin-up, and the AC/
  battery switch are all genuinely best-effort — they no-op cleanly wherever
  the hardware/compositor doesn't support them (most systems, for fan
  control), and none of them were tested on the real hardware they target.
- Shareable benchmark cards and "works for me" reports both route through
  GitHub (a PR, or a pre-filled issue) instead of a live upload endpoint —
  this project has no server and isn't standing one up.

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
