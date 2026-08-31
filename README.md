<div align="center">

<img src="docs/logo.png" alt="Goblin Mode Pro" width="360">

# Goblin Mode Pro

### A one-switch performance helper for Linux gaming.

Goblin Mode Pro notices when a game starts, flips your system settings to their
"I'm gaming now" positions, and puts every one of them back the moment you quit —
then watches the temps, frame rate and Proton log and tells you, in plain
language, why something broke.

**MangoHud shows you the numbers; Mission Center shows you the system. Goblin
Mode Pro *acts* on both** — it changes the CPU governor, process priority,
compositor and power limits automatically per game and reverts them cleanly,
and when your FPS falls off a cliff it captures the GPU state and names the
cause. It's the missing piece between "I can see the problem" and "it's fixed."

![version](https://img.shields.io/github/v/release/Bvaughan7/goblin-mode-pro?color=2ea043&label=release)
![python](https://img.shields.io/badge/python-3.11+-3f7fbf)
![license](https://img.shields.io/badge/license-MIT-4E6A24)
![CI](https://github.com/Bvaughan7/goblin-mode-pro/actions/workflows/ci.yml/badge.svg)
![helper](https://img.shields.io/badge/root_helper-polkit_hardened-4E6A24)
![distros](https://img.shields.io/badge/distros-any_systemd-266F64)

**[Documentation](https://bvaughan7.github.io/goblin-mode-pro/)** · [Getting started](https://bvaughan7.github.io/goblin-mode-pro/getting-started/) · [Command line](https://bvaughan7.github.io/goblin-mode-pro/cli/) · [Troubleshooting](https://bvaughan7.github.io/goblin-mode-pro/troubleshooting/)

<img src="docs/demo.gif" alt="Walkthrough of all four tabs: per-game settings, the system pre-flight check, the dashboard as a game launches and the boost engages, and Diagnostics catching a temperature climb and a flagged FPS regression before everything reverts." width="620">

<sub><i>Set it up per game → check the system is ready → a game launches and the CPU locks to <code>performance</code> → Diagnostics catches the temp climb and a −30% 1%-low regression → the game exits and everything reverts and cools.</i></sub>

</div>

---

<div align="center">

| | |
|---|---|
| ![Dashboard](docs/screenshots/dashboard.png) | ![Games](docs/screenshots/games.png) |
| **Dashboard** — what your machine can be tuned for, plus live CPU/GPU stats | **Games** — per-game switches, in plain language |
| ![System Check](docs/screenshots/system-check.png) | ![Diagnostics](docs/screenshots/diagnostics.png) |
| **System Check** — the kernel settings that make Linux games crash or stutter | **Diagnostics** — temp/FPS history, crash-log analysis, one-click report |

</div>

---

## What it actually does to boost your FPS

Linux ships with sensible **defaults for a laptop on battery**, not for a game
that wants every drop of performance. Goblin Mode Pro changes the settings below
while a game is running and reverts them afterwards. In plain terms:

| Setting | What Linux does by default | What Goblin Mode Pro does | Why it helps |
|---|---|---|---|
| **CPU speed ("governor")** | Slows the CPU down whenever it thinks you're idle, to save power | Locks it at full speed while you play | Stops the stutter that happens when the CPU "wakes up" a beat too late |
| **Game priority ("renice")** | Treats your game the same as every background task | Tells the scheduler your game comes first | Background jobs (updates, indexing, browser tabs) stop stealing CPU mid-fight |
| **Energy hint ("EPP")** | Leans toward efficiency | Leans toward performance | The CPU stops second-guessing itself and holds higher clocks |
| **Screen tearing** | Adds a small delay so the picture is always "clean" | Allows tearing while you game | Lower input lag — your mouse feels more connected |
| **Adaptive sync / VRR** | Often left off | Turns it on for monitors that support it | The monitor matches the game's frame rate, killing a whole class of stutter |
| **CPU power limit / TDP** | Caps the watts the CPU may draw | Optionally raises the cap — a slider with 15/25/35/45 W presets. Intel via RAPL; AMD laptops via `ryzenadj` (experimental) | If your cooling can keep up, the CPU holds its top speed for longer |
| **Core pinning** *(hybrid / chiplet CPUs)* | Threads run on any core | Optionally pins the game to the fast cores (Intel P-cores) or one CCD (Ryzen) | Keeps the game off the slow cores and the cross-CCD latency hop |
| **Focus mode** | Indexer, notifications and screen-blanking all keep running | Pauses the file indexer, turns on Do Not Disturb, stops the screen sleeping | Removes the background hitches and the "screen dimmed mid-cutscene" problem |
| **Proton/Wine switches** | Off unless you set them by hand | Flips the common ones (NVAPI, Fsync, async shaders) per game | The settings most Windows games need on Proton, without editing launch options |
| **GPU driver tuning** | Off unless you set them by hand | Per-game presets — NVIDIA (`__GL_THREADED_OPTIMIZATIONS`, unlimited shader cache, forced G-SYNC) and AMD/RADV (`mesa_glthread`, `RADV_PERFTEST=gpl,nggc,rt`). Only the toggles for *your* GPU are shown | The driver knobs the community reaches for, without hand-editing launch options |
| **Undervolt re-apply** *(Intel, opt-in)* | Suspend / `thermald` can silently reset your undervolt mid-session | Re-runs `intel-undervolt apply` when the game starts — **never picks the values**, only re-applies the ones you set in `/etc/intel-undervolt.conf` | Your undervolt actually stays on |
| **Curve Optimizer re-apply** *(AMD, `ryzenadj`, opt-in)* | Same problem as above, on AMD | Re-applies the offsets from `/etc/goblin-mode-pro/amd-undervolt.conf` — **never picks the values**, same rule as the Intel path. Behind an "I understand the risk" confirm | Your undervolt actually stays on |
| **Refresh-rate cap** *(internal panel)* | Fixed at your panel's max | Optionally caps it per game (Deck 40/50/60, Ally up to 120…) | Trade smoothness for battery life on a per-game basis |
| **Fan spin-up** *(opt-in, where the EC allows it)* | Reacts to heat after the fact | Forces fans to a high duty cycle on launch, reverts on exit | Gets ahead of thermal throttling instead of catching up to it |
| **MangoHud overlay** | Not shown | Shows FPS / temps on screen if you want it | See what's actually happening |
| **gamescope** *(optional)* | Not used | Runs the game inside gamescope, or launch a whole standalone gamescope session (`goblin-mode-pro-cli gamescope-session`, or the app-menu entry) | Rock-solid FPS cap, FSR/NIS upscaling, and alt-tab that doesn't break the game |

"Global" changes (CPU speed, tearing) are shared — they switch on when the first
game starts and switch off only when the last one quits.

### How this relates to what you already have

If you're on CachyOS, Nobara or a similar setup, some of the tuning above is
already handled. Goblin Mode Pro is built to sit *alongside* that stack, not
replace it — and the half it doesn't overlap with is the point.

| You already have | It covers | Goblin adds |
|---|---|---|
| **Feral GameMode** | governor + GPU perf level + `ioprio`/`nice` for the duration of a game | per-game (not global) profiles, compositor tearing/VRR, TDP, core-pinning, undervolt re-apply, focus mode — and it wraps `gamemoderun` itself unless you turn that off |
| **`ananicy-cpp`** (CachyOS default) | niceness / ionice / sched policy by rules | the System Check warns when it and GameMode and Goblin's `renice` would stack; new profiles start with `renice` off while it's running |
| **CachyOS `game-performance`** | governor + the distro's `scx` gaming scheduler on launch | everything in the table above; Goblin does **not** switch schedulers |
| **MangoHud** | shows FPS / frametime / temps | the frame-rate watchdog that snapshots GPU state on a dip and names the cause, benchmark regression tracking across sessions, the Proton log analyzer, the System Check |

### And while you play, it watches for problems

- **System health score** — the System Check rolled up into one traffic-light
  number on the Dashboard ("your system is 9/10 game-ready"), with the failing
  items one click away. The first-run wizard shows it too and offers to apply the
  safe fixes on the spot.
- **System Check** — a one-page scan of the kernel settings that make Linux games
  crash or stutter (the infamous `vm.max_map_count` that makes Unreal Engine 5
  games crash, the file-descriptor limit that breaks Proton's esync, user
  namespaces that anti-cheat needs, and about a dozen more). Each item is marked
  **PASS / CHECK / ACTION**, the safe ones have a one-click fix — and now an
  **Undo** button that restores the previous value.
- **Benchmark mode** — arm it for a game, play a few minutes, and get a report
  card: avg / 1% low / 0.1% low FPS, frame-time stutter %, peak CPU/GPU temps.
  It feeds the regression tracker.
- **Benchmark comparison + shareable cards** — diff two sessions' metrics side
  by side (before/after a Proton bump, a kernel change, a tweak), copy a
  session as JSON, or save a small report-card image. See
  [`community/benchmarks/`](community/benchmarks/) for the PR-based
  per-GPU submission flow.
- **"Explain my score"** — expands the health pill into every failing/warn
  check and what it actually costs you in-game, one click from the Dashboard.
- **"Works for me" reports** *(opt-in, per game)* — one button opens a
  pre-filled GitHub issue with an anonymized system + tuning summary.
  Telemetry-free: nothing is sent anywhere until you submit the form
  yourself, and no server or account is involved.
- **Compatibility check** — type a game's Steam AppID and get its ProtonDB tier
  and live anti-cheat verdict (from AreWeAntiCheatYet), cached to disk, looked up
  by the GUI only.
- **Auto-clip** *(opt-in, needs `gpu-screen-recorder`)* — keeps a 30-second
  replay buffer while the game runs and saves the clip when the frame-rate
  watchdog or a GPU fault fires, so a bug report can include footage.
- **Desktop notifications** — boost engaged / released, a benchmark result, a
  regression caught, a driver fault matched to a cause.
- **Proton log analyzer** — reads the Wine/Proton log after a crash and matches it
  against ~16 known Linux-gaming failures (missing Visual C++, VRAM ran out,
  "device lost", anti-cheat didn't start…), then tells you the cause and the fix
  in one sentence.
- **Frame-rate watchdog** — optional, per game. If your FPS falls off a cliff and
  stays there, it snapshots what the GPU was doing (VRAM, PCIe link, clocks,
  power state) so you can see *why*, instead of guessing.
- **Bug report** — one button collects your system info, the scan results, the
  last problem and the active settings into a Markdown report on your clipboard,
  ready to paste into a help thread.
- **Export my full setup** — every profile, kernel flag, custom Proton build and
  shader-cache size in one shareable Markdown file (paths and notes redacted),
  for "help me" threads or reproducing a config on another machine.
- **Proton & shader-cache tools** — see your custom Proton-GE / CachyOS / TKG
  builds and the size of every DXVK / VKD3D / Steam / Mesa shader cache, with a
  one-click **Clear** (behind a confirm).

---

## Will this work on my system?

**The core FPS features work on any Linux PC with systemd.** A few extras depend
on your hardware, and Goblin Mode Pro detects that and greys out what doesn't
apply — it never just fails silently.

| Your setup | What you get |
|---|---|
| **Any systemd Linux** — Arch, CachyOS, Debian, Ubuntu, Fedora, Nobara, openSUSE, Pop!_OS… | Everything below that your hardware supports. The installer detects your package manager. |
| **Intel CPU** | All CPU features, including the power-limit boost. |
| **AMD CPU** | Everything. Governor, energy hint and priority work exactly as on Intel; laptop TDP control and an *(opt-in)* Curve Optimizer undervolt re-apply both work if you install `ryzenadj` (the installer then loosens the helper sandbox just enough for it). Core pinning uses your CCD layout. |
| **NVIDIA GPU** | Everything, including the deep "why did my FPS drop" GPU snapshot and the read-only `nvidia-drm.modeset` / GSP-firmware info. |
| **AMD or Intel GPU** | Everything **except** that deep snapshot and the NVIDIA modeset info — you still get GPU temperature and load. |
| **Steam Deck / ROG Ally / Legion Go** | Auto-detected — new game profiles start with a handheld layout: TDP slider enabled with a starter preset for your model (and a lower one on battery, switched automatically on plug/unplug), fullscreen gamescope, a lower FPS-dip floor. |
| **KDE Plasma** | Everything, including tearing / VRR, per-output VRR, and the internal-panel refresh-rate cap. |
| **Hyprland** | Tearing and VRR both work (`hyprctl keyword`) — VRR is compositor-wide there, not per-monitor like KDE's. |
| **GNOME, Sway, other** | Everything **except** the compositor tweaks (GNOME/Mutter has no equivalent runtime toggle yet). GameMode (which the launch wrapper uses automatically) covers most of that ground. |
| **No systemd** (Void with runit, Gentoo/OpenRC, Alpine…) | Not supported — the daemon and the privileged helper are both systemd units. |

> **Is it "Intel only"?** No. That's a common misconception. Only the **CPU
> power-limit raise** is Intel-specific (it uses Intel's RAPL interface). Every
> other performance feature is vendor-neutral and works fine on AMD.

---

## Install

**Arch / CachyOS / Manjaro** — from the AUR (see [`packaging/`](packaging/)):

```sh
cd packaging/aur && makepkg -si
sudo systemctl enable --now goblin-mode-pro-helper.service
systemctl --user  enable --now goblin-mode-pro.service
```

**Debian / Ubuntu** and **Fedora / openSUSE** — source-package directories are in
[`packaging/debian/`](packaging/debian/) and
[`packaging/rpm/`](packaging/rpm/) (`dpkg-buildpackage` / `rpmbuild`, or point an
OBS project at them).

**Everything else** — the installer:

```sh
git clone https://github.com/Bvaughan7/goblin-mode-pro
cd goblin-mode-pro
./install.sh
```

The installer figures out your distribution, installs the dependencies it can,
and tells you exactly what to install by hand if it doesn't recognise your
package manager. Options:

```sh
./install.sh            # full install — asks for your password once, for the root helper
./install.sh --user     # no root helper: everything works except CPU speed/power tuning
./install.sh --uninstall
```

**What needs your password, and why:** changing the CPU governor, priority and
power limit requires root. Goblin Mode Pro does *not* run as root — instead it
installs one tiny root service (`goblin-mode-pro-helper`) that does only those
specific writes, each one checked by polkit and validated against a fixed
allowlist. Skip it with `--user` and everything else still works ("limited
mode").

### Dependencies (if you're installing them yourself)

You need Python 3, PyGObject, GTK 4, libadwaita, and `psutil`.

**Minimum versions:** Python **3.11**, GTK **4.0**, libadwaita **1.5**. The GUI
is built on `Adw.AlertDialog`, `Adw.AboutDialog` and `Adw.Breakpoint`, all of
which landed in libadwaita 1.5 — it checks at startup and tells you rather than
crashing. That floor is what Ubuntu 24.04 LTS and Debian 13 ship, so any
currently-supported distro is fine. The daemon and the `goblin-mode-pro-cli`
command have no GTK dependency at all and work on anything older.

Package names:

| Distro | Command |
|---|---|
| Arch / CachyOS | `pacman -S python-gobject python-psutil gtk4 libadwaita python-pystray wl-clipboard mangohud gamemode gamescope` |
| Debian / Ubuntu | `apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 python3-psutil wl-clipboard mangohud gamemode gamescope` |
| Fedora / Nobara | `dnf install python3-gobject python3-psutil gtk4 libadwaita wl-clipboard mangohud gamemode gamescope` |
| openSUSE | `zypper install python3-gobject python3-psutil gtk4-tools libadwaita wl-clipboard mangohud gamemode gamescope` |

`mangohud`, `gamemode` and `gamescope` are optional but recommended — the overlay,
the frame-rate watchdog and the gamescope integration need them.
`ryzenadj` (AUR / COPR) is needed for AMD-laptop TDP control.

### Before you file a "not working" issue

- **`polkit` must be installed** — the root helper is inert without it, and CPU
  speed / power tuning silently drop to "limited mode."
- **Kernel ≥ 5.16** for `WINEFSYNC` (anything current is fine; the pre-flight
  check flags it).
- **User namespaces must be enabled** — some hardened Debian/Ubuntu kernels ship
  `kernel.unprivileged_userns_clone=0`, which breaks the Steam Linux Runtime and
  EAC/BattlEye games. The System Check catches this and offers a fix.
- **The esync FD-limit fix needs a re-login** — raising `DefaultLimitNOFILE`
  won't take effect until you log out and back in.
- **`ananicy-cpp` and GameMode both manage process niceness** — CachyOS ships
  `ananicy-cpp` on by default, and stacking it with GameMode + Goblin's own
  renice makes three tools fight over the same knob. The System Check warns
  when it sees this; new profiles start with renice off while `ananicy-cpp` is
  active, and you can turn off **Wrap with GameMode** per game.
- **KDE:** if the app icon looks stale after install, run
  `kbuildsycoca6 --noincremental` and restart Plasma (or log out/in).

---

## Using it

1. The daemon starts automatically with your desktop session.
2. **First launch runs a short wizard** — it checks your system, offers the safe
   fixes, and shows exactly where to paste the launch wrapper for your launcher.
3. **Set your game's launch wrapper** — this is the important step; without it
   nothing captures the Proton log and env-var injection is skipped:
   - **Steam:** game → Properties → **Launch Options** → `goblin-run %command%`
   - **Lutris:** game → Configure → System options → **Command prefix** → `goblin-run`
   - **Heroic:** Settings → **Wrapper command** → `goblin-run`
4. Open **Goblin Mode Pro** from your app menu (or the tray icon).
5. **Games** page → the game should already be listed (auto-detect is on). Turn on
   the tweaks you want, or add an executable by hand. Every toggle has an ⓘ
   button with a plain-language explanation.

### From the terminal

`goblin-mode-pro-cli` talks to the running daemon over the session bus — handy
over SSH or in scripts:

```sh
goblin-mode-pro-cli status              # what's boosting right now
goblin-mode-pro-cli health              # the 0–10 readiness score
goblin-mode-pro-cli boost / unboost     # force performance mode on/off
goblin-mode-pro-cli benchmark "Wow.exe" # arm a benchmark run
goblin-mode-pro-cli sessions            # recent session / benchmark report cards
goblin-mode-pro-cli preflight --fix     # run the System Check, apply safe fixes
goblin-mode-pro-cli report              # the Markdown bug report
goblin-mode-pro-cli setup               # the full-setup export
goblin-mode-pro-cli compare "Wow.exe"   # diff the last two sessions for a game
goblin-mode-pro-cli works-for-me "Wow.exe" --note "runs great"  # share what worked
goblin-mode-pro-cli gamescope-session   # launch Steam Big Picture in its own gamescope session
```

### Sharing a profile with a friend

On any game row, the **Export** button (↗) writes the profile to a `.json` file.
Your friend uses the **Import** button (📂) at the top of the Games list to load
it. Handy for "here's the exact config that fixed the stutter in <game>".

The **↓ community** button next to it downloads a small set of known-good
starting profiles (kept in the [`profiles/`](profiles/) directory of this repo)
straight from GitHub — anonymous HTTPS GET, nothing uploaded, and you confirm
before anything is applied. Send a pull request to `profiles/` to add your own.

---

<details>
<summary><b>Technical reference</b> (architecture, the privilege model, every module)</summary>

### Architecture

Three cooperating processes:

```
 ┌─────────────────────────┐        session bus         ┌──────────────┐
 │  goblin-mode-pro-daemon  │◄──────  com.goblinmode.Pro ─►│     GUI      │
 │  (systemd --user)        │                             │ GTK4 / Adw   │
 │  Observer · Diagnostics  │                             └──────────────┘
 │  LogWatch · Tray · Bridge│
 └───────────┬─────────────┘
             │ system bus  com.goblinmode.ProHelper  (polkit-gated)
             ▼
 ┌─────────────────────────┐
 │ goblin-mode-pro-helper   │  governor · EPP · renice · RAPL PL1/PL2 · sysctls
 │ (systemd system, root)   │  snapshots originals to /run/goblin-mode-pro/state.json
 └─────────────────────────┘
```

- **Daemon** (unprivileged, `systemd --user`): the `psutil` poll loop that
  detects games, the diagnostics sampler, the log watcher, the tray icon. Applies
  the payload once on launch and reverts once on exit.
- **Helper** (root, `systemd` system service): does *only* the privileged sysfs
  writes, each gated by polkit and re-validated against a fixed allowlist.
- **GUI** (on demand): a pure D-Bus client of the daemon. No privileged code.

Config is a single JSON file, `~/.config/goblin-mode-pro/config.json`, shared by
all three. Incidents: `~/.local/share/goblin-mode-pro/incidents.jsonl`. Captured
game logs: `~/.local/share/goblin-mode-pro/logs/`.

### Modules

| Module | Behaviour |
|---|---|
| **Observer** | `psutil` poll loop. Per-profile state machine: `ABSENT→PRESENT` applies, `PRESENT→ABSENT` reverts. Global tweaks are refcounted across concurrent games. |
| **Auto-detect** | Recognises any game, not just the profile list — launcher tags (Steam/Lutris/Heroic) plus a signal stack (DRM `fdinfo` GPU activity, `libSDL2`/`libwine` links, a DE blocklist). New games get a default profile and a **Keep / Ignore** notification. |
| **Performance Payload** | governor→`performance`, EPP→`performance`, TDP raise (RAPL PL1/PL2 or `ryzenadj`, with a lower on-battery preset), `renice` (default `-5`), CPU-affinity pinning (P-cores / CCD), tearing + VRR (KWin `AllowTearing`/`kscreen-doctor`, or Hyprland `hyprctl keyword`) with optional per-output restriction on KDE, an internal-panel refresh-rate cap, Focus mode, MangoHud config, runner env vars, per-game GPU-driver tuning, Intel/AMD undervolt re-apply, preemptive fan spin-up, gamescope. Global tweaks are refcounted across concurrent games. |
| **Benchmark comparison & cards** | `benchmarkcard.py` diffs two sessions' metrics (correctly treating temps/stutter as "lower is better") and renders a small Cairo report-card PNG; JSON export is the session record as-is. |
| **Prometheus exporter** | Off by default (`Settings.prometheus_textfile`); writes the Dashboard's own metrics as a node_exporter textfile-collector `.prom` file on the daemon's existing status-broadcast path. |
| **Regression tracking** | On each game exit, summarises the MangoHud frame log (avg / median / 1% low FPS, duration, active tweaks) into `sessions.jsonl` and compares it to the recent history for that game. A >10% swing in the 1% low or average is flagged on the Diagnostics page. |
| **Capabilities** | One-time hardware probe (CPU vendor, cpufreq driver, EPP/RAPL availability, GPU vendors, `nvidia-smi`, compositor, distro, package manager, kernel flavour, handheld model, controllers, GameMode, `intel-undervolt`, `gpu-screen-recorder`). Attached to daemon status so the GUI labels or hides features that don't apply. |
| **Benchmark mode** | Arm a game; on exit the frame log is summarised into a report card (avg / 1% / 0.1% low FPS, frame-time stutter %, peak temps) and stored as a session. |
| **Web lookups** *(GUI only)* | ProtonDB tier and AreWeAntiCheatYet verdict — anonymous HTTPS GET to a fixed two-host allowlist, size-capped, disk-cached. The daemon and helper make **zero** network connections. |
| **Proton tools** | Discovers custom Proton/Wine builds and every shader-cache location with sizes; `Clear` deletes a listed cache's contents only. |
| **First-run wizard** | Shown once (marker file). System check + safe fixes → launcher wrapper → done. |
| **CLI** | `goblin-mode-pro-cli` — a headless session-bus client (status / boost / health / benchmark / sessions / preflight / report / setup / games / compare / works-for-me / gamescope-session). |
| **System Check** | Pre-flight panel: `vm.max_map_count`, esync FD limit, split-lock mitigation, `nvidia-drm.modeset`, THP, `compaction_proactiveness`, swappiness, kernel fsync support, `user.max_user_namespaces` **and** the Debian/Ubuntu `kernel.unprivileged_userns_clone` (Steam Runtime + anti-cheat), Vulkan ICD, gamemode/MangoHud presence, an `ananicy-cpp` niceness-conflict warning, plus an anti-cheat status note. Safe fixes are one-click; the `sysctl.d` / kernel-param text is shown for permanence. |
| **Proton log analyzer** | ~16 known failure patterns → plain-language cause + fix, run on the captured Wine/Proton log. |
| **Frame-rate watchdog** | Per game. Logs FPS via MangoHud; on a dip that persists ~4 s (default ≤22 fps or <50% of the frozen pre-dip baseline) takes a fresh deep GPU snapshot and files an `fps_dip` incident classified *withheld* (alt-tab / loading), *GPU-bound scene*, *CPU-bound scene*, or *starved* (a real fault, with ranked causes). Checks whether VRAM was released after exit (leak detection). |
| **MangoHud Integrator** | Round-trips `MangoHud.conf` (or a per-game `<exe>.conf`), touching only its managed block. |
| **Diagnostic Engine** | While a game runs: CPU pkg temp, per-core load, package power vs PL1/PL2, GPU load/temp, throttle flags. Debounced incidents on throttle onset or GPU driver fault. |
| **Bug report** | System info + pre-flight + last incident + log analysis + active tweaks → Markdown on the clipboard. |
| **LLM Export** | Packages an incident (+ metric window, FPS trace, GPU state, log tail, active tweaks) into structured JSON wrapped in a diagnostic system prompt. |

### Privilege model

| polkit action | covers | default on the active session |
|---|---|---|
| `com.goblinmode.pro.manage-performance` | governor, EPP, renice, **raising** RAPL PL1/PL2 / ryzenadj TDP, re-applying your `intel-undervolt` / Curve-Optimizer offsets, handing fan control back to the EC | allowed without a prompt |
| `com.goblinmode.pro.manage-kernel-tunables` | persistent sysctls from the System Check (and their **Undo**), the `nvidia-drm.modeset` modprobe write | prompts for admin auth |
| `com.goblinmode.pro.manage-hardware-thermal` | taking manual control of the fans (preemptive spin-up) | prompts for admin auth |

Lowering a power limit isn't offered over the bus at all — `SetPowerLimits`
has a 6 W floor, since it exists to *raise* the cap, and driving it to a few
watts would be a silent local slow-down. Fan spin-up can only ever *increase*
duty (40 % floor).

`manage-performance` is silent on the active session so a boost applies the
instant a game launches. To require a prompt there too, set
`<allow_active>auth_admin_keep</allow_active>` in
`/usr/share/polkit-1/actions/com.goblinmode.pro.policy`.

Input is constrained at the helper: the governor must be one the kernel
advertises; `renice` only raises priority and only for a process the caller owns;
RAPL writes are clamped to the firmware maximum; sysctl keys are a fixed
allowlist with per-key numeric ranges and the target path is confirmed under
`/proc/sys/`. See [SECURITY.md](SECURITY.md) for the full threat model.

### Runner variables

| Toggle | Environment |
|---|---|
| NVAPI | `PROTON_ENABLE_NVAPI=1`, `DXVK_ENABLE_NVAPI=1` |
| Force Fsync | `WINEFSYNC=1` |
| Disable Esync | `PROTON_NO_ESYNC=1` |
| Async shader compile | `DXVK_ASYNC=1` |

Plus the per-game **GPU driver tuning** toggles (only those matching your GPU are
shown):

| Toggle | Environment |
|---|---|
| NVIDIA: threaded GL | `__GL_THREADED_OPTIMIZATIONS=1` |
| NVIDIA: unlimited shader cache | `__GL_SHADER_DISK_CACHE=1`, `__GL_SHADER_DISK_CACHE_SKIP_CLEANUP=1` |
| NVIDIA: force G-SYNC / VRR | `__GL_GSYNC_ALLOWED=1`, `__GL_VRR_ALLOWED=1` |
| AMD: Mesa glthread | `mesa_glthread=true` |
| AMD: RADV pipeline library / NGG culling / ray-tracing | `RADV_PERFTEST=gpl,nggc,rt` (comma-merged) |

The launch wrapper imports all of these as strict `NAME=VALUE` lines — no `eval`,
no `source`.

### Compositor tweaks (Wayland note)

KWin cannot *suspend* compositing on Wayland, so on **KDE Wayland** the
"compositor" tweaks instead enable **Allow Tearing** (`kwriteconfig6` + a KWin
reconfigure) and switch **VRR** to `automatic` on VRR-capable outputs
(`kscreen-doctor`), reverting both on exit. On **KDE + X11** it does a real
compositor suspend/resume. On **GNOME / wlroots / unknown** it no-ops with a log
line — `gamemoderun` covers the rest.

### Development

```sh
python -m goblinmode.daemon -v                        # daemon, foreground
python -m goblinmode.gui.app                           # GUI
goblin-mode-pro-daemon --write-wrapper
goblin-mode-pro-daemon --print-env-for -- /path/to/game
goblin-mode-pro-daemon --print-gamescope -- /path/to/game
goblin-mode-pro-daemon --revert
```

Source is under `src/goblinmode/`; `daemon.py` wires the components together,
`payload.py` orchestrates apply/revert, and the privileged helper is
`helper/goblin_helper.py`.

Tests are stdlib `unittest` (no third-party dependency):

```sh
python -m unittest discover -s tests
```

They cover the pure logic — config validation, capability parsing, the session
/ regression maths, the CSV and MangoHud parsers, env-var filtering, gamescope
args, the community-fetch host guard, and the helper's polkit-gate dispatch
(every `_MUTATING` method denied before its underlying function runs, stubbed
`_check_authorized`, no real D-Bus). 190 tests. GitHub Actions also
import-checks every module, constructs the real `MainWindow` headlessly under
Xvfb (`tests/gui_smoke.py`), and validates the `profiles/` JSON on each push.

`scripts/make-screenshots.py` and `scripts/make-demo.py` regenerate the README visuals
by rendering the real GUI off-screen (needs a running daemon; `make-demo.py`
also needs `ffmpeg`).

</details>

---

## Contributing & what's next

Ideas, bug reports and PRs are welcome. See [`CONTRIBUTING.md`](CONTRIBUTING.md)
to get started. [`ROADMAP.md`](ROADMAP.md) tracks where this goes next — 1.2.0
cleared its entire post-1.1 menu (benchmark comparison, AMD Curve Optimizer
undervolt, the battery/AC auto-switch, full i18n, telemetry-free "works for
me" reports and more), so there's nothing currently proposed beyond what's
shipped. 👍 an item on the
[issue tracker](https://github.com/Bvaughan7/goblin-mode-pro/issues) or open a
new one.

See [CHANGELOG.md](CHANGELOG.md) for release history.

## Reporting a security issue

Please use the repository's **Security advisories** page ("Report a
vulnerability"), not a public issue. See [SECURITY.md](SECURITY.md).
