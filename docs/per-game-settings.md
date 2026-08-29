# Per-game settings

Open **Games**, expand a profile. Every switch:

## Performance
- **Process priority (renice)** — the game gets the CPU before background tasks.
- **Pin to CPU cores** — appears only on hybrid / chiplet CPUs. Fast cores, or one CCD.
- **CPU governor boost** — `performance` + EPP `performance` while the game runs.
- **Compositor: allow tearing** — lower input lag on KDE.
- **Compositor: adaptive sync (VRR)** — for VRR displays.
- **Focus mode** — pause the file indexer, Do Not Disturb, inhibit idle.
- **TDP / power limit** — presets (15/25/35/45/65 W) + fine spinners. Intel RAPL
  or AMD `ryzenadj`.
- **Re-apply my undervolt on launch** — runs `intel-undervolt apply` so the
  offsets *you* configured survive suspend / thermald. Goblin Mode Pro never
  picks the values.

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

## Sharing
The **↗** button exports a profile to JSON; **📂** imports one; **↓** browses the
[community starter profiles](https://github.com/Bvaughan7/goblin-mode-pro/tree/main/profiles).
