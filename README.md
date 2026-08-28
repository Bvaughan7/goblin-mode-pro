# Goblin Mode Pro

A lightweight native-Linux gaming "tinkerer" for MMORPGs (World of Warcraft via
Proton, native RuneScape). It watches for a game launching, applies a set of
performance tweaks, and reverts them cleanly when the game exits — then gets out
of the way.

Built for **CachyOS / Arch** on a **Dell G7** (Intel Comet Lake + NVIDIA RTX
2060), KDE Plasma 6 on Wayland.

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

Governor / EPP / negative renice / RAPL power limits are **root-only**. They are
handled by a tiny **system** D-Bus service, `goblin-mode-pro-helper`, gated by
the polkit action `com.goblinmode.pro.manage-performance`.

The shipped policy sets `allow_active=yes` — **no password prompt** while you are
the active session, so a boost applies the instant a game launches. To require a
prompt instead, edit
`/usr/share/polkit-1/actions/com.goblinmode.pro.policy` and set
`<allow_active>auth_admin_keep</allow_active>`.

If the helper is missing the daemon still runs in **limited mode**: MangoHud,
tearing, diagnostics and LLM export all work; governor/renice do not.

---

## Install

```sh
git clone <repo> goblin-mode-pro && cd goblin-mode-pro
./install.sh            # full install (prompts for sudo for the root bits)
# or
./install.sh --user    # skip the root helper (limited mode)
./install.sh --uninstall
```

Dependencies (all from the official repos):

```
python python-gobject python-psutil python-pillow python-pystray
gtk4 libadwaita wl-clipboard   # mangohud gamemode recommended
```

Running the test suite needs `python-pytest` (`sudo pacman -S python-pytest`),
then `pytest` from the repo root.

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
line (rely on `gamemoderun`, which the wrapper already uses).

The Dell G7 internal panel is `Vrr: incapable`, so Adaptive Sync only does
something with an external VRR display attached.

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
keeps the firmware value. Raising them past what the G7 chassis can cool just
trades one throttle for another — watch the Diagnostics graph. Restored to the
snapshot on game exit.

---

## Development

```sh
python -m goblinmode.daemon -v        # run the daemon in the foreground
python -m goblinmode.gui.app          # run the GUI
goblin-mode-pro-daemon --write-wrapper
goblin-mode-pro-daemon --print-env-for /path/to/Wow.exe
goblin-mode-pro-daemon --revert
```

Source layout under `src/goblinmode/` — see `daemon.py` for the wiring and
`payload.py` for the apply/revert orchestration.
