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
safe fixes, and shows exactly where your launcher's wrapper field is.

## The one step you can't skip

Goblin Mode Pro needs a small wrapper on the game's launch command:

| Launcher | Where | Value |
|---|---|---|
| Steam | Game → Properties → Launch Options | `goblin-run %command%` |
| Lutris | Game → Configure → System options → Command prefix | `goblin-run` |
| Heroic | Settings → Advanced → Wrapper command | `goblin-run` |

Without it, env-var injection and the Proton-log capture are skipped.

## Requirements

- **systemd** and **polkit** (the helper is inert without polkit)
- Kernel ≥ 5.16 for `WINEFSYNC`
- Unprivileged user namespaces enabled (some hardened Debian/Ubuntu kernels
  disable them — the System Check catches this)
- Optional: `mangohud`, `gamemode`, `gamescope`, `ryzenadj` (AMD TDP),
  `intel-undervolt`, `gpu-screen-recorder` (auto-clip)
