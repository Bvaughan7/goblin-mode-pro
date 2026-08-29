# Command line

`goblin-mode-pro-cli` talks to the running daemon over the session bus — works
in a plain terminal or over SSH, no display.

```text
goblin-mode-pro-cli status [--json]      current state
goblin-mode-pro-cli boost / unboost      force performance mode on/off
goblin-mode-pro-cli health               the 0-10 readiness score
goblin-mode-pro-cli sessions [--game N]  recent session history
goblin-mode-pro-cli benchmark "Wow.exe"  arm a benchmark for a game
goblin-mode-pro-cli preflight [--fix]    run the system check
goblin-mode-pro-cli report               a bug report to stdout
goblin-mode-pro-cli setup                the full setup report
goblin-mode-pro-cli games                list profiles
```
