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

## Debian / Ubuntu / Pop!\_OS / Mint …

[`debian/`](debian/) is a complete `debhelper` (compat 13) source-package
directory. It mirrors the FHS layout of the PKGBUILD via
`override_dh_auto_install` in [`debian/rules`](debian/rules), and its maintainer
scripts enable the helper service and `systemctl --global enable` the user
daemon.

```sh
cp -r packaging/debian .          # dpkg-buildpackage expects ./debian
dpkg-buildpackage -us -uc -b
```

For [OBS](https://build.opensuse.org/), point the package at this repo and use
`packaging/debian/` as the `debian` sub-directory.

## Fedora / RHEL / openSUSE / Nobara …

[`rpm/goblin-mode-pro.spec`](rpm/goblin-mode-pro.spec) is a `noarch` spec with
the Fedora `systemd` scriptlet macros wired up.

```sh
rpmbuild -ba packaging/rpm/goblin-mode-pro.spec \
  --define "_sourcedir $PWD"          # after `git archive`-ing a v<ver> tarball
```

## Other distros

[`../install.sh`](../install.sh) detects the package manager, installs the
runtime dependencies and lays down the same file set by hand — the fallback when
no native package exists yet.

## Flatpak — not provided, on purpose

The helper writes to `/sys/devices/system/cpu`, owns a name on the **system**
D-Bus, and runs as a root systemd service; none of that fits the Flatpak
sandbox. Shipping only the GUI as a Flatpak would still need the daemon and
helper installed on the host (the GUI is a pure session-bus client of the
daemon and does nothing useful alone), so it saves nothing — a user who can
install the daemon can install the ~200 KB GUI the same way. A sandboxed GUI
talking to a host daemon over the session bus is *possible* (`--talk-name`
`com.goblinmode.Pro.Daemon`), but it trades the whole install story for a
sandbox the GUI doesn't need. Revisit only if a distro ships the daemon but not
the GUI. See [`ROADMAP.md`](../ROADMAP.md) → "Reconsider Flatpak".
