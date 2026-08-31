# How it works

The internals: the three processes, what each module does, the privilege
model, and the environment variables and compositor calls it makes. If you
just want to *use* Goblin Mode Pro, start with
[Getting started](getting-started.md) instead.

## Architecture

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

## Modules

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

## Privilege model

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
`/proc/sys/`. See [SECURITY.md](https://github.com/Bvaughan7/goblin-mode-pro/blob/main/SECURITY.md)
for the full threat model.

## Runner variables

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

## Compositor tweaks (Wayland note)

KWin cannot *suspend* compositing on Wayland, so on **KDE Wayland** the
"compositor" tweaks instead enable **Allow Tearing** (`kwriteconfig6` + a KWin
reconfigure) and switch **VRR** to `automatic` on VRR-capable outputs
(`kscreen-doctor`), reverting both on exit. On **KDE + X11** it does a real
compositor suspend/resume. On **GNOME / wlroots / unknown** it no-ops with a log
line — `gamemoderun` covers the rest.

## Development

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


---

