# Roadmap

**1.2.0** cleared the entire post-1.1 menu — wizard part two, guided
launch-failure fixes, the "explain my score" panel, tray-only onboarding,
full i18n, benchmark comparison + shareable cards, AMD Curve Optimizer
undervolt, GSP/modeset info, shader pre-warm, per-output VRR, Hyprland
compositor support, gamescope session mode, the Prometheus exporter,
telemetry-free "works for me" reports, the battery/AC auto-switch,
per-handheld TDP presets, per-game refresh-rate cap, preemptive fan
spin-up, CI-built and signed `.deb`/`.rpm`, 20 new starter profiles, and
the helper contract + GUI smoke tests. See [`CHANGELOG.md`](CHANGELOG.md)
for the details and the couple of places scope was narrowed from the
original wording (mostly: no live upload server, nothing that would need
a new "run arbitrary command as root" helper method).

One piece of work is in flight and it is not a feature: the privileged
helper is being ported to Rust, tracked in [#1](https://github.com/Bvaughan7/goblin-mode-pro/issues/1). It changes nothing a
user sees — the daemon, GUI and CLI stay Python — and the reasoning, along
with the argument against doing it at all, is in
[`docs/rust-conversion.md`](docs/rust-conversion.md).

Beyond that, nothing is currently proposed — this is a menu, not
a backlog. 👍 an item on the
[issue tracker](https://github.com/Bvaughan7/goblin-mode-pro/issues) or
open a new one if you want to see something here.

---

## Shipped in 1.2.0

<details>
<summary>The whole post-1.1 menu (click to expand)</summary>

- [x] Wizard part two (install missing pieces) · [x] Guided launch-failure
      fixes · [x] "Explain my score" panel · [x] Tray-only onboarding ·
      [x] Full i18n (289 strings, 5 languages)
- [x] Benchmark comparison view · [x] Shareable benchmark cards ·
      [x] AMD Curve Optimizer / `ryzenadj` undervolt · [x] GSP-firmware /
      `nvidia-drm.modeset` info + toggle · [x] Shader pre-warm ·
      [x] Per-output VRR control · [x] Hyprland compositor support
      (GNOME/Mutter still has no equivalent runtime IPC — documented, not
      faked) · [x] `gamescope` session mode · [x] Prometheus textfile
      exporter
- [x] Battery-vs-AC auto-switch · [x] Per-game refresh-rate cap ·
      [x] Preemptive fan spin-up · [x] TDP presets per handheld
- [x] `.deb` / `.rpm` built + attached by CI · [x] Flathub decision
      (not pursuing) · [x] More `profiles/` (20 new starter profiles) ·
      [x] Restore coverage further (helper contract test + GUI smoke
      test under Xvfb) · [x] Signed releases · [x] Telemetry-free
      "works for me" reports

</details>

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

Have an idea that isn't here? [Open an issue.](https://github.com/Bvaughan7/goblin-mode-pro/issues/new)
