# Roadmap

**1.1.0** cleared the entire 1.0 menu — the first-run wizard, health score,
ProtonDB / anti-cheat lookup, NVIDIA & RADV tuning presets, benchmark mode, the
CLI, handheld profiles, auto-clip, undervolt re-apply, GameMode transparency,
desktop notifications, the docs site, `.deb`/`.rpm` packaging and the integration
tests. See [`CHANGELOG.md`](CHANGELOG.md).

This is the **next** "what could go here" list, grouped by who it's for. Nothing
is committed or scheduled — it's a menu. 👍 an item on the
[issue tracker](https://github.com/Bvaughan7/goblin-mode-pro/issues) or open a
new one if you want to see it (or build it).

**Legend:** ⭐ high impact · 🟢 good first issue · 🧪 needs design/spike ·
⚠️ touches privileged code

---

## Shipped in 1.1.0

<details>
<summary>The whole 1.0 menu (click to expand)</summary>

- [x] First-run wizard · [x] System health score · [x] ProtonDB lookup ·
      [x] "What does this do?" popovers · [x] Undo for pre-flight fixes ·
      [x] Distro-specific setup tips · [x] Recommended-kernel nudge ·
      [x] Controller check · [x] Copyable command snippets
- [x] Benchmark mode · [x] NVIDIA tuning presets · [x] AMD / RADV tuning presets ·
      [x] CLI / headless mode · [x] Proton/Wine version awareness ·
      [x] Shader-cache management · [x] GameMode transparency ·
      [x] Desktop notifications · [x] Full setup export · [x] Auto-clip a highlight ·
      [x] Undervolting / power-curve profiles (Intel re-apply) ·
      [x] Anti-cheat status lookup · [x] i18n scaffolding
- [x] Handheld auto-profile
- [x] `CONTRIBUTING.md` + templates + labels · [x] Docs site ·
      [x] `.deb` / `.rpm` · [x] Integration tests · [x] Flatpak decision

</details>

---

## For people new to Linux gaming

- [ ] ⭐ **Wizard, part two.** After the launcher step, offer to install the
      pieces that are missing (MangoHud, GameMode, a gaming kernel) by handing
      the exact command to a terminal — or, where the distro allows it, running
      it through the existing polkit helper.
- [ ] **Guided fix for a failed launch.** When the Proton-log analyzer names a
      cause ("missing vcrun2019"), offer the `protontricks` / winetricks command
      inline instead of just describing it.
- [ ] 🟢 **Translate the UI.** The gettext plumbing is in (`po/`); it needs
      actual catalogues. A `de`/`fr`/`es`/`pt_BR`/`zh_CN` starter would go far.
- [ ] 🟢 **"Explain my score" panel.** Expand the health number into a short
      readout of what each failing check actually breaks in-game.
- [ ] **Onboarding for the tray-only path.** Some users never open the window —
      surface Keep/Ignore and the score from the tray menu itself.

## For the Linux gaming community & power users

- [ ] ⭐ **Benchmark comparison view.** Two runs side by side (before/after a
      Proton bump, a kernel change, a tweak) with the deltas called out — the
      report card exists, the diff UI doesn't.
- [ ] ⭐ **Shareable benchmark cards.** Export a run as a PNG/JSON others can
      import, and a repo of community submissions per GPU.
- [ ] ⭐ **AMD Curve Optimizer / `ryzenadj` undervolt.** The Intel side re-applies
      an existing config; AMD desktops want an actual curve UI. ⚠️ 🧪 genuinely
      risky — behind an "I understand" gate, same as the Intel path.
- [ ] **GSP-firmware / `nvidia-drm.modeset` toggles** with a clear readout of
      what's active and a reboot prompt (NVIDIA preset section groundwork is in).
- [ ] **Shader pre-warm**, not just clear — kick off Steam's shader pre-cache for
      a game before first launch.
- [ ] **Per-output VRR control.** Today VRR is all-or-nothing via
      `kscreen-doctor`; expose it per monitor.
- [ ] 🧪 **wlroots / GNOME compositor parity.** Tearing and VRR hooks for
      Hyprland (`hyprctl`) and GNOME (`ddcutil`-free path) so KDE isn't the only
      first-class target.
- [ ] **`gamescope` session mode** — launch a game into a dedicated gamescope
      session from the GUI, not just wrap it.
- [ ] **Prometheus / `textfile` exporter** for the metric stream, for people who
      already run Grafana.

## Handhelds

- [ ] ⭐ **Battery-vs-AC auto-switch.** Different TDP / FPS-cap / refresh-rate
      profile on battery, switched automatically on plug/unplug.
- [ ] **Per-game refresh-rate cap** on the internal panel (Deck 40/50/60, Ally
      120…), tied to the FPS cap.
- [ ] **Preemptive fan spin-up** on launch where the EC allows it.
- [ ] **TDP presets per handheld** — sensible defaults keyed to the detected
      model instead of one generic set.

## Project & community

- [ ] **Ship the `.deb` / `.rpm` from CI.** The packaging dirs exist; wire an
      OBS project or a `dpkg-buildpackage` GitHub Action + release attachment.
- [ ] **Publish to Flathub** *only if* the sandboxed-GUI-to-host-daemon path
      proves worth it (see `packaging/README.md`).
- [ ] 🟢 **More `profiles/`** — community starter profiles for the top 50 Proton
      games.
- [ ] **Restore coverage further** — a GUI smoke test under a headless
      compositor, and a helper contract test against a fake polkit.
- [ ] **Signed releases** — sign the tags and the attached packages.
- [ ] 🧪 **Telemetry-free "works for me" reports** — opt-in, anonymised
      profile+result submissions to seed the community profile set.

---

Have an idea that isn't here? [Open an issue.](https://github.com/Bvaughan7/goblin-mode-pro/issues/new)
