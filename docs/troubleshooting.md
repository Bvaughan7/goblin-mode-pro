# Troubleshooting

## The app icon looks stale after install (KDE)
```sh
kbuildsycoca6 --noincremental
rm -f ~/.cache/icon-cache.kcache
# then restart Plasma, or log out and back in
```

## Games aren't detected
```sh
systemctl --user is-active goblin-mode-pro
journalctl --user -u goblin-mode-pro -f
```
Make sure the launch wrapper is set (`goblin-run %command%`). Auto-detect needs
`master_enabled` and the game to do sustained GPU work or carry a launcher tag.

## CPU speed / power tuning does nothing
The **helper** isn't running or **polkit** isn't installed:
```sh
systemctl status goblin-mode-pro-helper
```
`./install.sh --user` deliberately skips it ("limited mode").

## esync errors / "too many open files"
The System Check flags this. The fix raises `DefaultLimitNOFILE`, which needs a
**re-login** to take effect.

## Anti-cheat game won't launch
Set Proton to **Experimental**, make sure the **user namespaces** check passes,
and check the game on protondb.com. The System Check's *Anti-cheat* row explains
the current state.

## AMD TDP control isn't there
Install `ryzenadj` (AUR / COPR) and re-run `./install.sh` — it applies a systemd
drop-in that gives the helper the access `ryzenadj` needs.

## Still stuck
`goblin-mode-pro-cli setup` (or Diagnostics → Export my full setup) →
[open an issue](https://github.com/Bvaughan7/goblin-mode-pro/issues/new/choose).
