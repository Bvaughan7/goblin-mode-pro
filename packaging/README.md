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

### Publishing `goblin-mode-pro-git` to the AUR

The AUR is a separate git host; the package's PKGBUILD lives in its own repo
there, and `packaging/aur/` in this repo is the source of truth we copy from.

**One-time setup.** Create an account at <https://aur.archlinux.org/register>,
then add an SSH public key under *My Account → SSH Public Key*:

```sh
ssh-keygen -t ed25519 -C "aur" -f ~/.ssh/aur
cat ~/.ssh/aur.pub                      # paste this into the AUR account page
cat >> ~/.ssh/config <<'EOF'
Host aur.archlinux.org
  User aur
  IdentityFile ~/.ssh/aur
  IdentitiesOnly yes
EOF
ssh aur@aur.archlinux.org               # should greet you by username
```

**Publishing, and every update after it.** `.SRCINFO` is what the AUR actually
reads — a push whose `.SRCINFO` disagrees with its `PKGBUILD` is rejected, so
always regenerate it rather than editing it by hand:

```sh
git clone ssh://aur@aur.archlinux.org/goblin-mode-pro-git.git /tmp/aur-gmp
cp packaging/aur/PKGBUILD packaging/aur/goblin-mode-pro.install /tmp/aur-gmp/
cd /tmp/aur-gmp
makepkg --printsrcinfo > .SRCINFO       # never hand-edit this
makepkg -sf --noconfirm                 # prove it builds before pushing
git add PKGBUILD .SRCINFO goblin-mode-pro.install
git commit -m "goblin-mode-pro-git 1.3.0"
git push
```

Then copy `.SRCINFO` back into `packaging/aur/` here so the two don't drift —
`tests/test_packaging_versions.py` checks the base version agrees.

Notes worth keeping in mind:

- The repo name and `pkgname` must match (`goblin-mode-pro-git`), and the first
  push has to contain both `PKGBUILD` and `.SRCINFO` or the AUR rejects it.
- `pkgver()` resolves the real version at build time from `__about__.py`, so the
  `pkgver=` line in the PKGBUILD is only a placeholder. Don't bump it by hand
  expecting it to matter — but do keep it current, since it is what the AUR web
  page shows until someone builds the package.
- Only `-git` belongs on the AUR from this repo. A versioned
  `goblin-mode-pro` package would need release tarballs with stable checksums;
  `packaging/arch/` builds that locally from a tag and is not published.

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
