"""sched_ext scheduler control, via ``scx_loader`` on the system bus.

The one capability a CachyOS user already has that Goblin didn't: swapping the
kernel's CPU scheduler for a ``sched_ext`` BPF one (``scx_lavd``,
``scx_bpfland``, …) while a game runs, and putting it back afterwards.

**No new privileged code.** ``scx_loader`` is a root D-Bus service whose bus
policy explicitly allows *any* user to call ``StartScheduler`` /
``SwitchScheduler`` / ``StopScheduler``, delegating authorization to polkit
(``org.scx.loader.manage-schedulers``). So the unprivileged daemon talks to it
directly and Goblin's own helper - the only privileged code in this project -
does not grow a single method. That action is ``auth_admin_keep``, so the
first switch in a session prompts for a password and later ones don't.

Reverting is state-driven, like the rest of the payload: the scheduler that was
running before the first game started is recorded, and restored when the last
one exits. If none was running, we stop ours rather than guessing a default.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

log = logging.getLogger(__name__)

BUS_NAME = "org.scx.Loader"
OBJECT_PATH = "/org/scx/Loader"
IFACE = "org.scx.Loader"

#: kernel support: present whenever CONFIG_SCHED_CLASS_EXT is built in
SCHED_EXT_SYSFS = Path("/sys/kernel/sched_ext")

#: ``scx_loader``'s SchedMode enum, by name. Only "auto" (the restore default)
#: and "gaming" are used unless a profile asks for something else, and those
#: two are the unambiguous ends of the enum. The scheduler *name* is the
#: substantive setting and is verified by reading CurrentScheduler back after
#: a switch; the mode is a tuning variant of that scheduler, so a wrong value
#: here would pick a different tuning of the right scheduler rather than fail.
#: `state()` reports the mode actually in effect, so any drift is visible in
#: `goblin-mode-pro-cli selftest` rather than silent.
SCHED_MODES: dict[str, int] = {
    "auto": 0,
    "gaming": 1,
    "lowlatency": 2,
    "powersave": 3,
    "server": 4,
}
DEFAULT_MODE = "gaming"

#: How long to wait on a scx_loader call. Loading a BPF scheduler compiles and
#: attaches it, which is not instant, and the first call of a session may be
#: sitting behind a polkit password prompt.
_CALL_TIMEOUT_MS = 45_000


class ScxUnavailable(Exception):
    """scx_loader isn't reachable, or refused the call."""


def kernel_supports_sched_ext() -> bool:
    return SCHED_EXT_SYSFS.is_dir()


def loader_installed() -> bool:
    return shutil.which("scx_loader") is not None


def scheduler_binaries() -> list[str]:
    """``scx_*`` schedulers present on disk, short names, sorted.

    Used for capability reporting when scx_loader isn't running yet - asking
    the loader would D-Bus-activate it, which we don't want to do from a
    capability probe.
    """
    names = set()
    for d in ("/usr/bin", "/usr/local/bin"):
        for p in Path(d).glob("scx_*"):
            if p.name != "scx_loader" and p.is_file():
                names.add(p.name.removeprefix("scx_"))
    return sorted(names)


class ScxManager:
    """Unprivileged client for scx_loader. All calls are best-effort."""

    def __init__(self) -> None:
        self._proxy: Gio.DBusProxy | None = None

    # -- plumbing ----------------------------------------------------
    def _get_proxy(self) -> Gio.DBusProxy:
        if self._proxy is None:
            try:
                self._proxy = Gio.DBusProxy.new_for_bus_sync(
                    Gio.BusType.SYSTEM,
                    # DO_NOT_AUTO_START: a capability probe must never
                    # D-Bus-activate a root service as a side effect. We only
                    # start it when actually switching (see _switch_proxy).
                    Gio.DBusProxyFlags.DO_NOT_AUTO_START,
                    None, BUS_NAME, OBJECT_PATH, IFACE, None,
                )
            except GLib.Error as exc:
                raise ScxUnavailable(str(exc)) from exc
        return self._proxy

    def _switch_proxy(self) -> Gio.DBusProxy:
        """A proxy that *may* activate the loader - for calls that change state."""
        try:
            return Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SYSTEM, Gio.DBusProxyFlags.NONE,
                None, BUS_NAME, OBJECT_PATH, IFACE, None,
            )
        except GLib.Error as exc:
            raise ScxUnavailable(str(exc)) from exc

    def _prop(self, proxy: Gio.DBusProxy, name: str):
        v = proxy.get_cached_property(name)
        return v.unpack() if v is not None else None

    def available(self) -> bool:
        """True when a scheduler could actually be switched on this machine.

        Deliberately does not activate scx_loader: it checks the kernel has
        sched_ext and the loader is installed. Whether the *service* answers is
        a separate question, reported by `state()`.
        """
        return kernel_supports_sched_ext() and loader_installed()

    # -- reading -----------------------------------------------------
    def state(self) -> dict:
        """What sched_ext is doing right now. Never raises."""
        out: dict = {
            "kernel": kernel_supports_sched_ext(),
            "loader": loader_installed(),
            "running": None,
            "mode": None,
            "supported": scheduler_binaries(),
            "loader_running": False,
        }
        try:
            kstate = (SCHED_EXT_SYSFS / "state").read_text().strip()
            out["kernel_state"] = kstate
        except OSError:
            out["kernel_state"] = None
        if not out["loader"]:
            return out
        try:
            proxy = self._get_proxy()
            if proxy.get_name_owner() is None:
                return out                       # loader installed, not running
            out["loader_running"] = True
            current = self._prop(proxy, "CurrentScheduler")
            # scx_loader reports "unknown" rather than empty when idle
            out["running"] = None if current in (None, "", "unknown") else current
            out["mode"] = self._prop(proxy, "SchedulerMode")
            supported = self._prop(proxy, "SupportedSchedulers")
            if supported:
                out["supported"] = sorted(
                    s.removeprefix("scx_") for s in supported)
        except (ScxUnavailable, GLib.Error) as exc:
            log.debug("scx_loader state unavailable: %s", exc)
        return out

    def current(self) -> str | None:
        """The running scheduler's short name, or None."""
        return self.state().get("running")

    # -- writing -----------------------------------------------------
    def switch(self, scheduler: str, mode: str = DEFAULT_MODE) -> bool:
        """Switch to `scheduler` (short name, e.g. "lavd"). Returns success.

        Uses SwitchScheduler, which handles both "nothing running" and
        "something else running" - so we don't have to branch on current state
        and race with anything that changed it underneath us.
        """
        short = scheduler.removeprefix("scx_")
        name = f"scx_{short}"
        mode_id = SCHED_MODES.get(mode, SCHED_MODES[DEFAULT_MODE])
        # Config validates the *shape* of the name; this is where we find out
        # whether this machine actually has it. Checking first turns "a profile
        # names a scheduler you haven't installed" into a clear log line rather
        # than a D-Bus error from a root service.
        supported = self.state().get("supported") or []
        if supported and short not in supported:
            log.warning("sched_ext: %s is not available here (have: %s)",
                        name, ", ".join(supported) or "none")
            return False
        try:
            proxy = self._switch_proxy()
            proxy.call_sync(
                "SwitchScheduler", GLib.Variant("(su)", (name, mode_id)),
                Gio.DBusCallFlags.NONE, _CALL_TIMEOUT_MS, None,
            )
        except (ScxUnavailable, GLib.Error) as exc:
            log.warning("could not switch to %s: %s", name, exc)
            return False
        # Confirm it actually took. The scheduler name is the part that matters,
        # and scx_loader accepting the call is not the same as the BPF program
        # attaching - a scheduler the kernel rejects leaves us on the old one.
        got = self.current()
        if got and got.removeprefix("scx_") == name.removeprefix("scx_"):
            log.info("sched_ext: switched to %s (%s mode)", name, mode)
            return True
        log.warning("sched_ext: asked for %s but %s is running", name,
                    got or "no scheduler")
        return False

    def stop(self) -> bool:
        """Stop the running sched_ext scheduler (back to the kernel default)."""
        try:
            proxy = self._get_proxy()
            if proxy.get_name_owner() is None:
                return True                      # nothing running, nothing to do
            proxy.call_sync("StopScheduler", None, Gio.DBusCallFlags.NONE,
                            _CALL_TIMEOUT_MS, None)
        except (ScxUnavailable, GLib.Error) as exc:
            log.warning("could not stop the sched_ext scheduler: %s", exc)
            return False
        log.info("sched_ext: stopped, back to the kernel's own scheduler")
        return True

    def restore(self, previous: str | None, mode: str = DEFAULT_MODE) -> bool:
        """Put back what was running before we touched it.

        `previous` None means "nothing was running", which restores by stopping
        rather than by guessing a default - the machine's own configured
        default may differ from ours, and RestoreDefault would impose it.
        """
        if previous:
            return self.switch(previous, mode)
        return self.stop()
