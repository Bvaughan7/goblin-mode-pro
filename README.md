<div align="center">

<img src="docs/logo.png" alt="Goblin Mode Pro" width="360">

# Goblin Mode Pro

### A one-switch performance helper for Linux gaming.

Goblin Mode Pro notices when a game starts, flips your system settings to their
"I'm gaming now" positions, and puts every one of them back the moment you quit —
then watches the temps, frame rate and Proton log and tells you, in plain
language, why something broke.

![version](https://img.shields.io/badge/version-0.1.0-e8952c)
![python](https://img.shields.io/badge/python-3.11+-3f7fbf)
![license](https://img.shields.io/badge/license-MIT-4E6A24)
![helper](https://img.shields.io/badge/root_helper-polkit_hardened-4E6A24)
![distros](https://img.shields.io/badge/distros-any_systemd-266F64)

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
| **MangoHud overlay** | Not shown | Shows FPS / temps on screen if you want it | See what's actually happening |
| **gamescope** *(optional)* | Not used | Runs the game inside gamescope | Rock-solid FPS cap, FSR/NIS upscaling, and alt-tab that doesn't break the game |

"Global" changes (CPU speed, tearing) are shared — they switch on when the first
game starts and switch off only when the last one quits.

### And while you play, it watches for problems

- **System Check** — a one-page scan of the kernel settings that make Linux games
  crash or stutter (the infamous `vm.max_map_count` that makes Unreal Engine 5
  games crash, the file-descriptor limit that breaks Proton's esync, user
  namespaces that anti-cheat needs, and about a dozen more). Each item is marked
  **PASS / CHECK / ACTION**, and the safe ones have a one-click fix.
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

---

## Will this work on my system?

**The core FPS features work on any Linux PC with systemd.** A few extras depend
on your hardware, and Goblin Mode Pro detects that and greys out what doesn't
apply — it never just fails silently.

| Your setup | What you get |
|---|---|
| **Any systemd Linux** — Arch, CachyOS, Debian, Ubuntu, Fedora, Nobara, openSUSE, Pop!_OS… | Everything below that your hardware supports. The installer detects your package manager. |
| **Intel CPU** | All CPU features, including the power-limit boost. |
| **AMD CPU** | Everything. Governor, energy hint and priority work exactly as on Intel; laptop TDP control works if you install `ryzenadj` (the installer then loosens the helper sandbox just enough for it). Core pinning uses your CCD layout. |
| **NVIDIA GPU** | Everything, including the deep "why did my FPS drop" GPU snapshot. |
| **AMD or Intel GPU** | Everything **except** that deep snapshot — you still get GPU temperature and load. |
| **KDE Plasma** | Everything, including the tearing / VRR compositor tweaks. |
| **GNOME, Hyprland, Sway, other** | Everything **except** the KDE-specific compositor tweaks. GameMode (which the launch wrapper uses automatically) covers most of that ground. |
| **No systemd** (Void with runit, Gentoo/OpenRC, Alpine…) | Not supported — the daemon and the privileged helper are both systemd units. |

> **Is it "Intel only"?** No. That's a common misconception. Only the **CPU
> power-limit raise** is Intel-specific (it uses Intel's RAPL interface). Every
> other performance feature is vendor-neutral and works fine on AMD.

---

## Install

```sh
git clone https://github.com/<you>/goblin-mode-pro
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

You need Python 3, PyGObject, GTK 4, libadwaita, and `psutil`. Package names:

| Distro | Command |
|---|---|
| Arch / CachyOS | `pacman -S python-gobject python-psutil gtk4 libadwaita python-pystray wl-clipboard mangohud gamemode gamescope` |
| Debian / Ubuntu | `apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 python3-psutil wl-clipboard mangohud gamemode gamescope` |
| Fedora / Nobara | `dnf install python3-gobject python3-psutil gtk4 libadwaita wl-clipboard mangohud gamemode gamescope` |
| openSUSE | `zypper install python3-gobject python3-psutil gtk4-tools libadwaita wl-clipboard mangohud gamemode gamescope` |

`mangohud`, `gamemode` and `gamescope` are optional but recommended — the overlay,
the frame-rate watchdog and the gamescope integration need them.

---

## Using it

1. The daemon starts automatically with your desktop session.
2. Open **Goblin Mode Pro** from your app menu (or the tray icon).
3. **Games** page → add your game's executable, turn on the tweaks you want.
   Auto-detect is on by default, so most games get picked up without you adding
   anything.
4. **For Steam games**, set the game's **Launch Options** to:

   ```
   goblin-run %command%
   ```

   This lets Goblin Mode Pro pass the Proton/Wine switches to the game and
   capture the log for the crash analyzer. (It also runs the game through
   `gamemoderun` automatically.)

5. **For Lutris / Heroic**, add `goblin-run` as a command prefix / wrapper.

### Sharing a profile with a friend

On any game row, the **Export** button (↗) writes the profile to a `.json` file.
Your friend uses the **Import** button (📂) at the top of the Games list to load
it. Handy for "here's the exact config that fixed the stutter in <game>".

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
| **Performance Payload** | governor→`performance`, EPP→`performance`, TDP raise (RAPL PL1/PL2 or `ryzenadj`), `renice` (default `-5`), CPU-affinity pinning (P-cores / CCD), KWin `AllowTearing` + VRR via `kscreen-doctor`, Focus mode, MangoHud config, runner env vars, gamescope. |
| **Regression tracking** | On each game exit, summarises the MangoHud frame log (avg / median / 1% low FPS, duration, active tweaks) into `sessions.jsonl` and compares it to the recent history for that game. A >10% swing in the 1% low or average is flagged on the Diagnostics page. |
| **Capabilities** | One-time hardware probe (CPU vendor, cpufreq driver, EPP/RAPL availability, GPU vendors, `nvidia-smi`, compositor, distro, package manager). Attached to daemon status so the GUI labels or hides features that don't apply. |
| **System Check** | Pre-flight panel: `vm.max_map_count`, esync FD limit, split-lock mitigation, `nvidia-drm.modeset`, THP, `compaction_proactiveness`, swappiness, kernel fsync support, user namespaces (Steam Runtime + anti-cheat), Vulkan ICD, gamemode/MangoHud presence, plus an anti-cheat status note. Safe fixes are one-click; the `sysctl.d` / kernel-param text is shown for permanence. |
| **Proton log analyzer** | ~16 known failure patterns → plain-language cause + fix, run on the captured Wine/Proton log. |
| **Frame-rate watchdog** | Per game. Logs FPS via MangoHud; on a sustained extreme dip (default ≤22 fps or <50% of the recent median) snapshots deep GPU state into an `fps_dip` incident with ranked likely causes. Checks whether VRAM was released after exit (leak detection). |
| **MangoHud Integrator** | Round-trips `MangoHud.conf` (or a per-game `<exe>.conf`), touching only its managed block. |
| **Diagnostic Engine** | While a game runs: CPU pkg temp, per-core load, package power vs PL1/PL2, GPU load/temp, throttle flags. Debounced incidents on throttle onset or GPU driver fault. |
| **Bug report** | System info + pre-flight + last incident + log analysis + active tweaks → Markdown on the clipboard. |
| **LLM Export** | Packages an incident (+ metric window, FPS trace, GPU state, log tail, active tweaks) into structured JSON wrapped in a diagnostic system prompt. |

### Privilege model

| polkit action | covers | default on the active session |
|---|---|---|
| `com.goblinmode.pro.manage-performance` | governor, EPP, renice, RAPL PL1/PL2, ryzenadj TDP | allowed without a prompt |
| `com.goblinmode.pro.manage-kernel-tunables` | persistent sysctls from the System Check | prompts for admin auth |

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

The launch wrapper imports these as strict `NAME=VALUE` lines — no `eval`, no
`source`.

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

</details>

---

## Reporting a security issue

Please use the repository's **Security advisories** page ("Report a
vulnerability"), not a public issue. See [SECURITY.md](SECURITY.md).
