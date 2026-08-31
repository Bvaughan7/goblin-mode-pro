"""``goblin-mode-pro-cli selftest`` - prove the privileged paths on this machine.

Most of what this tool does is covered by unit tests, but the parts that matter
most *cannot* be: fan control, TDP, undervolt re-apply, nvidia modeset and the
sysctl writes only fail at runtime, on a real bus, against real hardware, behind
a real polkit agent and a real systemd sandbox. A green test suite says nothing
about whether any of them work on **your** machine.

This module probes each of those capabilities and reports what actually
happened, so an unverified feature is a visible SKIP rather than a silent
assumption. It is deliberately CLI-only and gi-light: it has to run over SSH and
in a TTY after a failed boot, which is exactly when it is worth having.

Two modes:

* **read-only** (default) - look at what the machine exposes and what the helper
  can reach. Changes nothing.
* **``--apply``** - additionally round-trip each capability: apply, read back,
  revert, read back. This is the only mode that actually proves a write path.

The rule the output follows: **never SKIP silently.** "SKIP - no writable PWM
channel (the EC owns the fan curve, which is normal on a laptop)" is a useful
result. A blank line is not.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"
INFO = "INFO"

#: polkit actions the helper routes methods to, in escalating order of what
#: they let a caller do. Mirrors helper/goblin_helper.py's three constants;
#: tests/test_selftest.py asserts the two lists stay in step.
POLKIT_ACTIONS = (
    "com.goblinmode.pro.manage-performance",
    "com.goblinmode.pro.manage-kernel-tunables",
    "com.goblinmode.pro.manage-hardware-thermal",
)

#: sysctl keys the helper will write. Mirrors goblin_helper.SYSCTL_ALLOW;
#: tests/test_selftest.py fails the build if they drift apart.
SYSCTL_KEYS = (
    "vm.max_map_count",
    "vm.swappiness",
    "vm.compaction_proactiveness",
    "kernel.split_lock_mitigate",
    "user.max_user_namespaces",
    "kernel.unprivileged_userns_clone",
)

#: Capabilities the helper must hold, and what needs each. Mirrors
#: goblin_helper.HELPER_CAPABILITIES; tests/test_selftest.py keeps them in step.
REQUIRED_CAPABILITIES = {
    "CAP_SYS_NICE": "renicing a game process owned by another user",
    "CAP_SYS_RESOURCE": "writing /proc/sys/user/* (the user.max_user_namespaces "
                        "pre-flight fix)",
}

#: capability bit numbers we care about, from include/uapi/linux/capability.h
_CAP_BITS = {"CAP_SYS_NICE": 23, "CAP_SYS_RESOURCE": 24, "CAP_SYS_ADMIN": 21}

#: the fan duty the helper's own floor pins spin-up to
MIN_FAN_PERCENT = 40
#: hard ceiling on how long --apply is allowed to leave fans off the EC curve
FAN_TEST_SECONDS = 5.0

_CPU = Path("/sys/devices/system/cpu")
_POWERCAP = Path("/sys/class/powercap")
_HWMON = Path("/sys/class/hwmon")
_POLICY_FILE = Path("/usr/share/polkit-1/actions/com.goblinmode.pro.policy")

#: known session polkit agents, for the "will a prompt actually appear" check
_AGENT_PROCESS_NAMES = (
    "polkit-kde-authentication-agent-1",
    "polkit-gnome-authentication-agent-1",
    "polkit-mate-authentication-agent-1",
    "xfce-polkit", "lxpolkit", "lxqt-policykit-agent",
    "polkit-efl-authentication-agent-1", "ukui-polkit-agent",
    "hyprpolkitagent", "systemd-ask-password-agent",
)


@dataclass
class Result:
    """One capability's verdict. `detail` is a sentence, and never empty."""
    name: str
    title: str
    status: str
    detail: str
    section: str = "General"
    observed: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# small readers - all safe to call unprivileged, none raise
# ---------------------------------------------------------------------------
def _read(path: str | Path) -> str | None:
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def _read_int(path: str | Path) -> int | None:
    raw = _read(path)
    try:
        return int(raw) if raw is not None else None
    except ValueError:
        return None


def _run(cmd: list[str], timeout: float = 5.0) -> tuple[int, str]:
    """Run a command; return (rc, combined output). Never raises."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, str(exc)


def _rapl_package_zone() -> Path | None:
    """The powercap zone named package-0, by name rather than by index."""
    for zone in sorted(_POWERCAP.glob("intel-rapl:*")):
        if (_read(zone / "name") or "").startswith("package"):
            return zone
    return None


def _writable_pwm_channels() -> list[Path]:
    """hwmon channels exposing the standard pwmN + pwmN_enable pair."""
    found: list[Path] = []
    if not _HWMON.is_dir():
        return found
    for hwmon in sorted(_HWMON.glob("hwmon*")):
        for pwm in sorted(hwmon.glob("pwm[0-9]*")):
            if re.fullmatch(r"pwm\d+", pwm.name) and (
                    hwmon / f"{pwm.name}_enable").exists():
                found.append(pwm)
    return found


def _ryzenadj_access_path() -> tuple[str, str]:
    """Which mechanism ryzenadj will use to reach the SMU, and why it matters.

    ryzenadj tries the ryzen_smu kernel module first and falls back to /dev/mem;
    on a kernel built with CONFIG_STRICT_DEVMEM (most distro kernels) that
    fallback is blocked, which is the usual reason TDP control silently does
    nothing on an AMD laptop.
    """
    if Path("/dev/ryzen_smu_drv").exists() or Path("/sys/module/ryzen_smu").exists():
        return "ryzen_smu", "the ryzen_smu kernel module is loaded - the reliable path"
    if Path("/dev/mem").exists():
        return "/dev/mem", ("no ryzen_smu module; ryzenadj will fall back to "
                            "/dev/mem, which CONFIG_STRICT_DEVMEM usually blocks")
    return "none", "neither the ryzen_smu module nor /dev/mem is available"


def _helper_pid() -> int | None:
    rc, out = _run(["systemctl", "show", "goblin-mode-pro-helper",
                    "-p", "MainPID", "--value"])
    if rc != 0:
        return None
    try:
        pid = int(out.strip())
    except ValueError:
        return None
    return pid or None


def _read_cap_set(pid: int, field: str) -> int | None:
    status = _read(f"/proc/{pid}/status")
    if status is None:
        return None
    m = re.search(rf"^{field}:\s*([0-9a-fA-F]+)$", status, re.M)
    return int(m.group(1), 16) if m else None


def _decode_caps(mask: int) -> list[str]:
    return [name for name, bit in _CAP_BITS.items() if mask >> bit & 1]


def _agent_running() -> str | None:
    """Name of a running session polkit agent, if one can be found."""
    try:
        import psutil
    except ImportError:
        return None
    for proc in psutil.process_iter(["name"]):
        name = (proc.info.get("name") or "")
        if name in _AGENT_PROCESS_NAMES:
            return name
    return None


def _pkcheck(action: str) -> tuple[str, str]:
    """Can this process already obtain `action` without a prompt?

    Deliberately runs pkcheck *without* --allow-user-interaction: the read-only
    mode must never pop a password dialog. "auth_required" is a normal, healthy
    answer for the kernel-tunables and thermal actions - it means the policy is
    installed and polkit will prompt when the time comes.
    """
    if not shutil.which("pkcheck"):
        return "unknown", "pkcheck is not installed (polkit's CLI)"
    rc, out = _run(["pkcheck", "--action-id", action, "--process", str(os.getpid())])
    if rc == 0:
        return "yes", "already authorized for this session, no prompt needed"
    if "auth_required" in out or rc == 2:
        return "prompt", "polkit will prompt for a password when this is used"
    if "not registered" in out or "No such" in out:
        return "missing", "polkit does not know this action - the policy file is not installed"
    return "no", out.splitlines()[0] if out else f"pkcheck exited {rc}"


# ---------------------------------------------------------------------------
# the probes
# ---------------------------------------------------------------------------
class SelfTest:
    """Runs every probe and collects Results. `apply` enables round-trips.

    The helper connection is made lazily and once: on a machine where the
    helper isn't installed we still want every unprivileged probe to report,
    so a missing helper degrades the round-trips to SKIP rather than aborting.
    """

    def __init__(self, apply: bool = False) -> None:
        self.apply = apply
        self.results: list[Result] = []
        self._helper = None
        self._helper_error: str | None = None
        self._helper_tried = False

    # -- plumbing ----------------------------------------------------
    def _add(self, name, title, status, detail, section="General", **observed) -> Result:
        r = Result(name=name, title=title, status=status, detail=detail,
                   section=section, observed=observed)
        self.results.append(r)
        return r

    def helper(self):
        """The HelperClient, or None. Imported here so `import selftest` stays
        gi-free - the module is imported by the CLI, which must work headless."""
        if not self._helper_tried:
            self._helper_tried = True
            try:
                from goblinmode.ipc.helper_client import HelperClient
                client = HelperClient()
                if client.available():
                    self._helper = client
                else:
                    self._helper_error = "the helper is not on the system bus"
            except Exception as exc:                    # noqa: BLE001 - report it
                self._helper_error = f"{type(exc).__name__}: {exc}"
        return self._helper

    def _round_trip(self, name, title, section, apply_fn, read_fn, revert_fn,
                    expected, unit=""):
        """apply -> read back -> revert -> read back, reported step by step.

        Any exception is a FAIL with the exception text, and the revert is
        attempted regardless - a selftest that leaves the machine tuned would
        be worse than no selftest at all.
        """
        before = read_fn()
        applied = reverted = None
        try:
            ok = apply_fn()
            applied = read_fn()
            if not ok:
                return self._add(name, title, FAIL,
                                 "the helper refused the call (it returned false)",
                                 section, before=before)
            if expected is not None and applied != expected:
                return self._add(
                    name, title, FAIL,
                    f"wrote {expected}{unit} but read back {applied}{unit} - "
                    "the call was accepted and did not take effect",
                    section, before=before, applied=applied, expected=expected)
        except Exception as exc:                        # noqa: BLE001
            return self._add(name, title, FAIL, f"{type(exc).__name__}: {exc}",
                             section, before=before, applied=applied)
        finally:
            try:
                revert_fn()
                reverted = read_fn()
            except Exception as exc:                    # noqa: BLE001
                self._add(f"{name}_revert", f"{title} (revert)", FAIL,
                          f"could not revert: {type(exc).__name__}: {exc} - "
                          f"the machine may still be at {applied}{unit}", section)
        detail = f"applied {applied}{unit} (was {before}{unit}), reverted to {reverted}{unit}"
        if reverted != before:
            return self._add(name, title, FAIL,
                             f"{detail} - revert did not restore the original value",
                             section, before=before, applied=applied, reverted=reverted)
        return self._add(name, title, PASS, detail, section,
                         before=before, applied=applied, reverted=reverted)

    # -- helper & authorization --------------------------------------
    def probe_helper(self) -> None:
        sec = "Helper and authorization"
        if self.helper() is not None:
            self._add("helper_bus", "Privileged helper", PASS,
                      "reachable on the system bus", sec)
        else:
            self._add("helper_bus", "Privileged helper", FAIL,
                      f"{self._helper_error} - start it with "
                      "`sudo systemctl start goblin-mode-pro-helper`; every "
                      "privileged capability below will SKIP without it", sec)

        if _POLICY_FILE.exists():
            declared = re.findall(r'action id="([^"]+)"', _read(_POLICY_FILE) or "")
            missing = [a for a in POLKIT_ACTIONS if a not in declared]
            self._add("polkit_policy", "polkit policy file",
                      FAIL if missing else PASS,
                      f"installed, missing {', '.join(missing)}" if missing
                      else f"installed, declares all {len(POLKIT_ACTIONS)} actions", sec,
                      path=str(_POLICY_FILE), declared=declared)
        else:
            self._add("polkit_policy", "polkit policy file", FAIL,
                      f"{_POLICY_FILE} is missing - every privileged call will "
                      "be refused; re-run ./install.sh", sec)

        agent = _agent_running()
        if agent:
            self._add("polkit_agent", "polkit agent", PASS,
                      f"{agent} is running, so a password prompt can appear", sec,
                      agent=agent)
        else:
            self._add("polkit_agent", "polkit agent", SKIP,
                      "no session polkit agent found - actions that prompt will "
                      "fail silently rather than asking. Normal over SSH; on a "
                      "desktop it means the agent isn't started", sec)

        for action in POLKIT_ACTIONS:
            verdict, why = _pkcheck(action)
            status = {"yes": PASS, "prompt": INFO,
                      "missing": FAIL, "unknown": SKIP}.get(verdict, FAIL)
            self._add(f"polkit:{action.rsplit('.', 1)[-1]}",
                      action.rsplit(".", 1)[-1], status, why, sec,
                      action=action, verdict=verdict)

    def probe_capabilities(self) -> None:
        """What Linux capabilities the running helper actually holds.

        Being root is not enough: the unit drops everything outside its
        bounding set, and a missing capability fails at the syscall with
        EACCES - which reads like a file-permission problem, not a sandbox
        one. That is how `user.max_user_namespaces` shipped broken. This probe
        exists so the next one is visible immediately.
        """
        sec = "Helper and authorization"
        pid = _helper_pid()
        if pid is None:
            self._add("helper_caps", "Helper capabilities", SKIP,
                      "could not find the helper's PID to read its capability "
                      "set (is it running under systemd?)", sec)
            return
        bnd = _read_cap_set(pid, "CapBnd")
        eff = _read_cap_set(pid, "CapEff")
        if bnd is None:
            self._add("helper_caps", "Helper capabilities", SKIP,
                      f"could not read /proc/{pid}/status", sec)
            return
        held = sorted(_decode_caps(eff if eff is not None else bnd))
        missing = [c for c in REQUIRED_CAPABILITIES if c not in held]
        if missing:
            self._add("helper_caps", "Helper capabilities", FAIL,
                      f"holds {', '.join(held) or 'none'}; missing "
                      f"{', '.join(missing)} - "
                      + "; ".join(f"{c} is needed for {REQUIRED_CAPABILITIES[c]}"
                                  for c in missing)
                      + ". Re-run ./install.sh and restart the helper", sec,
                      held=held, missing=missing)
        else:
            self._add("helper_caps", "Helper capabilities", PASS,
                      f"holds {', '.join(held)} - everything the helper needs", sec,
                      held=held)

    # -- CPU ---------------------------------------------------------
    def probe_governor(self) -> None:
        sec = "CPU"
        avail = _read(_CPU / "cpu0/cpufreq/scaling_available_governors")
        current = _read(_CPU / "cpu0/cpufreq/scaling_governor")
        driver = _read(_CPU / "cpu0/cpufreq/scaling_driver") or "unknown"
        if current is None:
            self._add("governor", "CPU governor", SKIP,
                      "no cpufreq interface on this machine (no "
                      "/sys/.../cpu0/cpufreq/scaling_governor)", sec)
            return
        governors = (avail or "").split()
        self._add("governor", "CPU governor", INFO,
                  f"{current} (driver {driver}; available: "
                  f"{', '.join(governors) or 'unknown'})", sec,
                  current=current, available=governors, driver=driver)
        if not self.apply:
            return
        h = self.helper()
        if h is None:
            self._add("governor_rt", "CPU governor round-trip", SKIP,
                      "no helper on the bus", sec)
            return
        target = next((g for g in ("performance", "powersave") if g != current
                       and g in governors), None)
        if target is None:
            self._add("governor_rt", "CPU governor round-trip", SKIP,
                      f"only one usable governor ({current}) - nothing to "
                      "switch to without changing behaviour", sec)
            return
        self._round_trip(
            "governor_rt", "CPU governor round-trip", sec,
            apply_fn=lambda: h.set_governor(target),
            read_fn=lambda: _read(_CPU / "cpu0/cpufreq/scaling_governor"),
            revert_fn=lambda: h.set_governor(current),
            expected=target)

    def probe_epp(self) -> None:
        sec = "CPU"
        path = _CPU / "cpu0/cpufreq/energy_performance_preference"
        current = _read(path)
        if current is None:
            self._add("epp", "Energy performance preference", SKIP,
                      "no EPP interface - normal outside intel_pstate/amd_pstate "
                      "in active mode", sec)
            return
        avail = (_read(_CPU / "cpu0/cpufreq/energy_performance_available_preferences")
                 or "").split()
        self._add("epp", "Energy performance preference", INFO,
                  f"{current} (available: {', '.join(avail) or 'unknown'})", sec,
                  current=current, available=avail)
        if not self.apply:
            return
        h = self.helper()
        if h is None:
            self._add("epp_rt", "EPP round-trip", SKIP, "no helper on the bus", sec)
            return
        target = next((e for e in ("balance_performance", "performance", "default")
                       if e != current and e in avail), None)
        if target is None:
            self._add("epp_rt", "EPP round-trip", SKIP,
                      f"no alternative preference to switch to (have: "
                      f"{', '.join(avail) or 'none'})", sec)
            return
        self._round_trip(
            "epp_rt", "EPP round-trip", sec,
            apply_fn=lambda: h.set_epp(target),
            read_fn=lambda: _read(path),
            revert_fn=lambda: h.set_epp(current),
            expected=target)

    # -- power -------------------------------------------------------
    def probe_rapl(self) -> None:
        sec = "Power"
        zone = _rapl_package_zone()
        if zone is None:
            self._add("rapl", "Intel RAPL power limits", SKIP,
                      "no powercap package zone - RAPL is Intel-only and this "
                      "machine doesn't expose it", sec)
            return
        pl1 = _read_int(zone / "constraint_0_power_limit_uw")
        pl2 = _read_int(zone / "constraint_1_power_limit_uw")
        fw_max = _read_int(zone / "constraint_0_max_power_uw")
        summary = (f"{zone.name}: PL1 {_w(pl1)}, PL2 {_w(pl2)}, firmware max "
                   f"{_w(fw_max) if fw_max else 'not published'}")
        over = (fw_max and pl1 and pl1 > fw_max)
        if over:
            # Worth saying out loud: a sustained limit above what the firmware
            # rates the package for is how a laptop ends up pinned at TjMax.
            summary += (" - PL1 is above the firmware max, so the package is "
                        "allowed to draw more than it is rated for and will "
                        "be held back by temperature instead of by power")
        self._add("rapl", "Intel RAPL power limits", INFO, summary, sec,
                  zone=zone.name, pl1_uw=pl1, pl2_uw=pl2, firmware_max_uw=fw_max,
                  pl1_above_firmware_max=bool(over))
        if not self.apply:
            return
        h = self.helper()
        if h is None:
            self._add("rapl_rt", "RAPL round-trip", SKIP, "no helper on the bus", sec)
            return
        if pl1 is None or pl2 is None:
            self._add("rapl_rt", "RAPL round-trip", SKIP,
                      "could not read the current limits to restore them "
                      "afterwards - refusing to write", sec)
            return
        # Re-assert the limits the machine is already at: this proves the write
        # path end to end without changing the thermal envelope, which on a
        # laptop is not ours to move as a side effect of a test.
        #
        # The expected read-back is the *clamped* value, not what we asked for:
        # SetPowerLimits deliberately caps every write at the zone's
        # constraint_0_max_power_uw. On a machine whose PL1 already sits above
        # the firmware max (this is common - the vendor sets it in firmware)
        # that means asking for the current value legitimately lowers it, and
        # asserting "read back what I wrote" would report a false failure.
        want = min(pl1, fw_max) if fw_max else pl1
        self._round_trip(
            "rapl_rt", "RAPL round-trip", sec,
            apply_fn=lambda: h.set_power_limits(pl1, pl2),
            read_fn=lambda: _read_int(zone / "constraint_0_power_limit_uw"),
            revert_fn=h.reset_power_limits,
            expected=want, unit=" uW")
        if want != pl1:
            self._add("rapl_clamp", "RAPL firmware clamp", INFO,
                      f"a power-limit write on this machine is clamped from "
                      f"{_w(pl1)} to the firmware max {_w(fw_max)} - so using "
                      f"the power-limit feature here *lowers* PL1 rather than "
                      f"raising it", sec, requested_uw=pl1, clamped_uw=want)

    def probe_ryzenadj(self) -> None:
        sec = "Power"
        binary = shutil.which("ryzenadj")
        if binary is None:
            self._add("ryzenadj", "AMD TDP (ryzenadj)", SKIP,
                      "ryzenadj is not installed - needed only for AMD laptop "
                      "TDP control (AUR: ryzenadj, COPR: ryzenadj)", sec)
            return
        path, why = _ryzenadj_access_path()
        rc, out = _run([binary, "--info"], timeout=10.0)
        stapm = None
        m = re.search(r"STAPM LIMIT\s*\|\s*([\d.]+)", out)
        if m:
            stapm = float(m.group(1))
        status = PASS if rc == 0 and stapm is not None else FAIL
        detail = (f"{binary}, access path: {path} ({why}); "
                  f"STAPM limit {stapm} W" if status == PASS else
                  f"{binary} present but `ryzenadj --info` failed (rc={rc}, "
                  f"access path {path}: {why})")
        self._add("ryzenadj", "AMD TDP (ryzenadj)", status, detail, sec,
                  binary=binary, access_path=path, stapm_w=stapm, rc=rc)
        if not self.apply or status != PASS:
            return
        h = self.helper()
        if h is None:
            self._add("tdp_rt", "AMD TDP round-trip", SKIP, "no helper on the bus", sec)
            return
        watts = int(stapm)
        self._round_trip(
            "tdp_rt", "AMD TDP round-trip", sec,
            apply_fn=lambda: h.set_tdp(watts),
            read_fn=lambda: _ryzenadj_stapm(binary),
            revert_fn=h.reset_tdp,
            expected=None, unit=" W")

    # -- thermal -----------------------------------------------------
    def probe_fans(self) -> None:
        sec = "Thermal"
        channels = _writable_pwm_channels()
        if not channels:
            self._add("fans", "Fan control", SKIP,
                      "no writable PWM channel on this machine - the EC owns "
                      "the fan curve, which is normal on most laptops and "
                      "means fan spin-up can never work here", sec)
            return
        names = [f"{c.parent.name}/{c.name}" for c in channels]
        modes = {n: _read_int(c.parent / f"{c.name}_enable")
                 for n, c in zip(names, channels, strict=False)}
        self._add("fans", "Fan control", INFO,
                  f"{len(channels)} PWM channel(s): {', '.join(names)} "
                  f"(enable modes: {modes})", sec,
                  channels=names, enable_modes=modes)
        if not self.apply:
            return
        h = self.helper()
        if h is None:
            self._add("fans_rt", "Fan spin-up round-trip", SKIP,
                      "no helper on the bus", sec)
            return
        first = channels[0]
        before = _read_int(first)
        applied = None
        try:
            ok = h.spin_up_fans(MIN_FAN_PERCENT)
            time.sleep(min(1.0, FAN_TEST_SECONDS))
            applied = _read_int(first)
            if not ok:
                self._add("fans_rt", "Fan spin-up round-trip", FAIL,
                          "the helper refused SpinUpFans (check the "
                          "manage-hardware-thermal polkit action)", sec,
                          before=before)
            elif applied is not None and before is not None and applied <= before:
                self._add("fans_rt", "Fan spin-up round-trip", FAIL,
                          f"SpinUpFans({MIN_FAN_PERCENT}%) was accepted but "
                          f"{first.name} did not rise ({before} -> {applied})", sec,
                          before=before, applied=applied)
            else:
                self._add("fans_rt", "Fan spin-up round-trip", PASS,
                          f"{first.parent.name}/{first.name} {before} -> {applied} "
                          f"at the {MIN_FAN_PERCENT}% floor, then handed back "
                          "to the EC", sec, before=before, applied=applied)
        except KeyboardInterrupt:
            self._add("fans_rt", "Fan spin-up round-trip", FAIL,
                      "interrupted - fans handed back to the EC", sec, before=before)
            raise
        except Exception as exc:                # noqa: BLE001 - report, don't abort
            self._add("fans_rt", "Fan spin-up round-trip", FAIL,
                      _explain_call_failure(exc, "SpinUpFans"), sec, before=before)
        finally:
            # Unconditional, on every exit path including KeyboardInterrupt:
            # leaving the fans off the EC curve is a thermal-safety problem,
            # not a failed test.
            try:
                h.reset_fans()
            except Exception as exc:            # noqa: BLE001
                self._add("fans_reset", "Fan control handback", FAIL,
                          f"ResetFans failed: {exc} - run "
                          "`sudo systemctl restart goblin-mode-pro-helper`, "
                          "which resets fans on startup", sec)

    # -- kernel tunables ---------------------------------------------
    def probe_sysctls(self) -> None:
        sec = "Kernel tunables"
        h = self.helper() if self.apply else None
        for key in SYSCTL_KEYS:
            path = Path("/proc/sys") / key.replace(".", "/")
            value = _read(path)
            if value is None:
                self._add(f"sysctl:{key}", key, SKIP,
                          "not present on this kernel"
                          + (" (a Debian/Ubuntu downstream knob)"
                             if key == "kernel.unprivileged_userns_clone" else ""),
                          sec, path=str(path))
                continue
            if not self.apply:
                self._add(f"sysctl:{key}", key, INFO, f"= {value}", sec,
                          value=value, path=str(path))
                continue
            if h is None:
                self._add(f"sysctl:{key}", key, SKIP,
                          f"= {value}, but there is no helper on the bus to "
                          "round-trip it", sec, value=value)
                continue
            # Re-write the value it already has: proves the write path without
            # changing how the machine behaves.
            self._round_trip(
                f"sysctl:{key}", key, sec,
                apply_fn=lambda k=key, v=value: h.set_sysctl(k, v),
                read_fn=lambda p=path: _read(p),
                revert_fn=lambda k=key: h.revert_sysctl(k),
                expected=value)

    def probe_sched_ext(self) -> None:
        """sched_ext support, and whether scx_loader will take our calls.

        Not routed through the helper: scx_loader's bus policy lets any user
        call it and delegates to polkit, so this is the one privileged-ish
        capability Goblin drives directly. Read-only here - `--apply` does not
        switch the system scheduler, because doing that behind someone's back
        to prove a point is not a reasonable thing for a test to do.
        """
        sec = "CPU scheduler"
        from goblinmode import scx

        st = scx.ScxManager().state()
        if not st["kernel"]:
            self._add("sched_ext", "sched_ext support", SKIP,
                      "this kernel has no /sys/kernel/sched_ext - it was built "
                      "without CONFIG_SCHED_CLASS_EXT, so no sched_ext "
                      "scheduler can run here", sec)
            return
        if not st["loader"]:
            self._add("sched_ext", "sched_ext support", SKIP,
                      "the kernel supports sched_ext but scx_loader is not "
                      "installed (Arch/CachyOS: scx-scheds) - install it to "
                      "use per-game schedulers", sec, kernel_state=st.get("kernel_state"))
            return
        self._add("sched_ext", "sched_ext support", PASS,
                  f"kernel state {st.get('kernel_state')}, "
                  f"{len(st['supported'])} scheduler(s) available: "
                  f"{', '.join(st['supported'])}", sec,
                  schedulers=st["supported"], kernel_state=st.get("kernel_state"))
        self._add("scx_running", "Running scheduler", INFO,
                  f"scx_{st['running']} (mode {st['mode']})" if st["running"]
                  else "none - the kernel's own scheduler is in charge", sec,
                  running=st["running"], mode=st["mode"])
        verdict, why = _pkcheck("org.scx.loader.manage-schedulers")
        self._add("scx_polkit", "scx_loader authorization",
                  {"yes": PASS, "prompt": INFO, "missing": FAIL,
                   "unknown": SKIP}.get(verdict, FAIL),
                  why + (" - switching a scheduler is system-wide, so this "
                         "prompting is correct" if verdict == "prompt" else ""),
                  sec, verdict=verdict)

    def probe_modprobe_d(self) -> None:
        sec = "Kernel tunables"
        d = Path("/etc/modprobe.d")
        if not d.is_dir():
            self._add("modprobe_d", "/etc/modprobe.d", FAIL,
                      "missing - SetNvidiaModeset writes here and cannot work", sec)
            return
        existing = sorted(p.name for p in d.glob("*goblin*"))
        self._add("modprobe_d", "/etc/modprobe.d", INFO,
                  "present; the helper's sandbox grants it via ReadWritePaths"
                  + (f"; our files: {', '.join(existing)}" if existing
                     else "; no goblin file written yet"), sec,
                  files=existing)

    # -- the whole run -----------------------------------------------
    #: (attribute, human name) in run order
    PROBES = (
        ("probe_helper", "Helper and authorization"),
        ("probe_capabilities", "Helper capabilities"),
        ("probe_governor", "CPU governor"),
        ("probe_epp", "EPP"),
        ("probe_rapl", "RAPL"),
        ("probe_ryzenadj", "AMD TDP"),
        ("probe_fans", "Fan control"),
        ("probe_sysctls", "Kernel tunables"),
        ("probe_sched_ext", "sched_ext"),
        ("probe_modprobe_d", "/etc/modprobe.d"),
    )

    def run(self) -> list[Result]:
        """Run every probe. One blowing up is itself a result, not an abort.

        This is a diagnostic tool: it runs on machines where things are broken,
        so a probe that raises has to be reported and stepped over. Losing the
        remaining probes - and dumping a traceback at someone already having a
        bad day - would defeat the point. Ctrl-C still stops everything.
        """
        for attr, title in self.PROBES:
            try:
                getattr(self, attr)()
            except KeyboardInterrupt:
                self._add("interrupted", "Run interrupted", FAIL,
                          f"stopped during {title}; anything already applied has "
                          "been reverted", "General")
                raise
            except Exception as exc:            # noqa: BLE001 - that's the job
                self._add(attr, title, FAIL,
                          f"the probe itself failed: {type(exc).__name__}: {exc}",
                          "General")
        return self.results


def _explain_call_failure(exc: BaseException, method: str) -> str:
    """Turn a helper call failure into something a person can act on."""
    text = str(exc)
    if "Timeout" in text:
        return (f"{method} timed out on the bus. This usually means a polkit "
                "password prompt appeared and wasn't answered - check for a "
                "dialog on your desktop, or run this from the session that "
                "owns the screen")
    if "AccessDenied" in text or "not authorized" in text.lower():
        return (f"{method} was refused by polkit - the action is installed but "
                "this session is not allowed to use it")
    return f"{method}: {type(exc).__name__}: {text}"


def _w(uw: int | None) -> str:
    return "unknown" if uw is None else f"{uw / 1_000_000:.1f} W"


def _ryzenadj_stapm(binary: str) -> float | None:
    _rc, out = _run([binary, "--info"], timeout=10.0)
    m = re.search(r"STAPM LIMIT\s*\|\s*([\d.]+)", out)
    return float(m.group(1)) if m else None


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------
def machine_summary() -> dict:
    """The identifying facts a verified-hardware row needs."""
    from goblinmode import capabilities
    caps = capabilities.detect()
    return {
        "cpu": caps.get("cpu_model") or "unknown",
        "cpu_vendor": caps.get("cpu_vendor"),
        "gpu": ", ".join(caps.get("gpu_vendors") or []) or "unknown",
        "distro": caps.get("distro_id"),
        "kernel": caps.get("kernel_release"),
        "cpufreq_driver": caps.get("cpufreq_driver"),
        "compositor": caps.get("compositor"),
        "handheld": caps.get("handheld"),
    }


def to_json(results: list[Result], apply: bool) -> dict:
    from goblinmode.__about__ import __version__
    counts: dict[str, int] = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    return {
        "version": __version__,
        "mode": "apply" if apply else "read-only",
        "machine": machine_summary(),
        "summary": counts,
        "results": [r.as_dict() for r in results],
    }


_STATUS_ORDER = (FAIL, SKIP, INFO, PASS)


def render(results: list[Result], apply: bool, color: bool = True) -> str:
    """A human-readable table, grouped by section, widest-first aligned."""
    mark = {PASS: "PASS", FAIL: "FAIL", SKIP: "SKIP", INFO: "info"}
    tint = {PASS: "\033[32m", FAIL: "\033[31m", SKIP: "\033[33m", INFO: "\033[36m"}
    reset = "\033[0m"
    m = machine_summary()
    out = [
        f"goblin-mode-pro selftest - {'apply (round-trip)' if apply else 'read-only'}",
        f"  {m['cpu']} / {m['gpu']} / {m['distro']} {m['kernel']}",
        "",
    ]
    width = max((len(r.title) for r in results), default=10)
    for section in dict.fromkeys(r.section for r in results):
        out.append(f"{section}")
        for r in (x for x in results if x.section == section):
            label = mark[r.status]
            if color:
                label = f"{tint[r.status]}{label}{reset}"
            out.append(f"  {label}  {r.title.ljust(width)}  {r.detail}")
        out.append("")
    counts = {s: sum(1 for r in results if r.status == s) for s in _STATUS_ORDER}
    out.append("  ".join(f"{mark[s]} {counts[s]}" for s in _STATUS_ORDER if counts[s]))
    if not apply:
        out += [
            "",
            "Read-only: nothing was changed, and nothing above proves a write",
            "path - these values are written by the root helper, so testing them",
            "as your user would say nothing either way. Run `selftest --apply` to",
            "round-trip each capability (apply, read back, revert, read back).",
        ]
    return "\n".join(out)


def run(apply: bool = False) -> tuple[list[Result], int]:
    """Run the suite. Exit code is 1 if anything FAILed, else 0.

    A SKIP is never an error: "this machine can't do that" is a valid,
    reportable outcome and is the expected answer for fan control on most
    laptops and for ryzenadj on every Intel machine.
    """
    st = SelfTest(apply=apply)
    results = st.run()
    return results, (1 if any(r.status == FAIL for r in results) else 0)
