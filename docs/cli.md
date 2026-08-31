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
goblin-mode-pro-cli selftest [--apply] [--json]
                                          prove the privileged paths on this
                                          machine (needs no daemon)
goblin-mode-pro-cli setup                the full setup report
goblin-mode-pro-cli games                list profiles
goblin-mode-pro-cli compare GAME         diff the last two sessions for a game
goblin-mode-pro-cli works-for-me GAME [--note TEXT]
                                          share what worked (opens a pre-filled
                                          GitHub issue link, telemetry-free)
goblin-mode-pro-cli gamescope-session [--game NAME] [-- COMMAND...]
                                          launch a standalone gamescope session
                                          (Steam Big Picture by default)
```

## `selftest`

Everything else here talks to the daemon. `selftest` does not — it goes
straight to the privileged helper, so it still works when the daemon is the
thing that's broken, and it needs no display.

It probes each privileged capability and reports what it found:

```text
$ goblin-mode-pro-cli selftest
goblin-mode-pro selftest - read-only
  Intel(R) Core(TM) i7-10750H CPU @ 2.60GHz / intel, nvidia / cachyos 7.2.2

Helper and authorization
  PASS  Privileged helper         reachable on the system bus
  PASS  polkit policy file        installed, declares all 3 actions
  PASS  polkit agent              polkit-kde-authentication-agent-1 is running
  PASS  Helper capabilities       holds CAP_SYS_NICE, CAP_SYS_RESOURCE
...
```

Nothing ever SKIPs silently — "no writable PWM channel on this machine (the EC
owns the fan curve, which is normal on a laptop)" is a result worth having, and
is why fan spin-up does nothing on most laptops.

### `--apply`

Read-only mode changes nothing, and so proves nothing: the values it reads are
written by the **root** helper, so testing them as your user says nothing either
way. `--apply` round-trips each capability — apply, read back, revert, read
back — and reports what was observed at each step. It is the only mode that
actually proves a write path works.

It restores everything it touches. Where a value is re-asserted rather than
changed (power limits, sysctls) that is deliberate: it exercises the write path
without moving your machine's thermal envelope as a side effect of a test. The
fan test is capped at a few seconds and hands control back to the EC in a
`finally`, on every exit path including Ctrl-C.

Expect a polkit password prompt: the kernel-tunable and thermal actions are
meant to prompt. If a call times out, that's usually a prompt waiting for an
answer on a screen you're not looking at.

### `--json`

Machine-readable, and the most useful thing you can attach to an issue — it
records the machine, every capability, and the observed values. The same data
is embedded in `goblin-mode-pro-cli report`, and the human-readable version is
behind **About → Troubleshooting → Debug Information** in the app.

Results from real machines are collected in
[Verified hardware](verified-hardware.md). Please add yours.

### Exit code

`0` if nothing failed, `1` if any capability FAILed. A SKIP is not a failure —
"this machine can't do that" is a valid answer, and the expected one for fan
control on most laptops and `ryzenadj` on every Intel machine.
