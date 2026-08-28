"""Client wrapper around the root helper's system D-Bus service.

The daemon runs unprivileged; anything that needs root (CPU governor, EPP,
negative renice, RAPL power limits) is delegated to ``goblin-mode-pro-helper``
over the system bus. If the helper is not installed/running every call raises
:class:`HelperUnavailable` and the daemon degrades to "limited mode".
"""

from __future__ import annotations

import logging

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from goblinmode import HELPER_BUS_NAME, HELPER_IFACE, HELPER_OBJECT_PATH

log = logging.getLogger(__name__)

_TIMEOUT_MS = 5000


class HelperUnavailable(RuntimeError):
    """Raised when the privileged helper cannot be reached."""


class HelperClient:
    def __init__(self) -> None:
        self._proxy: Gio.DBusProxy | None = None

    # -- connection -----------------------------------------------------
    def _get_proxy(self) -> Gio.DBusProxy:
        if self._proxy is not None:
            return self._proxy
        try:
            self._proxy = Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SYSTEM,
                Gio.DBusProxyFlags.DO_NOT_AUTO_START,
                None,
                HELPER_BUS_NAME,
                HELPER_OBJECT_PATH,
                HELPER_IFACE,
                None,
            )
        except GLib.Error as exc:  # pragma: no cover - needs a bus
            raise HelperUnavailable(str(exc)) from exc
        # new_for_bus_sync succeeds even if the name has no owner; probe it.
        if self._proxy.get_name_owner() is None:
            self._proxy = None
            raise HelperUnavailable(f"{HELPER_BUS_NAME} has no owner")
        return self._proxy

    def available(self) -> bool:
        try:
            self._get_proxy()
            return True
        except HelperUnavailable:
            return False

    def _call(self, method: str, params: GLib.Variant | None = None):
        proxy = self._get_proxy()
        try:
            return proxy.call_sync(
                method, params, Gio.DBusCallFlags.NONE, _TIMEOUT_MS, None
            )
        except GLib.Error as exc:
            raise HelperUnavailable(f"{method}: {exc}") from exc

    # -- CPU governor / EPP -------------------------------------------------
    def get_governor(self) -> str:
        return self._call("GetGovernor").unpack()[0]

    def set_governor(self, governor: str) -> bool:
        return self._call(
            "SetGovernor", GLib.Variant("(s)", (governor,))
        ).unpack()[0]

    def set_epp(self, epp: str) -> bool:
        return self._call("SetEPP", GLib.Variant("(s)", (epp,))).unpack()[0]

    # -- priority ---------------------------------------------------------
    def renice(self, pid: int, nice: int) -> bool:
        return self._call(
            "Renice", GLib.Variant("(ui)", (pid, nice))
        ).unpack()[0]

    # -- RAPL power limits ----------------------------------------------
    def get_power_limits(self) -> tuple[int, int]:
        pl1, pl2 = self._call("GetPowerLimits").unpack()
        return int(pl1), int(pl2)

    def set_power_limits(self, pl1_uw: int, pl2_uw: int) -> bool:
        return self._call(
            "SetPowerLimits", GLib.Variant("(tt)", (pl1_uw, pl2_uw))
        ).unpack()[0]

    def reset_power_limits(self) -> bool:
        return self._call("ResetPowerLimits").unpack()[0]

    # -- global revert --------------------------------------------------
    def revert_all(self) -> bool:
        return self._call("RevertAll").unpack()[0]

    # -- pre-flight sysctl fixes --------------------------------------
    def set_sysctl(self, key: str, value: str) -> bool:
        return self._call(
            "SetSysctl", GLib.Variant("(ss)", (key, str(value)))
        ).unpack()[0]
