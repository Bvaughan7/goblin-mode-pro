# Security policy

## Reporting a vulnerability

Please report suspected vulnerabilities privately through the repository's
**Security advisories** page ("Report a vulnerability"), not via a public issue.
Include the affected component, a description of the problem, and reproduction
steps if you have them.

## Design

- The only component that runs as root is `goblin-mode-pro-helper`. It is a small
  D-Bus service that imports only the standard library and PyGObject, runs under a
  hardened systemd unit (`CapabilityBoundingSet=CAP_SYS_NICE`, `NoNewPrivileges`,
  `ProtectSystem=strict` with an explicit `ReadWritePaths` allowlist,
  `IPAddressDeny=any`, a syscall filter), and gates every mutating call behind
  polkit.
- The helper re-validates every argument regardless of the caller: the CPU
  governor must be one the kernel advertises; `renice` only raises priority and
  only for a process owned by the calling uid; RAPL power-limit writes are clamped
  to the firmware maximum; the AMD TDP figure is clamped to 4–120 W; sysctl keys
  are a fixed allowlist, each with an accepted numeric range; the sysctl target
  path is resolved and confirmed to be under `/proc/sys/`.
- AMD laptop TDP control shells out to `ryzenadj`, which needs raw hardware
  access the base unit denies. That access is granted **only** through a systemd
  drop-in (`helper-amd-tdp.conf`) that the installer applies **only when
  `ryzenadj` is present** — every Intel or `ryzenadj`-less system keeps the
  fully locked-down unit (`CAP_SYS_NICE` only, `PrivateDevices=yes`,
  `@raw-io` filtered).
- The daemon is unprivileged and its GUI bridge lives on the per-user session
  bus. The helper's well-known name can only be owned by root (enforced by the
  D-Bus bus policy), so it cannot be impersonated.
- Persistent kernel tunables (`manage-kernel-tunables`) always prompt for admin
  authentication; the runtime gaming knobs (`manage-performance`) are silent on
  the active local session by default and can be switched to prompt.
- Configuration input is constrained: a profile's `exe` may not contain a path
  separator, `..`, or a control character; per-game file names are produced by a
  separate slug function; user-supplied regular expressions are length-capped and
  evaluated against length-bounded strings.
- The generated launch wrapper imports environment variables as strict
  `NAME=VALUE` lines and never uses `eval` or `source`.
- The **daemon and helper make no network connections** (`IPAddressDeny=any` on
  the helper). The only outbound request the project makes is the *community
  profile sync*, which runs in the **GUI** process, only on an explicit click,
  only an anonymous HTTPS GET, and only to `raw.githubusercontent.com` (the host
  is pinned and re-checked after redirects; responses are capped at 64 KB). The
  downloaded JSON is filtered to the known profile fields and re-validated
  through `GameProfile` before it is saved.

## Threat model

Goblin Mode Pro assumes a single-user workstation where an active local login
session is trusted. It is not designed to defend one local user against another
on a shared machine.
