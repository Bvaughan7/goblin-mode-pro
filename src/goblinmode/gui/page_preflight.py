"""System Check page - the pre-flight panel.

One row per check with a status pill; a button to apply the runtime sysctl
fixes; an expander with the persistent drop-in / kernel-param text to install.
"""

from __future__ import annotations

import logging
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from goblinmode.ipc.daemon_bridge import BridgeClient

log = logging.getLogger(__name__)

_PILL = {
    "ok": ("OK", "success"),
    "warn": ("WARN", "warning"),
    "fail": ("FAIL", "error"),
    "info": ("FYI", "dim-label"),
    "unknown": ("?", "dim-label"),
}


class PreflightPage(Adw.PreferencesPage):
    def __init__(self, bridge: BridgeClient, window) -> None:
        super().__init__(title="System Check", icon_name="emblem-ok-symbolic")
        self.bridge = bridge
        self._window = window

        head = Adw.PreferencesGroup(
            description="Kernel knobs and limits that make Linux games crash on "
            "launch, stutter, or leave performance on the table.",
        )
        self._summary = Gtk.Label(xalign=0)
        self._summary.add_css_class("dim-label")
        head.add(self._summary)
        rescan = Adw.ButtonRow(title="Re-scan")
        rescan.set_start_icon_name("view-refresh-symbolic")
        rescan.connect("activated", lambda _r: self.refresh())
        head.add(rescan)
        self._apply_row = Adw.ButtonRow(title="Apply safe fixes now")
        self._apply_row.set_start_icon_name("emblem-system-symbolic")
        self._apply_row.connect("activated", self._on_apply)
        head.add(self._apply_row)
        self.add(head)

        self._checks_group = Adw.PreferencesGroup(title="Checks")
        self.add(self._checks_group)
        self._rows: list[Gtk.Widget] = []

        self._persist_group = Adw.PreferencesGroup(
            title="Make it permanent",
            description="Runtime fixes reset on reboot. Install these to persist.",
        )
        self._persist = Adw.ExpanderRow(title="Config to install")
        self._dropin = _code_row("/etc/sysctl.d/99-goblin-mode-pro.conf", "")
        self._kparams = _code_row("Kernel boot parameters", "")
        self._persist.add_row(self._dropin)
        self._persist.add_row(self._kparams)
        self._persist_group.add(self._persist)
        self.add(self._persist_group)

    # -- data ------------------------------------------------------
    def refresh(self) -> None:
        try:
            results = self.bridge.run_preflight()
        except Exception as exc:  # noqa: BLE001
            log.warning("preflight failed: %s", exc)
            return
        for r in self._rows:
            self._checks_group.remove(r)
        self._rows.clear()

        n = {"ok": 0, "warn": 0, "fail": 0, "info": 0, "unknown": 0}
        for chk in results:
            n[chk["status"]] = n.get(chk["status"], 0) + 1
            self._rows.append(self._build_row(chk))
        self._summary.set_label(
            f"{n['ok']} passing · {n['warn']} warnings · {n['fail']} failing"
            + (f" · {n['info']} FYI" if n['info'] else "")
        )
        self._apply_row.set_sensitive(any(
            c["sysctl"] and c["status"] in ("warn", "fail") for c in results
        ))

        dropin = "\n".join(
            f"{c['sysctl'][0]} = {c['sysctl'][1]}"
            for c in results if c["sysctl"] and c["status"] in ("warn", "fail")
        )
        kparams = " ".join(
            c["kernel_param"] for c in results
            if c["kernel_param"] and c["status"] in ("warn", "fail")
        )
        self._dropin.set_text(dropin or "# nothing to add")
        self._kparams.set_text(kparams or "# nothing to add")
        self._persist_group.set_visible(bool(dropin or kparams))

    def _build_row(self, chk: dict[str, Any]) -> Adw.ExpanderRow:
        label, css = _PILL.get(chk["status"], _PILL["unknown"])
        row = Adw.ExpanderRow(title=chk["title"], subtitle=chk["why"])
        pill = Gtk.Label(label=label)
        pill.add_css_class("caption-heading")
        pill.add_css_class(css)
        pill.set_valign(Gtk.Align.CENTER)
        row.add_suffix(pill)
        val = Gtk.Label(label=str(chk["value"]))
        val.add_css_class("dim-label")
        val.add_css_class("monospace")
        val.set_valign(Gtk.Align.CENTER)
        row.add_suffix(val)

        body = Adw.ActionRow(
            title=chk["detail"] or "No detail.",
            subtitle=(chk["fix_hint"] or ""),
        )
        body.set_title_lines(4)
        body.set_subtitle_lines(4)
        row.add_row(body)
        self._checks_group.add(row)
        return row

    # -- actions -------------------------------------------------
    def _on_apply(self, _row) -> None:
        try:
            res = self.bridge.apply_preflight_fixes()
        except Exception as exc:  # noqa: BLE001
            self._toast(f"Fix failed: {exc}")
            return
        msg = []
        if res["applied"]:
            msg.append("applied " + ", ".join(res["applied"]))
        if res["failed"]:
            msg.append("failed " + ", ".join(res["failed"]))
        self._toast("; ".join(msg) or "nothing to apply")
        self.refresh()

    def _toast(self, text: str) -> None:
        if hasattr(self._window, "toast"):
            self._window.toast(text)


def _code_row(title: str, text: str) -> Adw.ActionRow:
    row = Adw.ActionRow(title=title)
    row.add_css_class("property")
    view = Gtk.TextView(editable=False, monospace=True, top_margin=6, bottom_margin=6,
                        left_margin=8, right_margin=8, wrap_mode=Gtk.WrapMode.WORD_CHAR)
    view.get_buffer().set_text(text)
    sc = Gtk.ScrolledWindow(min_content_height=60, max_content_height=140, propagate_natural_height=True)
    sc.set_child(view)
    row.set_child(sc)
    copy = Gtk.Button(icon_name="edit-copy-symbolic", valign=Gtk.Align.CENTER)
    copy.add_css_class("flat")

    def do_copy(_b):
        buf = view.get_buffer()
        disp = Gdk.Display.get_default()
        if disp:
            disp.get_clipboard().set(buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False))

    copy.connect("clicked", do_copy)
    row.add_suffix(copy)
    row._set_text = lambda t: view.get_buffer().set_text(t)  # type: ignore[attr-defined]
    row.set_text = row._set_text  # type: ignore[attr-defined]
    return row
