"""Diagnostics page - incident log, correlation graph, LLM export."""

from __future__ import annotations

import json
import logging
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk  # noqa: E402

from goblinmode.i18n import _  # noqa: E402

from goblinmode.gui.widgets.buttonrow import button_row
from goblinmode.gui.widgets.graph import CorrelationGraph, FpsGraph
from goblinmode.ipc.daemon_bridge import BridgeClient

log = logging.getLogger(__name__)

_KIND_LABEL = {
    "thermal_throttle": _("Thermal throttle"),
    "power_limit": _("Power limit (PL1)"),
    "gpu_throttle": _("GPU throttle"),
    "gpu_fault": _("GPU / driver fault"),
    "fps_dip": _("Frame-rate cliff"),
    "fps_recovered": _("Frame rate recovered"),
    "vram_not_freed": _("VRAM not released after exit"),
    "helper_unavailable": _("Helper unavailable"),
    "payload_error": _("Payload error"),
}


class DiagnosticsPage(Adw.PreferencesPage):
    def __init__(self, bridge: BridgeClient, window) -> None:
        super().__init__(title=_("Diagnostics"), icon_name="dialog-warning-symbolic")
        self.bridge = bridge
        self._window = window

        graph_group = Adw.PreferencesGroup(
            title=_("Temperature vs load"),
            description=_("CPU temp (red), GPU temp (orange), CPU load (blue). Vertical marks = throttle events."),
        )
        self.graph = CorrelationGraph()
        frame = Gtk.Frame()
        frame.set_child(self.graph)
        graph_group.add(frame)
        self.add(graph_group)

        fps_group = Adw.PreferencesGroup(
            title=_("Frame rate"),
            description=_("From the MangoHud watchdog log. The dashed line is the dip threshold."),
        )
        self.fps_graph = FpsGraph()
        fps_frame = Gtk.Frame()
        fps_frame.set_child(self.fps_graph)
        fps_group.add(fps_frame)
        self.add(fps_group)

        bench_group = Adw.PreferencesGroup(
            title=_("Benchmark"),
            description=_("Arm a benchmark, play for a few minutes, and get a report "
            "card (avg / 1% / 0.1% low, frame-time stutter, thermal peaks) when you quit."),
        )
        self._bench_combo = Adw.ComboRow(title=_("Game to benchmark"))
        bench_group.add(self._bench_combo)
        self._bench_row = button_row(_("Arm benchmark for the selected game"), "stopwatch-symbolic")
        self._bench_row.connect("activated", self._on_arm_benchmark)
        bench_group.add(self._bench_row)
        self.add(bench_group)

        self._sessions_group = Adw.PreferencesGroup(
            title=_("Session history"),
            description=_("A summary per game session, from the MangoHud log. A big "
            "swing from your recent average is flagged."),
        )
        self.add(self._sessions_group)
        self._session_rows: list[Gtk.Widget] = []
        self._sessions_empty: Adw.ActionRow | None = None
        self._set_sessions_empty()

        self._proton_group = Adw.PreferencesGroup(
            title=_("Proton builds and shader caches"),
            description=_("Custom Proton/Wine builds you've installed, and how much "
            "disk the shader caches are using."),
        )
        self._proton_rows: list[Gtk.Widget] = []
        self.add(self._proton_group)

        export_group = Adw.PreferencesGroup(
            description=_("Package the current state for an LLM, a forum thread, or a bug tracker."),
        )
        self._export_row = button_row(_("Export last incident for AI"), "edit-copy-symbolic")
        self._export_row.connect("activated", self._on_export)
        export_group.add(self._export_row)
        self._report_row = button_row(_("Build a bug report"), "dialog-question-symbolic")
        self._report_row.connect("activated", self._on_report)
        export_group.add(self._report_row)
        self._setup_row = button_row(_("Export my full setup"), "document-save-symbolic")
        self._setup_row.connect("activated", self._on_export_setup)
        export_group.add(self._setup_row)
        self._analyze_row = button_row(_("Analyze the Proton log"), "system-search-symbolic")
        self._analyze_row.connect("activated", self._on_analyze)
        export_group.add(self._analyze_row)
        self._compare_row = button_row(_("Compare two sessions"), "view-refresh-symbolic")
        self._compare_row.connect("activated", self._on_compare)
        export_group.add(self._compare_row)
        self.add(export_group)

        self._log_group = Adw.PreferencesGroup(title=_("Incident log"))
        self.add(self._log_group)
        self._log_rows: list[Gtk.Widget] = []
        self._empty_row: Adw.ActionRow | None = None
        self._set_empty()

    # -- session history -----------------------------------------
    def _set_sessions_empty(self) -> None:
        if self._sessions_empty is None:
            self._sessions_empty = Adw.ActionRow(
                title=_("No sessions recorded yet"),
                subtitle=_("Enable the frame-rate watchdog or the overlay for a game, "
                "then play — each session is summarised here on exit."),
            )
            self._sessions_group.add(self._sessions_empty)

    def load_sessions(self, sessions: list[dict]) -> None:
        for row in self._session_rows:
            self._sessions_group.remove(row)
        self._session_rows.clear()
        try:
            from goblinmode.sessions import SessionSummary, _detect_regression
        except Exception:  # noqa: BLE001
            SessionSummary = _detect_regression = None
        per_game: dict[str, list[dict]] = {}
        prepared: list[dict] = []
        for s in sessions[-30:]:
            reg = None
            if _detect_regression is not None and s.get("fps_1low") is not None:
                prior = per_game.get(s.get("exe", ""), [])
                if prior:
                    try:
                        r = _detect_regression(SessionSummary(**{
                            k: s.get(k) for k in SessionSummary.__dataclass_fields__
                        }), prior)
                        reg = r.as_dict() if r else None
                    except Exception:  # noqa: BLE001
                        reg = None
            per_game.setdefault(s.get("exe", ""), []).append(s)
            prepared.append({"summary": s, "regression": reg})
        for payload in reversed(prepared):  # newest session on top
            self._add_session_row(payload, prepend=False)

    def add_session(self, payload: dict[str, Any]) -> None:
        self._add_session_row(payload, prepend=True)
        if payload.get("regression"):
            r = payload["regression"]
            game = payload.get("summary", {}).get("game", "a game")
            self._toast(
                f"{game}: {r['metric']} "
                f"{'down' if r['direction'] == 'regression' else 'up'} "
                f"{abs(r['change_pct']):.0f}% vs your recent average"
            )

    def _add_session_row(self, payload: dict[str, Any], prepend: bool) -> None:
        s = payload.get("summary") or {}
        reg = payload.get("regression")
        if self._sessions_empty is not None:
            self._sessions_group.remove(self._sessions_empty)
            self._sessions_empty = None

        mins = int(round((s.get("duration_s") or 0) / 60))
        bits = [s.get("started", "")[:10], f"{mins} min"]
        if s.get("fps_avg") is not None:
            bits.append(f"avg {s['fps_avg']:.0f} fps")
        if s.get("fps_1low") is not None:
            bits.append(f"1% low {s['fps_1low']:.0f}")
        row = Adw.ExpanderRow(title=s.get("game") or s.get("exe") or _("session"),
                              subtitle="  ·  ".join(b for b in bits if b))

        if s.get("benchmark"):
            b = Gtk.Label(label=_("BENCHMARK"))
            b.add_css_class("caption-heading")
            b.add_css_class("accent")
            b.set_valign(Gtk.Align.CENTER)
            row.add_suffix(b)

        if reg:
            worse = reg["direction"] == "regression"
            pill = Gtk.Label(label=f"{'▼' if worse else '▲'} {abs(reg['change_pct']):.0f}%")
            pill.add_css_class("caption-heading")
            pill.add_css_class("error" if worse else "success")
            pill.set_valign(Gtk.Align.CENTER)
            row.add_suffix(pill)

        lines = []
        fields = [("fps_avg", _("average")), ("fps_median", _("median")),
                  ("fps_1low", _("1% low")), ("fps_min", _("minimum"))]
        if s.get("benchmark"):
            fields[3:3] = [("fps_p95", _("95th %ile")), ("fps_01low", _("0.1% low"))]
        for k, lbl in fields:
            if s.get(k) is not None:
                lines.append(f"{lbl:>10}: {s[k]:.1f} fps")
        if s.get("frametime_stutter_pct") is not None:
            lines.append(f"{'stutter':>10}: {s['frametime_stutter_pct']:.1f}% of frames "
                         f"(>2× median frame time)")
        for k, lbl in (("cpu_temp_avg", _("CPU temp")), ("gpu_temp_avg", _("GPU temp"))):
            if s.get(k) is not None:
                mx = s.get(k.replace("_avg", "_max"))
                extra = f" (peak {mx:.0f})" if mx is not None else ""
                lines.append(f"{lbl:>10}: {s[k]:.0f} °C avg{extra}")
        if s.get("tweaks"):
            lines.append(f"{'tweaks':>9}: " + ", ".join(s["tweaks"]))
        if s.get("kernel"):
            lines.append(f"{'kernel':>9}: {s['kernel']}")
        if reg:
            lines.append("")
            lines.append(
                f"{reg['metric']} {reg['current']:.0f} fps vs a recent baseline of "
                f"{reg['baseline']:.0f} fps ({reg['sessions_compared']} sessions)."
            )
        body = Gtk.Label(label="\n".join(lines) or _("No frame log for this session."),
                         xalign=0, wrap=True, selectable=True)
        body.add_css_class("monospace")
        inner = Adw.ActionRow()
        inner.set_child(body)
        row.add_row(inner)

        export = Adw.ActionRow(title=_("Share this session"))
        json_btn = Gtk.Button(label=_("Copy as JSON"), valign=Gtk.Align.CENTER)
        json_btn.add_css_class("flat")
        json_btn.connect("clicked", lambda _b, sess=s: self._export_session_json(sess))
        export.add_suffix(json_btn)
        png_btn = Gtk.Button(label=_("Save as image"), valign=Gtk.Align.CENTER)
        png_btn.add_css_class("flat")
        png_btn.connect("clicked", lambda _b, sess=s: self._export_session_png(sess))
        export.add_suffix(png_btn)
        row.add_row(export)

        self._sessions_group.add(row)
        if prepend:
            self._session_rows.insert(0, row)
        else:
            self._session_rows.append(row)
        if len(self._session_rows) > 30:
            self._sessions_group.remove(self._session_rows.pop())

    # -- incident list --------------------------------------------
    def _set_empty(self) -> None:
        if self._empty_row is None:
            self._empty_row = Adw.ActionRow(
                title=_("No incidents recorded"),
                subtitle=_("Throttling and GPU faults during gameplay show up here"),
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
        profiles = [p for p in (status.get("profiles") or []) if p.get("exe") != "__forced__"]
        floors = [p.get("fps_dip_floor", 22) for p in profiles if p.get("fps_watchdog")]
        if floors:
            self.fps_graph.set_threshold(max(floors))
        self._bench_profiles = profiles
        names = [p.get("display_name") or p.get("exe") for p in profiles]
        self._bench_combo.set_model(Gtk.StringList.new(names or ["(no games yet)"]))
        self._bench_row.set_sensitive(bool(profiles))

    # -- benchmark ----------------------------------------------
    def _on_arm_benchmark(self, _row) -> None:
        profs = getattr(self, "_bench_profiles", [])
        idx = self._bench_combo.get_selected()
        if not profs or idx >= len(profs):
            return
        exe = profs[idx]["exe"]
        try:
            self.bridge.arm_benchmark(exe)
        except Exception as exc:  # noqa: BLE001
            self._toast(f"Couldn't arm: {exc}")
            return
        self._toast(f"Benchmark armed — launch {profs[idx].get('display_name') or exe} "
                    "and play for a few minutes. The report card lands in Session history.")

    # -- shareable benchmark cards ----------------------------------
    def _export_session_json(self, session: dict) -> None:
        text = json.dumps(session, indent=2)
        disp = Gdk.Display.get_default()
        if disp is not None:
            disp.get_clipboard().set(text)
        self._toast(_("Session JSON copied — paste it into a community/benchmarks/ PR "
                      "or a forum thread"))

    def _export_session_png(self, session: dict) -> None:
        import re
        import time as _time
        from pathlib import Path

        from goblinmode.benchmarkcard import render_png

        out_dir = Path.home() / "Pictures" / "goblin-mode-pro"
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            game = re.sub(r"[^A-Za-z0-9._-]+", "_", str(session.get("game") or session.get("exe") or "session"))
            path = out_dir / f"{game}-{int(_time.time())}.png"
            render_png(session, str(path))
        except Exception as exc:  # noqa: BLE001
            self._toast(f"Couldn't save the image: {exc}")
            return
        self._toast(f"Saved to {path}")

    # -- benchmark comparison -------------------------------------
    def _on_compare(self, _row) -> None:
        profs = getattr(self, "_bench_profiles", [])
        if not profs:
            self._toast(_("No games with a profile yet"))
            return
        idx = self._bench_combo.get_selected()
        if idx >= len(profs):
            idx = 0
        exe = profs[idx]["exe"]
        self.bridge.get_session_history_async(exe, self._compare_history_ready)

    def _compare_history_ready(self, history, err) -> None:
        if err is not None or not history or len(history) < 2:
            self._toast(_("Need at least two recorded sessions for this game to compare"))
            return
        b, a = history[-1], history[-2]  # newest = "after", prior = "before"
        from goblinmode.benchmarkcard import diff_sessions

        rows = diff_sessions(a, b)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        header = Gtk.Label(
            label=f"{a.get('started', '')[:16]}  →  {b.get('started', '')[:16]}",
            xalign=0)
        header.add_css_class("dim-label")
        box.append(header)
        for r in rows:
            line = f"{r['label']:<22} {r['a']!s:>10}  →  {r['b']!s:>10}"
            if r["delta_pct"] is not None:
                arrow = "▲" if r["better"] == "b" else "▼" if r["better"] == "a" else "·"
                line += f"   {arrow} {r['delta_pct']:+.1f}%"
            lbl = Gtk.Label(label=line, xalign=0, selectable=True)
            lbl.add_css_class("monospace")
            if r["better"] == "b":
                lbl.add_css_class("success")
            elif r["better"] == "a":
                lbl.add_css_class("error")
            box.append(lbl)

        d = Adw.AlertDialog(heading=_("Session comparison"))
        d.set_extra_child(box)
        d.add_response("ok", _("Close"))
        d.present(self._window)

    # -- proton / shader caches -------------------------------
    def load_proton_info(self, info: dict) -> None:
        for r in self._proton_rows:
            self._proton_group.remove(r)
        self._proton_rows.clear()
        builds = (info or {}).get("builds") or []
        caches = (info or {}).get("shader_caches") or []
        if not builds and not caches:
            row = Adw.ActionRow(title=_("Nothing found"),
                                subtitle=_("No custom Proton builds or shader caches yet"))
            self._proton_group.add(row)
            self._proton_rows.append(row)
            return
        for b in builds[:12]:
            row = Adw.ActionRow(title=b["name"], subtitle=b["kind"])
            row.add_css_class("property")
            self._proton_group.add(row)
            self._proton_rows.append(row)
        for c in caches:
            mb = c["bytes"] / (1024 ** 2)
            row = Adw.ActionRow(title=c["label"], subtitle=f"{mb:.0f} MB  ·  {c['path']}")
            clear = Gtk.Button(label=_("Clear"), valign=Gtk.Align.CENTER)
            clear.add_css_class("flat")
            if mb < 1:
                clear.set_sensitive(False)
            clear.connect("clicked", lambda _b, p=c["path"]: self._clear_cache(p))
            row.add_suffix(clear)
            self._proton_group.add(row)
            self._proton_rows.append(row)

    def _clear_cache(self, path: str) -> None:
        d = Adw.AlertDialog(
            heading=_("Clear this shader cache?"),
            body=_("Games will rebuild it on next launch — the first run may stutter "
            "while shaders recompile."))
        d.add_response("cancel", _("Cancel"))
        d.add_response("clear", _("Clear"))
        d.set_response_appearance("clear", Adw.ResponseAppearance.DESTRUCTIVE)
        d.connect("response", lambda _dd, resp: resp == "clear" and
                  self.bridge.clear_shader_cache_async(path, self._cache_cleared))
        d.present(self._window)

    def _cache_cleared(self, res, _err) -> None:
        r = res or {}
        self._toast(r.get("message", _("done")) if r.get("ok") else
                    f"Couldn't clear: {r.get('message', 'error')}")
        self._window.request_proton_refresh()

    def _on_export_setup(self, _row) -> None:
        self._toast(_("Building setup report…"))
        self.bridge.export_setup_async(self._setup_ready)

    def _setup_ready(self, md, _err) -> None:
        if not md:
            self._toast(_("Setup export failed"))
            return
        disp = Gdk.Display.get_default()
        if disp is not None:
            disp.get_clipboard().set(md)
        self._toast(_("Full setup copied to clipboard — paste it into a help thread"))

    # -- export ---------------------------------------------------
    def _on_export(self, _row) -> None:
        try:
            payload = self.bridge.export_last_incident()
        except Exception as exc:  # noqa: BLE001
            log.warning("export failed: %s", exc)
            self._toast(_("Export failed"))
            return
        if not payload:
            self._toast(_("No incident to export yet"))
            return
        # Clipboard from the GUI as a reliable fallback to the daemon's wl-copy.
        display = Gdk.Display.get_default()
        if display is not None:
            display.get_clipboard().set(payload)
        self._toast(_("Incident payload copied to clipboard"))

    def _on_report(self, _row) -> None:
        dialog = Adw.AlertDialog(
            heading=_("Build a bug report"),
            body=_("Describe the problem in a sentence (optional). GMP gathers system "
            "info, the pre-flight results, the last incident and the Proton log "
            "analysis, copies a Markdown report to your clipboard, and opens a "
            "pre-filled issue form."),
        )
        entry = Gtk.Entry(placeholder_text=_("e.g. crashes on the loading screen"))
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("build", _("Build report"))
        dialog.set_response_appearance("build", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect("response", self._on_report_response, entry)
        dialog.present(self._window)

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
        self._toast(_("Bug report copied — paste it into a forum thread or issue"))

    def _on_analyze(self, _row) -> None:
        try:
            findings = self.bridge.analyze_log()
        except Exception as exc:  # noqa: BLE001
            self._toast(f"Analyze failed: {exc}")
            return
        if not findings:
            self._toast(_("No captured Proton log, or no known issues in it"))
            return

        from goblinmode.gui.widgets.snippet import command_row

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for f in findings:
            group = Adw.PreferencesGroup(
                title=f"{f['label']}  ({f['category']}, ×{f['count']})",
                description=f"{f['cause']}\n{f['fix']}",
            )
            if f.get("fix_cmd"):
                group.add(command_row(_("Run this to fix it"), f["fix_cmd"]))
            box.append(group)
        sc = Gtk.ScrolledWindow(min_content_height=200, max_content_height=440)
        sc.set_child(box)

        d = Adw.AlertDialog(heading=f"{len(findings)} issue(s) in the Proton log")
        d.set_extra_child(sc)
        d.add_response("ok", _("Close"))
        d.present(self._window)

    def _toast(self, text: str) -> None:
        if hasattr(self._window, "toast"):
            self._window.toast(text)
        else:
            log.info("%s", text)
