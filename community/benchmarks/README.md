# Community benchmarks

A place to share benchmark report cards per GPU, so other players can see
what a given game runs like on hardware similar to theirs. This is a plain
PR-based contribution flow, not a live upload service — the project has no
server and isn't standing one up just to collect JSON files.

## Contribute a run

1. In Diagnostics → Session history, find the run you want to share (arm a
   benchmark first from the Diagnostics page if you don't have one yet), and
   click **Copy as JSON** on it.
2. Add a file under `community/benchmarks/<your-gpu>/<game-slug>.json` —
   e.g. `community/benchmarks/rtx-4070/cyberpunk2077.json`. Use lowercase,
   hyphenated GPU names (`rtx-4070`, `rx-7800-xt`, `arc-a770`, `steamdeck-oled`, ...).
   If a file already exists for that GPU+game, add your run as a new entry in
   a JSON array instead of overwriting it.
3. Open a PR. That's it — no account, no server, no telemetry.

## What's in the JSON

Whatever `SessionSummary` captured for that run: average / median / 1% /
0.1% low FPS, 95th percentile, frame-time stutter %, CPU/GPU temps, the
kernel and active tweaks. Nothing else — no usernames, no paths (the daemon
never puts either in a session record to begin with).

## Using someone else's run

There's no in-app importer for this folder (yet — see ROADMAP.md). For now,
browse the folder on GitHub and compare numbers by eye, or paste a JSON file
into a diff tool alongside your own **Copy as JSON** export.
