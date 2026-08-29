# Per-game settings

Open **Games**, expand a profile. Every switch:

## Performance
- **Process priority (renice)** — the game gets the CPU before background tasks.
- **Pin to CPU cores** — appears only on hybrid / chiplet CPUs. Fast cores, or one CCD.
- **CPU governor boost** — `performance` + EPP `performance` while the game runs.
- **Compositor: allow tearing** — lower input lag on KDE, or Hyprland.
- **Compositor: adaptive sync (VRR)** — for VRR displays; on KDE you can
  restrict it to specific outputs instead of every VRR-capable monitor.
  Hyprland's VRR is compositor-wide (no per-monitor equivalent there yet).
- **Focus mode** — pause the file indexer, Do Not Disturb, inhibit idle.
- **TDP / power limit** — presets (15/25/35/45/65 W) + fine spinners. Intel RAPL
  or AMD `ryzenadj`. On a handheld, new profiles start with a starter preset for
  your model, and a lower one is used automatically while on battery.
- **Re-apply my undervolt on launch** — runs `intel-undervolt apply` (Intel) or
  re-applies your `/etc/goblin-mode-pro/amd-undervolt.conf` Curve Optimizer
  offsets (AMD, `ryzenadj`) so they survive suspend / thermald. Goblin Mode Pro
  never picks the values. Both are behind an "I understand the risk" confirm.
- **Refresh-rate cap** — caps the internal panel's refresh rate for this game
  (mainly for handhelds: Deck 40/50/60, Ally up to 120…). No-ops if no matching
  mode is advertised.
- **Spin up the fans on launch** *(opt-in)* — forces every controllable fan to a
  high duty cycle on launch, reverts on exit. Only shows up where the EC
  actually exposes a writable `pwm` control — most laptops/handhelds don't.

## GPU driver tuning
Vendor-specific env vars — NVIDIA `__GL_THREADED_OPTIMIZATIONS` / shader cache /
force-VRR, AMD `RADV_PERFTEST=gpl,nggc` / `mesa_glthread`, Intel `ANV_GPL`.

## MangoHud
Overlay toggles, a per-game `<exe>.conf`, and the **frame-rate watchdog** (see
[Diagnostics](diagnostics.md)). **Auto-clip a problem** keeps a 30 s replay
buffer (needs `gpu-screen-recorder`) and saves it to `~/Videos` on a cliff.

## Runner variables
NVAPI, Force Fsync, Disable Esync, async shader compile.

## gamescope
Resolution, FPS cap, FSR / NIS upscaling, HDR.

## Compatibility check
Enter the Steam AppID → ProtonDB tier and anti-cheat status
(AreWeAntiCheatYet), fetched on demand.

## Share what worked
Opens a pre-filled GitHub issue with your system info and this game's tuning
settings (no undervolt/fan-control values, no usernames or paths) — nothing is
sent anywhere until you post it yourself. Telemetry-free: no server, no
account. See [`goblin-mode-pro-cli works-for-me`](cli.md) for the headless
equivalent.

## Sharing
The **↗** button exports a profile to JSON; **📂** imports one; **↓** browses the
[community starter profiles](https://github.com/Bvaughan7/goblin-mode-pro/tree/main/profiles).
