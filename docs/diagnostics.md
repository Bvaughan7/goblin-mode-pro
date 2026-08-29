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

## Exports
- **Export last incident for AI** — the incident + metric window + log tail as a
  structured JSON prompt.
- **Build a bug report** — system info + pre-flight + last incident + log
  analysis, redacted, as Markdown.
- **Export my full setup** — the whole machine + every profile.
