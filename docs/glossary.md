# Glossary

**CPU governor** — Linux slows your CPU down when it thinks you're idle, to save
power. During a game that causes stutter. Goblin Mode Pro locks it at
`performance` while you play and restores it after.

**EPP (Energy-Performance Preference)** — a finer hint than the governor, on
Intel P-state and AMD P-state. Set to `performance` alongside the governor.

**renice** — process priority. A lower "nice" number means the scheduler gives
that process the CPU first.

**RAPL / PL1 / PL2** — Intel's power-limit interface. PL1 is the sustained
wattage, PL2 the short burst. Raising them lets the CPU hold its top speed
longer *if the cooling can keep up*.

**TDP** — thermal design power; the wattage the chip is meant to run at. The
AMD-laptop equivalent of RAPL, set via `ryzenadj`.

**Screen tearing** — normally the compositor adds a frame of delay for a clean
picture. Allowing tearing removes that delay for lower input lag.

**VRR / Adaptive Sync** — the monitor matches the game's frame rate instead of a
fixed one, removing a whole class of stutter. Needs a VRR-capable display.

**Core pinning** — restricting the game's threads to specific CPU cores: the fast
cores of a hybrid CPU (Intel P-cores), or one CCD on a Ryzen (to avoid the
cross-CCD latency penalty).

**gamescope** — a micro-compositor that gives a rock-solid frame cap, FSR/NIS
upscaling and clean alt-tab.

**Proton / DXVK / VKD3D** — the translation layers that run Windows games on
Linux. DXVK does Direct3D 9-11 → Vulkan; VKD3D-Proton does D3D12 → Vulkan.

**1% low / 0.1% low FPS** — the average of the slowest 1% (or 0.1%) of frames.
A much better "is it smooth?" number than the average.

**Curve Optimizer** — AMD's per-core undervolt/frequency-curve tuning, applied
via `ryzenadj`. Goblin Mode Pro only ever *re-applies* the offsets you already
put in `/etc/goblin-mode-pro/amd-undervolt.conf` — it never picks values.

**GSP firmware** — NVIDIA's GPU System Processor, an offload microcontroller on
the card itself that recent drivers use for parts of the kernel-mode work.

**nvidia-drm.modeset** — a boot-time kernel module parameter NVIDIA needs on
for Wayland and explicit sync. There's no runtime toggle — changing it means
writing a modprobe.d config and rebooting.
