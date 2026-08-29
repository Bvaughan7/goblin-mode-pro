# Diagnostics & benchmarking

## The graphs
While a game runs, the daemon samples CPU/GPU temp, load, package power vs
PL1/PL2 and throttle flags (~1 Hz). The **Temperature vs load** chart plots them
with vertical marks at throttle events; the **Frame rate** chart shows the
MangoHud watchdog log with the dip threshold.

## Frame-rate watchdog
Enable it per game (Games → MangoHud → Frame-rate watchdog). If your FPS falls
to/under the threshold (or below half the recent median) and stays there, it
snapshots deep GPU state — VRAM, PCIe link, clocks, power state — classifies it
*withheld* (focus loss / loading) vs *starved* (a real bottleneck), and raises an
incident with a ranked cause. After the game exits it checks whether VRAM was
actually released.

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
