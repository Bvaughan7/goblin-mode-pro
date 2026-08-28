# Roadmap build — progress / pass-off

Worked through the §5 roadmap from the artifact page. **Read `HANDOFF.md` first**
— its "Roadmap build (2026-08-28)" section is the authoritative record of what
was added and how it's wired; its "Deferred roadmap — design notes" section is
the spec for the remaining items.

Last updated: 2026-08-28, end of the roadmap build session.

## Bottom line

- **Shipped:** cartoon logo · auto-detect any game · pre-flight system check ·
  Proton log analyzer · one-click bug report · focus mode. All wired into the
  daemon + GUI + helper + tests. 67 tests pass, GUI smoke green, compiles clean.
- **Not done:** backend abstraction / multi-distro packaging, and 6 "still to
  build" features (crowdsourced profiles, regression tracking, gamescope, TDP
  presets, core pinning, anti-cheat) — all have design notes in HANDOFF.md.
- **Not yet run live** — needs `./install.sh` + `systemctl --user restart
  goblin-mode-pro` on the machine, then a real game launch to exercise
  auto-detect end to end.
- Repo is `git init`'d and everything is `git add`'d but **not committed** — the
  next session (or the user) should make the first commit.

## Order of work (value × feasibility)

| # | Item | State |
|---|------|-------|
| — | Cartoon logo (OpenClaw-style goblin mascot) | ✅ `data/icons/com.goblinmode.Pro.svg` (plated) + `goblin-mark.svg` (transparent); artifact sigil updated |
| 3 | Pre-flight system check | ✅ `preflight.py` (12 checks) + `page_preflight.py` + helper `SetSysctl` (allowlisted) + daemon `run_preflight`/`apply_preflight_fixes` + tests |
| 2 | Auto-detect any game | ✅ `gamedetect.py` (launcher/fdinfo/libs/blocklist signal stack) + observer sweep + daemon `_adopt_detected_game`/`ignore_game`/`keep_game` + `GameDetected` signal + tray notify + Games-page toggle & AUTO badge + tests |
| 4 | Proton log analyzer | ✅ `logrules.py` (16 rules) — `logwatch` now consumes its LIVE rules; report + "Analyze the Proton log" GUI action use `analyze_text` + tests |
| 5 | One-click bug report | ✅ `report.py` (system info + preflight + incident + log findings + tweaks → markdown / LLM prompt / GH url) + daemon `build_report` + Diagnostics "Build a bug report" dialog + tests |
| 6 | Focus mode | ✅ `focus.py` (baloo/tracker suspend, ScreenSaver idle-inhibit, KDE DND) — refcounted global in `payload` + `GameProfile.focus_mode` + Games-page toggle |
| 1 | Backend abstraction (`backends/`) | ⬜ deferred — see HANDOFF "Deferred roadmap — design notes" |
| 7-12 | core pinning / TDP presets / regression / crowdsourced / gamescope / anti-cheat | ⬜ deferred — design notes in HANDOFF |

**Tests: 67 pass** (`python3 scripts/run_tests.py`). GUI smoke green. Not yet
reinstalled/run live — needs `./install.sh` + `systemctl --user restart`.

## Guardrails

- Keep `python3 -m py_compile $(find src helper scripts tests -name '*.py')` clean after every module.
- Keep `python3 scripts/run_tests.py` green. Add tests with each feature.
- Keep `python3 scripts/gui_smoke.py` green after GUI changes.
- Don't break the existing daemon/helper/GUI wiring.
- Clean up test artifacts from `~/.config`, `~/.local` after smoke runs.

## Decisions / notes as I go

### Logo — DONE (v1)
- `data/icons/com.goblinmode.Pro.svg` — cartoon goblin mascot on a rounded-square
  app plate. Green goblin head, steampunk goggles pushed up on the forehead, wide
  toothy grin with fangs + tongue, heavy scheming brows, amber eyes (one wide, one
  squint), warts. Bold ink outlines, radial-gradient skin. Reads at 48px.
- `data/icons/goblin-mark.svg` — same, transparent background (for the artifact
  hero / README / wordmark use).
- Artifact page masthead `.sigil` updated to an inline copy of the mark, 60px.
- Render check: `rsvg-convert -w 420 icon.svg -o out.png`. rsvg-convert is available.
- TODO later: a `goblin-hero.svg` with a wrench, and a proper wordmark lockup.

### Realistic scope call
Delivering a strong coherent subset really well beats half-doing all 12:
1 gamedetect · 3 preflight · 4 logrules · 5 report · 1(infra) backends · 6 focus.
The rest (TDP presets, core pinning, regression, crowdsourced, gamescope,
anti-cheat) get design notes in HANDOFF.md for the next pass.

---
