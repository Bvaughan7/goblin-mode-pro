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
Verified 2026-08-31 with `selftest --apply`: **15 PASS, 0 FAIL, 1 SKIP.**

| Capability | Result | Notes |
|---|---|---|
| Helper on the system bus | PASS | |
| polkit policy installed | PASS | all three actions declared |
| polkit agent | PASS | `polkit-kde-authentication-agent-1` |
| `manage-performance` | PASS | silent on the active session, as designed |
| `manage-kernel-tunables` | PASS | prompts, then cached for the session |
| `manage-hardware-thermal` | PASS | prompts |
| Helper capabilities | PASS | `CAP_SYS_NICE`, `CAP_SYS_RESOURCE` |
| CPU governor | PASS | `powersave` ⇄ `performance` |
| Energy performance preference | PASS | round-tripped and restored |
| Intel RAPL power limits | PASS | clamped to the firmware max — see below |
| AMD TDP (`ryzenadj`) | SKIP | Intel machine |
| **Fan spin-up** | **PASS** | `hwmon4/pwm1` 0 → 128 at the 40 % floor, then handed back to the EC |
| `vm.max_map_count` | PASS | |
| `vm.swappiness` | PASS | |
| `vm.compaction_proactiveness` | PASS | |
| `kernel.split_lock_mitigate` | PASS | |
| **`user.max_user_namespaces`** | **PASS** | was `EACCES` before the capability fix below |
| `kernel.unprivileged_userns_clone` | PASS | |
| `/etc/modprobe.d` writable | — | present and granted; not round-tripped |

#### Three findings from this machine

**`user.max_user_namespaces` had never worked, on any machine.** The helper's
unit grants `/proc/sys/user` under `ReadWritePaths=`, and a test asserts that it
does — but the unit also set `CapabilityBoundingSet=CAP_SYS_NICE`, and the
kernel gates `/proc/sys/user/*` writes on `CAP_SYS_RESOURCE` in the owning user
namespace (`set_permissions()` in `kernel/ucount.c` masks the write bit out of
the effective mode without it). Being root is not sufficient. The failure
surfaces as `EACCES`, which reads like a file-permission problem rather than a
sandbox one, and a process's capability set only exists at runtime, so no test
could see it. Fixed by adding `CAP_SYS_RESOURCE`, and **verified above**.

**`./install.sh` had never upgraded a running install.** It used `systemctl
enable --now`, which starts a stopped service and does nothing at all to a
running one — so re-running the installer left both the helper and the daemon
executing the previous release's code against the previous unit file. The
capability fix above sat installed-but-not-running across three reinstalls
because of it. Now `enable` then `restart`.

**PL1 on this machine sits at 107 W against a 45 W firmware maximum.** The
helper clamps every power-limit write to `constraint_0_max_power_uw`, so using
the power-limit feature *here* lowers PL1 rather than raising it. That is the
helper working as designed — but worth knowing before turning it on, so
`selftest` reports the clamp as a finding rather than leaving you to notice it.
(This laptop is thermally saturated at stock; raising limits on it is not the
right move regardless.)

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
