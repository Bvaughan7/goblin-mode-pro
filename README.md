# Goblin Mode Pro

A lightweight native-Linux gaming utility. It detects a game launching, applies a
set of system performance tweaks, and reverts them cleanly on exit — then stays
out of the way. It also runs a diagnostic engine that watches thermals, frame
rate and the Wine/Proton log, and turns a problem into a shareable report.

A headless `systemd --user` daemon does the work; a GTK4 / Libadwaita window and
a tray icon are opened on demand. Anything requiring root is delegated to a small
polkit-gated helper.

Primary target: **Arch-based** distributions with **KDE Plasma on Wayland** and
an NVIDIA or AMD GPU. Developed and tested on CachyOS.

---

## What it does

| Module | Behaviour |
|---|---|
| **Observer** | `psutil` poll loop (systemd *user* service). Applies the payload once on launch, reverts once on exit. |
| **Auto-detect** | Recognises *any* game, not just the profile list — Steam / Lutris / Heroic launcher tags, plus a signal stack (DRM `fdinfo` GPU activity, `libSDL2`/`libwine` links, a desktop-environment blocklist). New games get a default profile and a tray notification with **Keep / Ignore**. Toggle off in **Games**. |
| **Performance Payload** | CPU governor → `performance` + EPP → `performance`; optional RAPL **PL1/PL2 raise**; `renice` the game (default `-5`); KWin **AllowTearing** + **Adaptive Sync/VRR**; **Focus mode** (pause the file indexer, Do Not Disturb, inhibit idle); MangoHud config; runner env vars. Global tweaks are refcounted across games. |
| **System Check** | A pre-flight panel: `vm.max_map_count` (UE5 crash guard), esync file-descriptor limit, split-lock mitigation, `nvidia-drm.modeset`, transparent hugepages, memory compaction, swap, Vulkan ICD, gamemode/MangoHud presence — each with a status and, where safe, a one-click fix (plus the `sysctl.d` / kernel-param text to make it permanent). |
| **Proton log analyzer** | 16 known Linux-gaming failure patterns (esync FD ceiling, VC++/wine-mono missing, `VK_ERROR_DEVICE_LOST`, VRAM OOM, anti-cheat not initialising, shader-cache unwritable, …) → plain-language cause + fix. Runs on the captured Wine/Proton log. |
| **Bug report** | One button gathers system info + the pre-flight results + the last incident + the log analysis + active tweaks into a Markdown report on your clipboard, ready to paste into a forum thread or issue. |
| **MangoHud Integrator** | Round-trips `~/.config/MangoHud/MangoHud.conf` (or a per-game `<exe>.conf`), touching only its own managed block. Toggles: overlay, FPS, CPU/GPU temp, RAM, frame timing. |
| **Diagnostic Engine** | While a game runs: CPU pkg temp, per-core load, package power vs PL1/PL2, GPU load/temp, CPU & GPU throttle flags. Raises a debounced *incident* on a throttle onset or a GPU driver fault seen in the Proton log. |
| **Frame-rate watchdog** | Optional per game. Logs FPS via MangoHud and, on a sustained extreme dip (default ≤ 22 fps or < 50% of the recent median), snapshots deep GPU state — VRAM used/free, PCIe link gen/width, power-state, core-clock collapse — into an `fps_dip` incident with a ranked list of likely causes. After the game exits it checks whether VRAM was actually released (still allocated → a driver leak that a reboot clears). Built for DX12 / VKD3D-Proton stalls that thermal monitoring can't explain. |
| **LLM Export** | Packages an incident (+ metric window, FPS trace, GPU state, log tail, active tweaks) into a structured JSON payload wrapped in a diagnostic system prompt, and copies it to the clipboard. |

### Privilege model

CPU governor / EPP, `renice`, RAPL power limits and the pre-flight kernel
tunables are **root-only**. They are handled by a small **system** D-Bus service,
`goblin-mode-pro-helper`, running as root under a hardened systemd unit. Every
mutating call is authorised through polkit:

| polkit action | covers | default on the active session |
|---|---|---|
| `com.goblinmode.pro.manage-performance` | governor, EPP, renice, RAPL PL1/PL2 | allowed without a prompt |
| `com.goblinmode.pro.manage-kernel-tunables` | persistent sysctls from the System Check | prompts for admin auth |

Inputs are constrained at the helper: the governor must be one the kernel
advertises, `renice` only raises priority and only for a process the caller owns,
RAPL writes are clamped to the firmware maximum, and sysctl keys are a fixed
allowlist with per-key numeric ranges. `manage-performance` is silent on the
active session so a boost applies the instant a game launches — to require a
prompt there too, set `<allow_active>auth_admin_keep</allow_active>` in
`/usr/share/polkit-1/actions/com.goblinmode.pro.policy`.

Without the helper the daemon runs in **limited mode**: auto-detect, MangoHud,
compositor tweaks, diagnostics, the log analyzer and reports all work; the CPU
governor, `renice` and power limits do not.

---

## Install

```sh
git clone <repo> goblin-mode-pro && cd goblin-mode-pro
./install.sh            # full install (prompts for sudo for the root bits)
# or
./install.sh --user    # skip the root helper (limited mode)
./install.sh --uninstall
```

Dependencies (Arch package names, all from the official repos):

```
python python-gobject python-psutil python-pillow python-pystray
gtk4 libadwaita wl-clipboard        # mangohud and gamemode recommended
```

---

## Usage

* The daemon starts with your session (`systemctl --user status goblin-mode-pro`).
  If a game isn't detected, first check `systemctl --user is-active goblin-mode-pro`
  and `journalctl --user -u goblin-mode-pro -f`.
* Open the GUI from the app menu or `goblin-mode-pro`, or the tray icon's
  **Open Goblin Mode Pro**.
* **Games** page → add your executables, tune per-game toggles.
* For Proton games, set the Steam **launch options** to:

  ```
  goblin-run %command%
  ```

  This lets Goblin Mode Pro inject the runner variables and capture the
  Wine/Proton log for fault detection. The wrapper also runs the game through
  `gamemoderun` when available.

### Runner variables

Per-game toggles in the GUI map to:

| Toggle | Env |
|---|---|
| NVAPI | `PROTON_ENABLE_NVAPI=1`, `DXVK_ENABLE_NVAPI=1` |
| Force Fsync | `WINEFSYNC=1` |
| Disable Esync | `PROTON_NO_ESYNC=1` |
| Async shader compile | `DXVK_ASYNC=1` |

---

## Architecture

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
 │ goblin-mode-pro-helper   │  governor · EPP · renice · RAPL PL1/PL2
 │ (systemd system, root)   │  snapshots to /run/goblin-mode-pro/state.json
 └─────────────────────────┘
```

* **Config**: `~/.config/goblin-mode-pro/config.json` (plain JSON, shared by all
  three processes).
* **Incidents**: `~/.local/share/goblin-mode-pro/incidents.jsonl`.
* **Captured game logs**: `~/.local/share/goblin-mode-pro/logs/`.

### Wayland note

KWin cannot *suspend* compositing on Wayland, so the "compositor" tweaks instead:

* enable **Allow Tearing** (immediate presentation) via `kwriteconfig6` + a KWin
  reconfigure, and
* switch **Adaptive Sync / VRR** to `automatic` on any VRR-capable output via
  `kscreen-doctor`

…for the duration of the game, restoring both on exit. On **KDE + X11** it does a
real compositor suspend/resume; on GNOME/wlroots/unknown it no-ops with a log
line (`gamemoderun`, which the wrapper already uses, covers the rest).

Adaptive Sync only takes effect on a VRR-capable output; a display reported as
`Vrr: incapable` by `kscreen-doctor` is skipped.

### Frame-rate watchdog

Enable it per game under **Games → \<game\> → MangoHud → Frame-rate watchdog**.
It needs MangoHud injected into the game (the Steam launch wrapper does this
automatically; on **Lutris** tick "Enable MangoHud" or set `MANGOHUD=1`). GMP
then writes continuous CSV logging into `MangoHud.conf` and tails
`~/.local/share/goblin-mode-pro/mangohud/`.

When the trailing ~2.5 s average frame rate drops to/under the **Dip threshold**
(default 22) or below half the recent median, it logs an `fps_dip` incident
holding the FPS trace plus an `nvidia-smi` snapshot — VRAM, PCIe link
gen/width, power-state, clocks — and a ranked guess at the cause (VRAM
exhaustion / PCIe down-training / stuck power-state / clock collapse). Export it
with **Diagnostics → Export for AI Analysis**.

*First launch after enabling won't log* — MangoHud reads its config at start and
GMP writes it a few seconds later. Relaunch once.

### Power limits (RAPL)

Per game, off by default. `PL1` (sustained) and `PL2` (burst) are in watts; `0`
keeps the firmware value. Raising them past what the chassis can cool just trades
a power-limit throttle for a thermal one — watch the Diagnostics graph. Clamped
to the firmware maximum and restored to the snapshot on game exit.

---

## Security notes

- The privileged helper runs as root but under a hardened unit
  (`CapabilityBoundingSet=CAP_SYS_NICE`, `NoNewPrivileges`, `ProtectSystem=strict`
  with an explicit `ReadWritePaths` allowlist, `PrivateNetwork`/`IPAddressDeny`,
  a syscall filter). It imports only the standard library and PyGObject.
- Every helper method validates its arguments (enum / allowlist / range / process
  ownership) independently of the caller.
- The daemon <-> GUI bridge is on the **session** bus (per-user). The helper name
  can only be owned by root (enforced by the bus policy), so it cannot be
  impersonated.
- Profile `exe` values are rejected if they contain a path separator, `..`, or a
  control character; per-game file names are derived through a separate slug
  function. User regex patterns are length-capped and matched against
  length-bounded strings (backtracking guard).
- The launch wrapper imports runner variables as strict `NAME=VALUE` lines — no
  `eval`, no `source`.

Report a suspected vulnerability privately via the repository's security advisory
page rather than a public issue.

## Development

```sh
python -m goblinmode.daemon -v                       # daemon, foreground
python -m goblinmode.gui.app                          # GUI
goblin-mode-pro-daemon --write-wrapper
goblin-mode-pro-daemon --print-env-for -- /path/to/game
goblin-mode-pro-daemon --revert
```

Source is under `src/goblinmode/`; `daemon.py` wires the components together and
`payload.py` orchestrates apply/revert. The privileged helper is `helper/goblin_helper.py`.
