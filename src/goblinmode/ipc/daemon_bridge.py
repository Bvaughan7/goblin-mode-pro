"""Session-bus bridge between the daemon and the on-demand GUI.

The daemon owns ``com.goblinmode.Pro.Daemon`` on the session bus and exposes a
small JSON-over-D-Bus API. The GUI is a pure client (:class:`BridgeClient`).
Complex values are passed as JSON strings to keep the interface trivial and
schema-free.

The bridge name is deliberately *not* ``com.goblinmode.Pro`` - that belongs to
the GUI's ``Adw.Application`` registration, and sharing it makes GTK try to talk
``org.gtk.Application`` to the daemon.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, Callable, Protocol

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from goblinmode import BRIDGE_BUS_NAME, BRIDGE_OBJECT_PATH

log = logging.getLogger(__name__)

BUS_NAME = BRIDGE_BUS_NAME
OBJECT_PATH = BRIDGE_OBJECT_PATH
IFACE = "com.goblinmode.Pro.Daemon"

INTROSPECTION_XML = f"""
<node>
  <interface name="{IFACE}">
    <method name="GetStatus"><arg type="s" name="json" direction="out"/></method>
    <method name="GetMetrics"><arg type="s" name="json" direction="out"/></method>
    <method name="GetIncidents"><arg type="s" name="json" direction="out"/></method>
    <method name="SetProfile">
      <arg type="s" name="json" direction="in"/>
      <arg type="b" name="ok" direction="out"/>
    </method>
    <method name="RemoveProfile">
      <arg type="s" name="exe" direction="in"/>
      <arg type="b" name="ok" direction="out"/>
    </method>
    <method name="SetMasterEnabled">
      <arg type="b" name="enabled" direction="in"/>
      <arg type="b" name="ok" direction="out"/>
    </method>
    <method name="SetAutoDetect">
      <arg type="b" name="enabled" direction="in"/>
      <arg type="b" name="ok" direction="out"/>
    </method>
    <method name="ForceBoost">
      <arg type="b" name="on" direction="in"/>
      <arg type="b" name="ok" direction="out"/>
    </method>
    <method name="ExportLastIncident">
      <arg type="s" name="payload" direction="out"/>
    </method>
    <method name="WriteWrapper"><arg type="s" name="path" direction="out"/></method>
    <method name="IgnoreGame">
      <arg type="s" name="exe" direction="in"/><arg type="b" name="ok" direction="out"/>
    </method>
    <method name="KeepGame">
      <arg type="s" name="exe" direction="in"/><arg type="b" name="ok" direction="out"/>
    </method>
    <method name="RunPreflight"><arg type="s" name="json" direction="out"/></method>
    <method name="ApplyPreflightFixes"><arg type="s" name="json" direction="out"/></method>
    <method name="BuildReport">
      <arg type="s" name="note" direction="in"/><arg type="s" name="markdown" direction="out"/>
    </method>
    <method name="AnalyzeLog"><arg type="s" name="json" direction="out"/></method>
    <method name="GetSessions"><arg type="s" name="json" direction="out"/></method>
    <method name="GetSessionHistory">
      <arg type="s" name="exe" direction="in"/><arg type="s" name="json" direction="out"/>
    </method>
    <method name="GetHealth"><arg type="s" name="json" direction="out"/></method>
    <method name="ArmBenchmark">
      <arg type="s" name="exe" direction="in"/><arg type="b" name="ok" direction="out"/>
    </method>
    <method name="GetSystemInfo"><arg type="s" name="json" direction="out"/></method>
    <method name="GetProtonInfo"><arg type="s" name="json" direction="out"/></method>
    <method name="ClearShaderCache">
      <arg type="s" name="path" direction="in"/><arg type="s" name="json" direction="out"/>
    </method>
    <method name="ExportSetup"><arg type="s" name="markdown" direction="out"/></method>
    <signal name="StatusChanged"><arg type="s" name="json"/></signal>
    <signal name="MetricsUpdated"><arg type="s" name="json"/></signal>
    <signal name="IncidentLogged"><arg type="s" name="json"/></signal>
    <signal name="GameDetected"><arg type="s" name="json"/></signal>
    <signal name="SessionLogged"><arg type="s" name="json"/></signal>
  </interface>
</node>
"""


class DaemonHandler(Protocol):
    def get_status(self) -> dict[str, Any]: ...
    def get_metrics(self) -> list[dict[str, Any]]: ...
    def get_incidents(self) -> list[dict[str, Any]]: ...
    def set_profile(self, profile: dict[str, Any]) -> bool: ...
    def remove_profile(self, exe: str) -> bool: ...
    def set_master_enabled(self, enabled: bool) -> bool: ...
    def set_auto_detect(self, enabled: bool) -> bool: ...
    def force_boost(self, on: bool) -> bool: ...
    def export_last_incident(self) -> str: ...
    def write_wrapper(self) -> str: ...
    def ignore_game(self, exe: str) -> bool: ...
    def keep_game(self, exe: str) -> bool: ...
    def run_preflight(self) -> list[dict[str, Any]]: ...
    def apply_preflight_fixes(self) -> dict[str, Any]: ...
    def build_report(self, note: str) -> str: ...
    def analyze_log(self) -> list[dict[str, Any]]: ...
    def get_sessions(self) -> list[dict[str, Any]]: ...
    def get_session_history(self, exe: str) -> list[dict[str, Any]]: ...
    def get_health(self) -> dict[str, Any]: ...
    def arm_benchmark(self, exe: str) -> bool: ...
    def get_system_info(self) -> dict[str, Any]: ...
    def get_proton_info(self) -> dict[str, Any]: ...
    def clear_shader_cache(self, path: str) -> dict[str, Any]: ...
    def export_setup(self) -> str: ...


# --------------------------------------------------------------------------
# Daemon side
# --------------------------------------------------------------------------
class DaemonBridge:
    def __init__(self, handler: DaemonHandler) -> None:
        self._handler = handler
        self._conn: Gio.DBusConnection | None = None
        self._reg_id = 0
        self._owner_id = 0

    def publish(self) -> None:
        self._owner_id = Gio.bus_own_name(
            Gio.BusType.SESSION,
            BUS_NAME,
            Gio.BusNameOwnerFlags.DO_NOT_QUEUE,
            self._on_bus_acquired,
            None,
            lambda *_: log.warning(
                "could not own %s - another daemon instance is running", BUS_NAME
            ),
        )

    def _on_bus_acquired(self, conn: Gio.DBusConnection, name: str) -> None:
        self._conn = conn
        node = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION_XML)
        self._reg_id = conn.register_object(
            OBJECT_PATH, node.interfaces[0], self._handle_call, None, None
        )
        log.info("daemon bridge published on %s", name)

    def _handle_call(
        self, conn, sender, path, iface, method, params, invocation
    ) -> None:
        try:
            if method == "GetStatus":
                invocation.return_value(
                    GLib.Variant("(s)", (json.dumps(self._handler.get_status()),))
                )
            elif method == "GetMetrics":
                invocation.return_value(
                    GLib.Variant("(s)", (json.dumps(self._handler.get_metrics()),))
                )
            elif method == "GetIncidents":
                invocation.return_value(
                    GLib.Variant("(s)", (json.dumps(self._handler.get_incidents()),))
                )
            elif method == "SetProfile":
                ok = self._handler.set_profile(json.loads(params.unpack()[0]))
                invocation.return_value(GLib.Variant("(b)", (ok,)))
            elif method == "RemoveProfile":
                ok = self._handler.remove_profile(params.unpack()[0])
                invocation.return_value(GLib.Variant("(b)", (ok,)))
            elif method == "SetMasterEnabled":
                ok = self._handler.set_master_enabled(bool(params.unpack()[0]))
                invocation.return_value(GLib.Variant("(b)", (ok,)))
            elif method == "SetAutoDetect":
                ok = self._handler.set_auto_detect(bool(params.unpack()[0]))
                invocation.return_value(GLib.Variant("(b)", (ok,)))
            elif method == "ForceBoost":
                ok = self._handler.force_boost(bool(params.unpack()[0]))
                invocation.return_value(GLib.Variant("(b)", (ok,)))
            elif method == "ExportLastIncident":
                invocation.return_value(
                    GLib.Variant("(s)", (self._handler.export_last_incident(),))
                )
            elif method == "WriteWrapper":
                invocation.return_value(
                    GLib.Variant("(s)", (self._handler.write_wrapper(),))
                )
            elif method == "IgnoreGame":
                invocation.return_value(
                    GLib.Variant("(b)", (self._handler.ignore_game(params.unpack()[0]),))
                )
            elif method == "KeepGame":
                invocation.return_value(
                    GLib.Variant("(b)", (self._handler.keep_game(params.unpack()[0]),))
                )
            elif method == "RunPreflight":
                self._async_str(invocation, lambda: json.dumps(self._handler.run_preflight()))
            elif method == "ApplyPreflightFixes":
                self._async_str(invocation, lambda: json.dumps(self._handler.apply_preflight_fixes()))
            elif method == "BuildReport":
                note = params.unpack()[0]
                self._async_str(invocation, lambda: self._handler.build_report(note))
            elif method == "AnalyzeLog":
                self._async_str(invocation, lambda: json.dumps(self._handler.analyze_log()))
            elif method == "GetSessions":
                invocation.return_value(
                    GLib.Variant("(s)", (json.dumps(self._handler.get_sessions()),))
                )
            elif method == "GetSessionHistory":
                exe = params.unpack()[0]
                invocation.return_value(
                    GLib.Variant("(s)", (json.dumps(self._handler.get_session_history(exe)),))
                )
            elif method == "GetHealth":
                invocation.return_value(
                    GLib.Variant("(s)", (json.dumps(self._handler.get_health()),))
                )
            elif method == "ArmBenchmark":
                invocation.return_value(
                    GLib.Variant("(b)", (self._handler.arm_benchmark(params.unpack()[0]),))
                )
            elif method == "GetSystemInfo":
                self._async_str(invocation, lambda: json.dumps(self._handler.get_system_info()))
            elif method == "GetProtonInfo":
                self._async_str(invocation, lambda: json.dumps(self._handler.get_proton_info()))
            elif method == "ClearShaderCache":
                p = params.unpack()[0]
                self._async_str(invocation, lambda: json.dumps(self._handler.clear_shader_cache(p)))
            elif method == "ExportSetup":
                self._async_str(invocation, lambda: self._handler.export_setup())
            else:
                invocation.return_dbus_error(
                    "org.freedesktop.DBus.Error.UnknownMethod", method
                )
        except Exception as exc:  # noqa: BLE001
            log.exception("bridge method %s failed", method)
            invocation.return_dbus_error(f"{IFACE}.Failed", str(exc))

    def _async_str(self, invocation, work: Callable[[], str]) -> None:
        """Run a slow handler off the main loop so the daemon stays responsive;
        reply with its ``(s)`` result (or a D-Bus error) from the main context."""
        def reply_ok(result: str) -> bool:
            invocation.return_value(GLib.Variant("(s)", (result,)))
            return GLib.SOURCE_REMOVE

        def reply_err(message: str) -> bool:
            invocation.return_dbus_error(f"{IFACE}.Failed", message)
            return GLib.SOURCE_REMOVE

        def run() -> None:
            try:
                result = work()
                GLib.idle_add(reply_ok, result)
            except Exception as exc:  # noqa: BLE001
                log.exception("async bridge handler failed")
                GLib.idle_add(reply_err, str(exc))

        threading.Thread(target=run, name="gmp-bridge-work", daemon=True).start()

    # -- signal emitters ---------------------------------------------------
    def _emit(self, signal: str, payload: Any) -> None:
        if self._conn is None:
            return
        self._conn.emit_signal(
            None, OBJECT_PATH, IFACE, signal,
            GLib.Variant("(s)", (json.dumps(payload),)),
        )

    def emit_status(self, status: dict) -> None:
        self._emit("StatusChanged", status)

    def emit_metrics(self, sample: dict) -> None:
        self._emit("MetricsUpdated", sample)

    def emit_incident(self, incident: dict) -> None:
        self._emit("IncidentLogged", incident)

    def emit_detected(self, game: dict) -> None:
        self._emit("GameDetected", game)

    def emit_session(self, payload: dict) -> None:
        self._emit("SessionLogged", payload)


# --------------------------------------------------------------------------
# GUI side
# --------------------------------------------------------------------------
class BridgeClient:
    def __init__(self) -> None:
        self._proxy: Gio.DBusProxy | None = None
        self._sig_handlers: list[Callable] = []

    def connect(self) -> bool:
        try:
            self._proxy = Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SESSION,
                Gio.DBusProxyFlags.DO_NOT_AUTO_START,
                None,
                BUS_NAME,
                OBJECT_PATH,
                IFACE,
                None,
            )
        except GLib.Error as exc:
            log.warning("bridge connect failed: %s", exc)
            return False
        if self._proxy.get_name_owner() is None:
            return False
        self._proxy.connect("g-signal", self._on_signal)
        return True

    @property
    def available(self) -> bool:
        return self._proxy is not None and self._proxy.get_name_owner() is not None

    def _on_signal(self, _proxy, _sender, signal_name, params) -> None:
        try:
            payload = json.loads(params.unpack()[0])
        except (json.JSONDecodeError, IndexError):
            payload = None
        for cb in self._sig_handlers:
            cb(signal_name, payload)

    def on_signal(self, callback: Callable[[str, Any], None]) -> None:
        self._sig_handlers.append(callback)

    def _call(self, method: str, variant: GLib.Variant | None = None):
        if self._proxy is None:
            raise RuntimeError("bridge not connected")
        return self._proxy.call_sync(
            method, variant, Gio.DBusCallFlags.NONE, 5000, None
        ).unpack()

    def _call_async(self, method, variant, on_done, timeout_ms=30000):
        """Call *method* without blocking the GUI loop. ``on_done(result, error)``
        fires on the main context; ``result`` is the unpacked tuple or None."""
        if self._proxy is None:
            on_done(None, RuntimeError("bridge not connected"))
            return

        def _finish(proxy, res):
            try:
                on_done(proxy.call_finish(res).unpack(), None)
            except GLib.Error as exc:  # pragma: no cover - needs a live bus
                on_done(None, exc)

        self._proxy.call(method, variant, Gio.DBusCallFlags.NONE, timeout_ms, None, _finish)

    # -- typed wrappers ------------------------------------------------
    def get_status(self) -> dict:
        return json.loads(self._call("GetStatus")[0])

    def get_metrics(self) -> list[dict]:
        return json.loads(self._call("GetMetrics")[0])

    def get_incidents(self) -> list[dict]:
        return json.loads(self._call("GetIncidents")[0])

    def set_profile(self, profile: dict) -> bool:
        return bool(self._call("SetProfile", GLib.Variant("(s)", (json.dumps(profile),)))[0])

    def remove_profile(self, exe: str) -> bool:
        return bool(self._call("RemoveProfile", GLib.Variant("(s)", (exe,)))[0])

    def set_master_enabled(self, enabled: bool) -> bool:
        return bool(self._call("SetMasterEnabled", GLib.Variant("(b)", (enabled,)))[0])

    def set_auto_detect(self, enabled: bool) -> bool:
        return bool(self._call("SetAutoDetect", GLib.Variant("(b)", (enabled,)))[0])

    def force_boost(self, on: bool) -> bool:
        return bool(self._call("ForceBoost", GLib.Variant("(b)", (on,)))[0])

    def export_last_incident(self) -> str:
        return self._call("ExportLastIncident")[0]

    def write_wrapper(self) -> str:
        return self._call("WriteWrapper")[0]

    def ignore_game(self, exe: str) -> bool:
        return bool(self._call("IgnoreGame", GLib.Variant("(s)", (exe,)))[0])

    def keep_game(self, exe: str) -> bool:
        return bool(self._call("KeepGame", GLib.Variant("(s)", (exe,)))[0])

    def run_preflight(self) -> list[dict]:
        return json.loads(self._call("RunPreflight")[0])

    def run_preflight_async(self, on_done: Callable[[list | None, object], None]) -> None:
        self._call_async("RunPreflight", None, lambda out, err: on_done(
            json.loads(out[0]) if out else None, err))

    def apply_preflight_fixes(self) -> dict:
        return json.loads(self._call("ApplyPreflightFixes")[0])

    def apply_preflight_fixes_async(self, on_done: Callable[[dict | None, object], None]) -> None:
        self._call_async("ApplyPreflightFixes", None, lambda out, err: on_done(
            json.loads(out[0]) if out else None, err))

    def build_report(self, note: str = "") -> str:
        return self._call("BuildReport", GLib.Variant("(s)", (note,)))[0]

    def build_report_async(self, note: str, on_done: Callable[[str | None, object], None]) -> None:
        self._call_async("BuildReport", GLib.Variant("(s)", (note,)),
                         lambda out, err: on_done(out[0] if out else None, err))

    def analyze_log_async(self, on_done: Callable[[list | None, object], None]) -> None:
        self._call_async("AnalyzeLog", None, lambda out, err: on_done(
            json.loads(out[0]) if out else None, err))

    def analyze_log(self) -> list[dict]:
        return json.loads(self._call("AnalyzeLog")[0])

    def get_sessions(self) -> list[dict]:
        return json.loads(self._call("GetSessions")[0])

    def get_session_history(self, exe: str) -> list[dict]:
        return json.loads(self._call("GetSessionHistory", GLib.Variant("(s)", (exe,)))[0])

    # -- async reads (never block the GTK main loop) ----------------
    def get_status_async(self, on_done: Callable[[dict | None, object], None]) -> None:
        self._call_async("GetStatus", None, lambda out, err: on_done(
            json.loads(out[0]) if out else None, err), timeout_ms=8000)

    def get_metrics_async(self, on_done: Callable[[list | None, object], None]) -> None:
        self._call_async("GetMetrics", None, lambda out, err: on_done(
            json.loads(out[0]) if out else None, err), timeout_ms=8000)

    def get_incidents_async(self, on_done: Callable[[list | None, object], None]) -> None:
        self._call_async("GetIncidents", None, lambda out, err: on_done(
            json.loads(out[0]) if out else None, err), timeout_ms=8000)

    def get_sessions_async(self, on_done: Callable[[list | None, object], None]) -> None:
        self._call_async("GetSessions", None, lambda out, err: on_done(
            json.loads(out[0]) if out else None, err), timeout_ms=8000)

    # -- roadmap additions ----------------------------------------------
    def get_health(self) -> dict:
        return json.loads(self._call("GetHealth")[0])

    def get_health_async(self, on_done: Callable[[dict | None, object], None]) -> None:
        self._call_async("GetHealth", None, lambda out, err: on_done(
            json.loads(out[0]) if out else None, err), timeout_ms=8000)

    def arm_benchmark(self, exe: str) -> bool:
        return bool(self._call("ArmBenchmark", GLib.Variant("(s)", (exe,)))[0])

    def get_system_info_async(self, on_done: Callable[[dict | None, object], None]) -> None:
        self._call_async("GetSystemInfo", None, lambda out, err: on_done(
            json.loads(out[0]) if out else None, err))

    def get_proton_info_async(self, on_done: Callable[[dict | None, object], None]) -> None:
        self._call_async("GetProtonInfo", None, lambda out, err: on_done(
            json.loads(out[0]) if out else None, err))

    def clear_shader_cache_async(self, path: str,
                                 on_done: Callable[[dict | None, object], None]) -> None:
        self._call_async("ClearShaderCache", GLib.Variant("(s)", (path,)),
                         lambda out, err: on_done(json.loads(out[0]) if out else None, err))

    def export_setup_async(self, on_done: Callable[[str | None, object], None]) -> None:
        self._call_async("ExportSetup", None,
                         lambda out, err: on_done(out[0] if out else None, err))
