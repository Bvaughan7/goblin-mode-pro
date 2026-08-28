"""Diagnostics page - incident log, correlation graph, LLM export."""

from __future__ import annotations

import json
import logging
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from goblinmode.gui.widgets.graph import CorrelationGraph, FpsGraph
from goblinmode.ipc.daemon_bridge import BridgeClient

log = logging.getLogger(__name__)

_KIND_LABEL = {
    "thermal_throttle": "Thermal throttle",
    "power_limit": "Power limit (PL1)",
    "gpu_throttle": "GPU throttle",
    "gpu_fault": "GPU / driver fault",
    "fps_dip": "Frame-rate cliff",
    "fps_recovered": "Frame rate recovered",
    "vram_not_freed": "VRAM not released after exit",
    "helper_unavailable": "Helper unavailable",
    "payload_error": "Payload error",
}


class DiagnosticsPage(Adw.PreferencesPage):
    def __init__(self, bridge: BridgeClient, window) -> None:
        super().__init__(title="Diagnostics", icon_name="dialog-warning-symbolic")
        self.bridge = bridge
        self._window = window

        graph_group = Adw.PreferencesGroup(
            title="Temperature vs load",
            description="CPU temp (red), GPU temp (orange), CPU load (blue). Vertical marks = throttle events.",
        )
        self.graph = CorrelationGraph()
        frame = Gtk.Frame()
        frame.set_child(self.graph)
        graph_group.add(frame)
        self.add(graph_group)

        fps_group = Adw.PreferencesGroup(
            title="Frame rate",
            description="From the MangoHud watchdog log. The dashed line is the dip threshold.",
        )
        self.fps_graph = FpsGraph()
        fps_frame = Gtk.Frame()
        fps_frame.set_child(self.fps_graph)
        fps_group.add(fps_frame)
        self.add(fps_group)

        export_group = Adw.PreferencesGroup(
            description="Package the current state for an LLM, a forum thread, or a bug tracker.",
        )
        self._export_row = Adw.ButtonRow(title="Export last incident for AI")
        self._export_row.set_start_icon_name("edit-copy-symbolic")
        self._export_row.connect("activated", self._on_export)
        export_group.add(self._export_row)
        self._report_row = Adw.ButtonRow(title="Build a bug report")
        self._report_row.set_start_icon_name("dialog-question-symbolic")
        self._report_row.connect("activated", self._on_report)
        export_group.add(self._report_row)
        self._analyze_row = Adw.ButtonRow(title="Analyze the Proton log")
        self._analyze_row.set_start_icon_name("system-search-symbolic")
        self._analyze_row.connect("activated", self._on_analyze)
        export_group.add(self._analyze_row)
        self.add(export_group)

        self._log_group = Adw.PreferencesGroup(title="Incident log")
        self.add(self._log_group)
        self._log_rows: list[Gtk.Widget] = []
        self._empty_row: Adw.ActionRow | None = None
        self._set_empty()

    # -- incident list --------------------------------------------
    def _set_empty(self) -> None:
        if self._empty_row is None:
            self._empty_row = Adw.ActionRow(
                title="No incidents recorded",
                subtitle="Throttling and GPU faults during gameplay show up here",
            )
            self._log_group.add(self._empty_row)

    def _clear_empty(self) -> None:
        if self._empty_row is not None:
            self._log_group.remove(self._empty_row)
            self._empty_row = None

    def load_history(self, metrics: list[dict], incidents: list[dict]) -> None:
        self.graph.load_history(metrics)
        for inc in incidents[-50:]:
            self.add_incident(inc, persistically=False)

    def add_incident(self, incident: dict[str, Any], persistically: bool = True) -> None:
        self._clear_empty()
        if incident.get("fps_trace"):
            self.fps_graph.load_history(incident["fps_trace"])
        kind = incident.get("kind", "unknown")
        title = _KIND_LABEL.get(kind, kind)
        ts = incident.get("ts", "")
        row = Adw.ExpanderRow(
            title=title,
            subtitle=f"{ts}  ·  {incident.get('detail', '')}",
        )
        detail = Gtk.TextView(editable=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        detail.get_buffer().set_text(json.dumps(incident, indent=2))
        sc = Gtk.ScrolledWindow(min_content_height=160, max_content_height=280)
        sc.set_child(detail)
        wrapper = Adw.ActionRow()
        wrapper.set_child(sc)
        row.add_row(wrapper)

        self._log_group.add(row)
        self._log_rows.insert(0, row)
        # keep newest near the top by removing/re-adding is expensive; cap count
        if len(self._log_rows) > 50:
            old = self._log_rows.pop()
            self._log_group.remove(old)

    def push_sample(self, sample: dict[str, Any]) -> None:
        self.graph.push(sample)
        if sample.get("fps") is not None:
            self.fps_graph.push(sample["fps"])

    def update_status(self, status: dict[str, Any]) -> None:
        profiles = status.get("profiles") or []
        floors = [p.get("fps_dip_floor", 22) for p in profiles if p.get("fps_watchdog")]
        if floors:
            self.fps_graph.set_threshold(max(floors))

    # -- export ---------------------------------------------------
    def _on_export(self, _row) -> None:
        try:
            payload = self.bridge.export_last_incident()
        except Exception as exc:  # noqa: BLE001
            log.warning("export failed: %s", exc)
            self._toast("Export failed")
            return
        if not payload:
            self._toast("No incident to export yet")
            return
        # Clipboard from the GUI as a reliable fallback to the daemon's wl-copy.
        display = Gdk.Display.get_default()
        if display is not None:
            display.get_clipboard().set(payload)
        self._toast("Incident payload copied to clipboard")

    def _on_report(self, _row) -> None:
        dialog = Adw.MessageDialog(
            transient_for=self._window,
            heading="Build a bug report",
            body="Describe the problem in a sentence (optional). GMP gathers system "
            "info, the pre-flight results, the last incident and the Proton log "
            "analysis, copies a Markdown report to your clipboard, and opens a "
            "pre-filled issue form.",
        )
        entry = Gtk.Entry(placeholder_text="e.g. crashes on the loading screen")
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("build", "Build report")
        dialog.set_response_appearance("build", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect("response", self._on_report_response, entry)
        dialog.present()

    def _on_report_response(self, _d, response: str, entry) -> None:
        if response != "build":
            return
        try:
            md = self.bridge.build_report(entry.get_text())
        except Exception as exc:  # noqa: BLE001
            self._toast(f"Report failed: {exc}")
            return
        disp = Gdk.Display.get_default()
        if disp is not None:
            disp.get_clipboard().set(md)
        self._toast("Bug report copied — paste it into a forum thread or issue")

    def _on_analyze(self, _row) -> None:
        try:
            findings = self.bridge.analyze_log()
        except Exception as exc:  # noqa: BLE001
            self._toast(f"Analyze failed: {exc}")
            return
        if not findings:
            self._toast("No captured Proton log, or no known issues in it")
            return
        body = "\n\n".join(
            f"• {f['label']}  ({f['category']}, ×{f['count']})\n{f['cause']}\nFix: {f['fix']}"
            for f in findings
        )
        d = Adw.MessageDialog(transient_for=self._window,
                              heading=f"{len(findings)} issue(s) in the Proton log", body=body)
        d.add_response("ok", "Close")
        d.present()

    def _toast(self, text: str) -> None:
        if hasattr(self._window, "toast"):
            self._window.toast(text)
        else:
            log.info("%s", text)
