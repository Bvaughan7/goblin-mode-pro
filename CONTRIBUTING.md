# Contributing

Thanks for looking! Bug reports, ideas and PRs are all welcome.

## Ground rules

- **Security first.** The whole project is built so that the only thing running
  as root is the tiny `helper/goblin_helper.py`, and it re-validates every
  argument. New privileged code needs a very good reason. Read
  [`SECURITY.md`](SECURITY.md).
- No new hard dependencies without discussion. The runtime is Python 3.11+,
  PyGObject, GTK4/libadwaita, `psutil`. Everything else is optional and probed
  for at runtime (`goblinmode/capabilities.py`).
- Match the surrounding style. No formatter is enforced; just keep it readable
  and consistent with the file you're in.

## Setup

```sh
git clone https://github.com/Bvaughan7/goblin-mode-pro
cd goblin-mode-pro
./install.sh                     # or run from source, see below
```

Run from a source checkout without installing:

```sh
PYTHONPATH=src python3 -m goblinmode.daemon -v      # daemon, foreground
PYTHONPATH=src python3 -m goblinmode.gui.app        # GUI
PYTHONPATH=src python3 -m goblinmode.cli status     # CLI
```

## Tests

```sh
python -m unittest discover -s tests
```

Stdlib `unittest` + `psutil` — no other deps. CI (`.github/workflows/ci.yml`)
runs the suite, compile-checks every module, import-checks the GUI against real
GTK, lints the shell scripts and validates `profiles/`.

Add a test for anything with logic. The pure-function modules (`capabilities`,
`sessions`, `cpuset`, `community`, `webdata`, `proton`, `config`, `runner`,
`preflight`) are all easy to cover; GUI modules are import-checked only.

## Where things live

| Path | What |
|---|---|
| `helper/goblin_helper.py` | the only root code — polkit-gated, stdlib + Gio |
| `src/goblinmode/daemon.py` | wires the components on one GLib loop |
| `src/goblinmode/payload.py` | apply / revert orchestration |
| `src/goblinmode/capabilities.py` | one-time hardware/software probe |
| `src/goblinmode/gui/` | GTK4 pages — pure D-Bus clients of the daemon |
| `src/goblinmode/ipc/daemon_bridge.py` | the session-bus interface |
| `profiles/` | community starter profiles (send a PR to add one) |
| `po/` | translations — see `po/README.md` |

## Good first issues

Look for the `good first issue` label. Some standing ones:

- Wrap more UI strings for translation (`_( )`) — `po/README.md`.
- A ⓘ help popover for a setting that doesn't have one — `gui/widgets/help.py`.
- A new entry in `profiles/` for a game you've tuned.
- Distro-specific setup tips in `gui/page_dashboard.py::set_kernel_nudge`.

## Roadmap

[`ROADMAP.md`](ROADMAP.md) is the menu. Comment on (or open) an issue before
starting something large so we don't duplicate work.
