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

### The daemon's own interface

Measured 2026-09-02 on the Dell G7 below with
`python3 tests/conformance/daemon.py --apply`: **23 PASS / 0 FAIL / 9 SKIP**.

The SKIPs are deliberate and permanent. Six methods are never called by the
suite at all — they rewrite the user's per-game settings, delete files, or
write boot configuration — and the three kernel-tunable ones are graded at the
helper seam instead, where they can be applied and reverted against a snapshot.
The remaining skip is behaviour with two games running at once, which needs two
real games and is not reachable from a suite.

The first run scored 1 FAIL: ignoring a game could not be undone. That is
fixed, and the same check now verifies the fix.

### Both helper implementations, same machine, same score

The privileged helper exists twice: the Python one that ships, and the Rust
port. They serve the same frozen D-Bus interface, and on 2026-09-02 both were
graded by `tests/conformance/helper.py` on the Dell G7 below, running under the
real unit, real polkit and the real systemd sandbox.

| | Python | Rust |
|---|---|---|
| root run (`--apply --polkit-routing --prompts`) | 39 PASS / 0 FAIL / 1 SKIP | **39 PASS / 0 FAIL / 1 SKIP** |
| unprivileged run (`--apply`) | 19 PASS / 0 FAIL | **19 PASS / 0 FAIL** |
| `Renice` ownership gate | PASS | **PASS** |
| all 15 polkit routings | PASS | **PASS** |

The two runs grade disjoint sets, so both are needed: root cannot observe the
ownership gate at all, because `renice()` skips it for uid 0.

Also confirmed against the Rust helper on hardware, because neither is
observable from a test suite: `/etc/modprobe.d/goblin-mode-pro-nvidia.conf` is
written **0644**, not 0600 — the unit's `UMask=0077` is right for `/run` and
wrong for `/etc`, which is the v1.3.1 bug — and the service survived the switch
with `NRestarts=0` and nothing but INFO in its journal.

One known, harmless difference: an unknown method is refused with the same
error *name* (`org.freedesktop.DBus.Error.UnknownMethod`) but different wording
— GDBus says `No such method "X"`, zbus says `Unknown method 'X'`. The name is
the contract; the prose is not.

### Dell G7 7590 — i7-10750H / RTX 2060 — CachyOS (kernel 7.2.2)

Intel Comet Lake laptop, `intel_pstate` active, KDE Plasma 6 on Wayland.
Verified 2026-08-31 with `selftest --apply`: **16 PASS, 0 FAIL, 1 SKIP** —
the only SKIP is `ryzenadj`, which is correct on an Intel machine.

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
| **`nvidia-drm.modeset` write** | **PASS** | the root helper wrote the modprobe.d drop-in inside its sandbox, 29 bytes = `options nvidia_drm modeset=1` exactly |
| sched_ext support | PASS | 13 schedulers available; `scx_loader` reachable, polkit prompts as it should |

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

### What is still unverified, and why

Two paths remain unproven on real hardware, and no amount of testing here can
change that:

- **AMD `ryzenadj` TDP and Curve Optimizer re-apply** — nobody involved has an
  AMD laptop. Auditing them for the new `tests/test_helper_amd.py` did find a
  real bug: `set_tdp` raises the *fast* (burst) limit to sustained + 8 W, but
  the snapshot only recorded STAPM, so `reset_tdp` restored the fast limit to
  the **sustained** value. A machine shipping stapm = 25 W / fast = 30 W
  silently lost 5 W of burst headroom after any set/reset cycle, and kept
  losing it until the next reboot. All three limits are now snapshotted and
  each is restored to its own original. The parsing, snapshotting and restore
  logic is under test with a faked `ryzenadj`; whether `ryzenadj` reaches the
  silicon is what an AMD machine running `selftest --apply` would answer.
**Writing `nvidia-drm.modeset` is now verified** (Dell G7, NVIDIA): the root
helper wrote `/etc/modprobe.d/goblin-mode-pro-nvidia.conf` from inside its
sandbox, 29 bytes matching `options nvidia_drm modeset=1` exactly. That found
one thing: the unit's `UMask=0077` — right for the state it keeps in `/run` —
made the file `0600`, where every other file in `modprobe.d` is `0644` and
initramfs tooling and the user both need to read it. It is chmodded explicitly
now.

Fan spin-up also exposed a flaw in the probe itself rather than the feature: it
read the PWM once, a second after writing, and on a cool idle machine the
embedded controller pulls the duty back before then — so the same machine
alternated PASS and FAIL. It samples for three seconds and takes the peak now,
and a channel that genuinely never moves is reported as a SKIP naming the EC as
the reason, not a failure.

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
