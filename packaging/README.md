# Packaging

Goblin Mode Pro is a system-integration tool: it installs a **polkit action**, a
**systemd system service** (the root helper) and a **systemd user service** (the
daemon). That shapes what packaging makes sense.

## Arch / CachyOS / Manjaro

Two PKGBUILDs:

| Dir | Package | Builds from |
|---|---|---|
| [`aur/`](aur/) | `goblin-mode-pro-git` | latest `main` (rolling) |
| [`arch/`](arch/) | `goblin-mode-pro` | the newest tagged release |

```sh
cd packaging/arch && makepkg -si      # tagged release
# or
cd packaging/aur  && makepkg -si      # rolling
```

A pre-built `goblin-mode-pro-<version>-any.pkg.tar.zst` is also attached to each
[GitHub release](https://github.com/Bvaughan7/goblin-mode-pro/releases) —
`sudo pacman -U ./goblin-mode-pro-*.pkg.tar.zst`.

After install:

```sh
sudo systemctl enable --now goblin-mode-pro-helper.service
systemctl --user  enable --now goblin-mode-pro.service
```

The `.install` hook activates the AMD-TDP sandbox drop-in automatically when
`ryzenadj` is present.

## Debian / Ubuntu / Fedora / openSUSE / Nobara / Pop!\_OS …

Use [`../install.sh`](../install.sh) — it detects the package manager, installs
the runtime dependencies and lays down the same file set as the PKGBUILD. A
proper `.deb` / `.rpm` built in OBS is a follow-up; the file layout is already
FHS-correct so it is mostly a control-file exercise.

## Flatpak — not provided, on purpose

The helper writes to `/sys/devices/system/cpu`, owns a name on the **system**
D-Bus, and runs as a root systemd service; none of that fits the Flatpak
sandbox. Shipping only the GUI as a Flatpak would still need the daemon and
helper installed on the host, so it saves nothing. If a sandboxed GUI is ever
worth it, it would talk to a host-installed daemon over the session bus — but
that is not on the roadmap.
