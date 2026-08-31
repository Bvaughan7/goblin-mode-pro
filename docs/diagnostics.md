# Diagnostics & benchmarking

## The graphs
While a game runs, the daemon samples CPU/GPU temp, load, package power vs
PL1/PL2 and throttle flags (~1 Hz). The **Temperature vs load** chart plots them
with vertical marks at throttle events; the **Frame rate** chart shows the
MangoHud watchdog log with the dip threshold.

## Frame-rate watchdog
Enable it per game (Games → MangoHud → Frame-rate watchdog). It watches the
MangoHud log for a **sustained** drop — the trailing mean has to sit at/under
the threshold (or under `dip_ratio` × the pre-dip baseline) for about 4
seconds before it counts. A 1–3 second drop is a menu, a zone load or shader
compilation, so it's ignored; a window that isn't rendering at all (alt-tabbed)
isn't a dip either. The baseline is frozen for the length of the dip, and
recovery isn't declared until the frame rate climbs back to ~85 % of it — so
you won't see "recovered to 24 FPS" any more.

On a real dip it takes a fresh deep GPU snapshot (VRAM, PCIe link, clocks,
power state) and files the incident as one of:

- **withheld** — CPU and GPU both near-idle: focus loss, a loading screen or a
  menu. Not a hardware problem.
- **GPU-bound scene** — the card is pegged (≥ 92 %): this spot is heavier than
  your settings can sustain. Lower a setting or cap the frame rate here.
- **CPU-bound scene** — a CPU core is pinned (≥ 95 %) while the GPU has
  headroom: a single-threaded hotspot, e.g. a busy city or a raid. More cores
  won't help.
- **starved** — a real fault, with a ranked cause (VRAM exhaustion, a
  down-trained PCIe link, a stuck power state, a collapsed core clock…).

Only *starved* arms the post-exit check for whether VRAM was actually released.

## Notifications
Desktop notifications are reserved for things you'd want to act on: a GPU /
driver fault, VRAM that leaked after a game exited, and sustained CPU thermal
throttling. Thermal throttling has to persist across a 20-second window before
it notifies (a laptop nicks the throttle counter under any turbo load), then
reminds at most once every 15 minutes while it lasts. Frame-rate dips and
benign incidents are logged to the Diagnostics page but never pop a
notification.

## Regression tracking
Every session is summarised (avg / median / 1% low, duration, active tweaks) into
`~/.local/share/goblin-mode-pro/sessions.jsonl` and compared to the recent
history for that game. A >10% swing in the 1% low or average is flagged with a
▼/▲ pill.

## Benchmark mode
Pick a game, **Arm benchmark**, play a few minutes. On exit you get a report card
tagged **BENCHMARK**: 0.1% low, 95th percentile, frame-time stutter %, and
thermal peaks — on top of the usual numbers.

## Comparing sessions
**Compare two sessions** diffs a game's two most recent sessions — every FPS,
frame-time and thermal metric side by side, with the % change and which side
improved (temps/stutter treat *lower* as the improvement, FPS treats *higher*).
Same thing headlessly: `goblin-mode-pro-cli compare GAME`.

## Shareable benchmark cards
Each session in the history list has **Copy as JSON** (clipboard, for pasting
into a PR or forum thread) and **Save as image** (a small report-card PNG to
`~/Pictures/goblin-mode-pro/`). See
[`community/benchmarks/`](https://github.com/Bvaughan7/goblin-mode-pro/tree/main/community/benchmarks)
for the PR-based per-GPU submission flow — there's no upload server.

## Exports
- **Export last incident for AI** — the incident + metric window + log tail as a
  structured JSON prompt.
- **Build a bug report** — system info + pre-flight + last incident + log
  analysis, redacted, as Markdown.
- **Export my full setup** — the whole machine + every profile.
- **Analyze the Proton log** — matches the captured log against known Linux-
  gaming failures; findings with a known fix (e.g. missing vcrun/mono) show a
  copyable `protontricks` command inline.
