# Getting started

## Install

=== "Arch / CachyOS / Manjaro"
    ```sh
    cd packaging/arch && makepkg -si     # tagged release
    # or the rolling build:  cd packaging/aur && makepkg -si
    ```

=== "Everything else"
    ```sh
    git clone https://github.com/Bvaughan7/goblin-mode-pro
    cd goblin-mode-pro && ./install.sh
    ```

Then enable the services:

```sh
sudo systemctl enable --now goblin-mode-pro-helper.service   # root helper
systemctl --user  enable --now goblin-mode-pro.service       # per-user daemon
```

`./install.sh --user` skips the helper — everything works except CPU speed /
power tuning ("limited mode").

## First run

The GUI opens a short wizard the first time: it checks your system, offers the
safe fixes, lists anything still missing (MangoHud, GameMode, a gaming kernel)
with a copy-pasteable install command for your distro, and shows exactly where
your launcher's wrapper field is. If you never open the window, the tray icon
still shows your readiness score and a "Finish setup" nudge.

The UI is available in English, German, French, Spanish, Brazilian Portuguese
and Chinese (Simplified) — it follows your system locale automatically.

## The one step you can't skip

Goblin Mode Pro needs a small wrapper on the game's launch command:

| Launcher | Where | Value |
|---|---|---|
| Steam | Game → Properties → Launch Options | `goblin-run %command%` |
| Lutris | Game → Configure → System options → Command prefix | `goblin-run` |
| Heroic | Settings → Advanced → Wrapper command | `goblin-run` |

Without it, env-var injection and the Proton-log capture are skipped.

## Dependency package names

You need Python 3, PyGObject, GTK 4, libadwaita and `psutil`. Minimum versions:
Python **3.11**, GTK **4.0**, libadwaita **1.5** — the GUI checks at startup and
tells you rather than crashing. The daemon and `goblin-mode-pro-cli` have no GTK
dependency at all and work on anything older.

| Distro | Command |
|---|---|
| Arch / CachyOS | `pacman -S python-gobject python-psutil gtk4 libadwaita python-pystray wl-clipboard mangohud gamemode gamescope` |
| Debian / Ubuntu | `apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 python3-psutil wl-clipboard mangohud gamemode gamescope` |
| Fedora / Nobara | `dnf install python3-gobject python3-psutil gtk4 libadwaita wl-clipboard mangohud gamemode gamescope` |
| openSUSE | `zypper install python3-gobject python3-psutil gtk4-tools libadwaita wl-clipboard mangohud gamemode gamescope` |

`mangohud`, `gamemode` and `gamescope` are optional but recommended — the
overlay, the frame-rate watchdog and the gamescope integration need them.
`ryzenadj` (AUR / COPR) is needed for AMD-laptop TDP control, and `scx-scheds`
for the per-game sched_ext scheduler.

## Requirements

- **systemd** and **polkit** (the helper is inert without polkit)
- Kernel ≥ 5.16 for `WINEFSYNC`
- Unprivileged user namespaces enabled (some hardened Debian/Ubuntu kernels
  disable them — the System Check catches this)
- Optional: `mangohud`, `gamemode`, `gamescope`, `ryzenadj` (AMD TDP +
  Curve Optimizer undervolt), `intel-undervolt`, `gpu-screen-recorder`
  (auto-clip), `python3-cairo` (saving a benchmark card as an image)
