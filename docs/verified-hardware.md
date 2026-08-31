# Verified hardware

What has actually been tested, on what, and what happened. Every row here comes
from `goblin-mode-pro-cli selftest` on a real machine — not from a passing test
suite, which can't see any of this.

Most of Goblin Mode Pro is covered by unit tests. The privileged parts are not,
and can't be: fan control, TDP, undervolt re-apply, nvidia modeset and the
sysctl writes only fail at runtime, against real hardware, behind a real polkit
agent and a real systemd sandbox. This page is the record of which ones have
been proven, on which machines.

**Please add yours.** Run `goblin-mode-pro-cli selftest --json` and open an
issue with the output. A row saying a capability *doesn't* exist on your machine
is just as useful as one saying it does.

## Legend

| | |
|---|---|
| **PASS** | round-tripped with `--apply`: applied, read back, reverted, read back |
| **FAIL** | the machine has the capability and it did not work |
| **SKIP** | the machine doesn't have the capability — an expected, valid answer |
| **—** | not tested on this machine yet |

## Machines

### Dell G7 7590 — i7-10750H / RTX 2060 — CachyOS (kernel 7.2.2)

Intel Comet Lake laptop, `intel_pstate` active, KDE Plasma 6 on Wayland.
Tested 2026-08-31 with `selftest --apply`.

| Capability | Result | Notes |
|---|---|---|
| Helper on the system bus | PASS | |
| polkit policy installed | PASS | all three actions declared |
| polkit agent | PASS | `polkit-kde-authentication-agent-1` |
| `manage-performance` | PASS | silent on the active session, as designed |
| `manage-kernel-tunables` | PASS | prompts, then cached for the session |
| `manage-hardware-thermal` | PASS | prompts |
| Helper capabilities | **FAIL → fixed** | held `CAP_SYS_NICE` only; `CAP_SYS_RESOURCE` was missing. See the note below |
| CPU governor | PASS | `powersave` ⇄ `performance` |
| Energy performance preference | PASS | `balance_power` ⇄ `balance_performance` |
| Intel RAPL power limits | PASS | with a caveat, below |
| AMD TDP (`ryzenadj`) | SKIP | Intel machine |
| Fan control | SKIP | 2 PWM channels exist (`hwmon4/pwm1`, `pwm2`) but the round-trip was not completed |
| `vm.max_map_count` | PASS | |
| `vm.swappiness` | PASS | |
| `vm.compaction_proactiveness` | PASS | |
| `kernel.split_lock_mitigate` | PASS | |
| `user.max_user_namespaces` | **FAIL → fixed** | `EACCES` — the capability bug below |
| `kernel.unprivileged_userns_clone` | PASS | |
| `/etc/modprobe.d` writable | — | present and granted; not round-tripped |

#### Two findings from this machine

**`user.max_user_namespaces` had never worked, on any machine.** The helper's
unit grants `/proc/sys/user` under `ReadWritePaths=`, and a test asserts that it
does — but the unit also set `CapabilityBoundingSet=CAP_SYS_NICE`, and the
kernel gates `/proc/sys/user/*` writes on `CAP_SYS_RESOURCE` in the owning user
namespace (`set_permissions()` in `kernel/ucount.c` masks the write bit out of
the effective mode without it). Being root is not sufficient. The failure
surfaces as `EACCES`, which reads like a file-permission problem rather than a
sandbox one, and a process's capability set only exists at runtime, so no test
could see it. `CAP_SYS_RESOURCE` has been added to the bounding set, and
`selftest` now reports the helper's live capabilities so the next missing one is
visible without `--apply`.

**PL1 on this machine sits at 107 W against a 45 W firmware maximum.** The
helper clamps every power-limit write to `constraint_0_max_power_uw`, so using
the power-limit feature *here* lowers PL1 rather than raising it. That is the
helper working as designed — but it is worth knowing before turning it on, so
`selftest` now reports the clamp as a finding rather than leaving you to notice
it. (This laptop is thermally saturated at stock; raising limits on it is not
the right move regardless.)

### Your machine here

Run `goblin-mode-pro-cli selftest --json`, open an issue, and this table grows.
Machines that are *especially* wanted, because nothing below has ever been
verified on real hardware:

- **Any AMD laptop with `ryzenadj`** — TDP control and Curve Optimizer re-apply
  are both marked experimental purely because nobody has run them. The
  `ryzenadj` access path (`ryzen_smu` module vs `/dev/mem`) is the thing to
  watch; `selftest` reports which one it will take.
- **Any machine where the EC does *not* own the fan curve** — desktops and a
  few gaming laptops. Fan spin-up has never been round-tripped.
- **An NVIDIA machine where `nvidia-drm.modeset` gets written**, rather than
  only read.
- **A handheld** (Steam Deck, ROG Ally, Legion Go) — the auto-profile path.
