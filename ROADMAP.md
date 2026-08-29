# Roadmap

1.0.0 shipped with the core loop (detect → tune → revert), the diagnostics engine
and the packaging. This is the "what next" list, grouped by who it's for.

Nothing here is committed or scheduled — it's a menu. 👍 an item on the
[issue tracker](https://github.com/Bvaughan7/goblin-mode-pro/issues) or open a
new one if you want to see it (or build it).

**Legend:** ⭐ high impact · 🟢 good first issue · 🧪 needs design/spike ·
⚠️ touches privileged code

---

## For people new to Linux gaming

The goal here is to remove the "…now what?" moment after install.

- [ ] ⭐ **First-run wizard.** A guided flow on first launch: run the System
      Check, ask which launcher you use, then show *exactly* where to paste
      `goblin-run %command%` (with a screenshot of the Steam / Lutris / Heroic
      field), and offer to apply sensible defaults.
- [ ] ⭐ **System health score.** One traffic-light number on the Dashboard —
      "your system is 9/10 game-ready" — derived from the pre-flight results,
      with the failing items one click away.
- [ ] ⭐ **ProtonDB lookup.** Type a game name → show its ProtonDB tier
      (Platinum / Gold / …) and the top community-reported tweaks, wired into the
      existing community-profiles feature. 🧪 (needs a caching layer; ProtonDB
      has no official API.)
- [ ] 🟢 **"What does this do?" popovers.** An info button on every toggle with a
      two-sentence plain-language explanation ("Linux slows your CPU down to save
      power; this keeps it at full speed while you play").
- [ ] 🟢 **Undo for pre-flight fixes**, and a clearer *temporary vs. permanent*
      distinction, so applying one feels less scary.
- [ ] **Distro-specific setup tips.** "You're on Ubuntu — enable user namespaces
      with this one command" / "You're on Fedora — install the RPM Fusion
      NVIDIA driver". The capability probe already knows the distro.
- [ ] **Recommended-kernel nudge.** If you're on a stock non-gaming kernel,
      gently point at CachyOS / Xanmod / the distro's `-zen` kernel with the
      exact install command — dismissible, never nagging.
- [ ] **Controller check.** "Your DualSense is connected, Steam Input is on" —
      a quick line so a newcomer knows their pad will work.
- [ ] 🟢 **Copyable command snippets everywhere.** Every `sudo …` line in the UI
      gets a copy button.

## For the Linux gaming community & power users

Depth, sharing and scriptability.

- [ ] ⭐ **Benchmark mode.** Run a game for N minutes → a clean report card:
      avg / 1% / 0.1% low FPS, frame-time consistency, thermal headroom used,
      Proton version. Feeds the regression tracker. The community lives on these
      numbers.
- [ ] ⭐ **NVIDIA tuning presets.** `__GL_THREADED_OPTIMIZATIONS`, shader-disk-
      cache size, GSP-firmware state, `nvidia-drm.modeset`, and a clear readout
      of what's active. NVIDIA users are a large, under-served, frustrated
      segment.
- [ ] ⭐ **AMD / RADV tuning presets.** `RADV_PERFTEST`, `mesa_glthread`,
      `AMD_VULKAN_ICD` (RADV vs AMDVLK), `RADV_TEX_ANISO`. Per game.
- [ ] ⭐ **CLI / headless mode.** `goblin-mode-pro status`, `… boost <game>`,
      `… report` — for the terminal crowd, SSH sessions and scripts.
- [ ] **Proton/Wine version awareness.** Detect installed Proton-GE /
      Proton-CachyOS / Wine-TKG builds, show which one a game is using, flag when
      Steam silently changed it.
- [ ] **Shader-cache management.** Show DXVK / VKD3D / Steam shader-cache sizes
      per game; offer to clear or pre-warm. First-run stutter is a top complaint.
- [ ] **GameMode transparency.** Surface what `gamemoded` is actually doing
      (governor, GPU perf level, ioprio, screensaver) — it's a black box to most
      people.
- [ ] **Desktop notifications.** Boost engaged / released, a regression caught,
      a crash matched to a known cause ("Cyberpunk crashed — missing
      vcrun2019").
- [ ] **Full setup export.** One file with every profile, kernel param, installed
      Proton version and driver — for "help me" threads or reproducing on another
      machine.
- [ ] 🧪 **Auto-clip a highlight.** "New FPS high in <game>" → a short clip via
      the compositor's recorder. Shareable = visible.
- [ ] ⚠️ 🧪 **Undervolting / power-curve profiles.** `intel-undervolt`, AMD Curve
      Optimizer. Big enthusiast draw, genuinely risky — opt-in, heavily
      caveated, behind an "I understand" gate.
- [ ] **Anti-cheat status lookup.** Live per-game verdict from
      areweanticheatyet.com, beyond the current static note.
- [ ] **i18n scaffolding.** gettext plumbing so the (large) non-English Linux
      gaming community can translate it.

## Handhelds

- [ ] ⭐ **Handheld auto-profile.** Detect a Steam Deck / ROG Ally / Legion Go and
      surface a handheld layout: TDP slider front-and-centre, battery-vs-AC
      auto-switch, per-game refresh-rate cap, preemptive fan spin-up.

## Project & community

- [ ] 🟢 **`CONTRIBUTING.md`** + issue / PR templates + `good first issue`
      labels.
- [ ] 🟢 **A docs site** (mkdocs → GitHub Pages): the glossary, per-game guides,
      troubleshooting — community-editable.
- [ ] **`.deb` / `.rpm` via OBS.** The file layout is already FHS-correct;
      mostly a control-file exercise. `install.sh` covers those distros
      meanwhile.
- [ ] **Restore the test suite's reach.** The pure-logic modules are covered;
      add integration coverage for `payload` apply/revert and the observer state
      machine (with a fake helper).
- [ ] 🧪 **Reconsider Flatpak** for the unprivileged GUI talking to a
      host-installed daemon — worth it only if the UX doesn't confuse people.

---

Have an idea that isn't here? [Open an issue.](https://github.com/Bvaughan7/goblin-mode-pro/issues/new)
