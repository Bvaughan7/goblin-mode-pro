"""System Check page.

Runs a set of checks on the machine's kernel settings and shows, for each, a
plain-language status and (where it is safe to do automatically) a one-click
fix. The scan and the fixes run on the daemon and are called asynchronously so
the window never freezes; a spinner shows while work is in progress.
"""

from __future__ import annotations

import logging
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk  # noqa: E402

from goblinmode.i18n import _  # noqa: E402

from goblinmode.ipc.daemon_bridge import BridgeClient

log = logging.getLogger(__name__)

_PILL = {
    "ok": (_("PASS"), "success"),
    "warn": (_("CHECK"), "warning"),
    "fail": (_("ACTION"), "error"),
    "info": (_("FYI"), "dim-label"),
    "unknown": ("?", "dim-label"),
}


class PreflightPage(Adw.PreferencesPage):
    def __init__(self, bridge: BridgeClient, window) -> None:
        super().__init__(title=_("System Check"), icon_name="emblem-ok-symbolic")
        self.bridge = bridge
        self._window = window
        self._busy = False

        head = Adw.PreferencesGroup(
            description=_("These are Linux settings that decide whether some games "
            "run smoothly, stutter, or fail to start. The scan is read-only."),
        )

        status_row = Adw.ActionRow()
        self._spinner = Gtk.Spinner()
        status_row.add_prefix(self._spinner)
        self._status = Gtk.Label(xalign=0, wrap=True)
        self._status.add_css_class("dim-label")
        status_row.set_child(self._status)
        head.add(status_row)

        self._rescan = Adw.ButtonRow(title=_("Scan again"))
        self._rescan.set_start_icon_name("view-refresh-symbolic")
        self._rescan.connect("activated", lambda _r: self.refresh())
        head.add(self._rescan)

        self._apply_row = Adw.ButtonRow(title=_("Apply the safe fixes"))
        self._apply_row.set_start_icon_name("emblem-system-symbolic")
        self._apply_row.connect("activated", self._on_apply)
        self._apply_row.set_sensitive(False)
        head.add(self._apply_row)
        self.add(head)

        self._checks_group = Adw.PreferencesGroup(title=_("Checks"))
        self.add(self._checks_group)
        self._rows: list[Gtk.Widget] = []
        self._applied_keys: set[str] = set()   # sysctls we changed this session

        self._persist_group = Adw.PreferencesGroup(
            title=_("Make it stick after a reboot"),
            description=_("The one-click fixes above are temporary. To keep them, "
            "install the text below (ask an admin if you're not sure)."),
        )
        self._persist = Adw.ExpanderRow(title=_("Settings to install"))
        self._dropin = _code_row("/etc/sysctl.d/99-goblin-mode-pro.conf", "")
        self._kparams = _code_row(_("Startup (kernel) options"), "")
        self._persist.add_row(self._dropin)
        self._persist.add_row(self._kparams)
        self._persist_group.add(self._persist)
        self._persist_group.set_visible(False)
        self.add(self._persist_group)

    # -- scan -----------------------------------------------------------
    def refresh(self) -> None:
        if self._busy:
            return
        self._set_busy(True, _("Scanning your system…"))
        self.bridge.run_preflight_async(self._on_results)

    def _on_results(self, results: list | None, err) -> None:
        self._set_busy(False)
        if err is not None or results is None:
            self._status.set_label(f"Couldn't run the scan: {err}")
            return
        self._render(results)

    def _render(self, results: list[dict[str, Any]]) -> None:
        for r in self._rows:
            self._checks_group.remove(r)
        self._rows.clear()

        n = {"ok": 0, "warn": 0, "fail": 0, "info": 0, "unknown": 0}
        for chk in results:
            n[chk["status"]] = n.get(chk["status"], 0) + 1
            self._rows.append(self._build_row(chk))

        parts = [f"{n['ok']} fine"]
        if n["fail"]:
            parts.append(f"{n['fail']} need action")
        if n["warn"]:
            parts.append(f"{n['warn']} worth a look")
        if n["info"]:
            parts.append(f"{n['info']} for information")
        self._status.set_label(" · ".join(parts))

        self._pending = [
            c for c in results if c["sysctl"] and c["status"] in ("warn", "fail")
        ]
        self._apply_row.set_sensitive(bool(self._pending) and not self._busy)

        dropin = "\n".join(f"{c['sysctl'][0]} = {c['sysctl'][1]}" for c in self._pending)
        kparams = " ".join(
            c["kernel_param"] for c in results
            if c["kernel_param"] and c["status"] in ("warn", "fail")
        )
        self._dropin.set_text(dropin or _("# nothing to add"))
        self._kparams.set_text(kparams or _("# nothing to add"))
        self._persist_group.set_visible(bool(dropin) or bool(kparams and kparams.strip("# ")))

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

        detail = chk["detail"] or _("No further detail.")
        if chk["fix_hint"]:
            detail += f"\n\nTo fix it yourself: {chk['fix_hint']}"
        body = Adw.ActionRow(title=detail)
        body.set_title_lines(0)
        body.set_css_classes(["dim-label"])
        row.add_row(body)

        sysctl = chk.get("sysctl")
        if sysctl and sysctl[0] in self._applied_keys:
            undo = Adw.ButtonRow(title=f"Undo — restore {sysctl[0]} to its previous value")
            undo.set_start_icon_name("edit-undo-symbolic")
            undo.connect("activated", lambda _r, k=sysctl[0]: self._on_undo(k))
            row.add_row(undo)

        self._checks_group.add(row)
        return row

    def _on_undo(self, key: str) -> None:
        self._set_busy(True, f"Reverting {key}…")
        self.bridge.revert_preflight_fix_async(key, lambda res, err: self._on_undone(key, res, err))

    def _on_undone(self, key: str, res, err) -> None:
        self._set_busy(False)
        r = res or {}
        if err or not r.get("ok"):
            self._toast(f"Couldn't revert {key}: {err or r.get('message')}")
            return
        self._applied_keys.discard(key)
        self._toast(f"{key} reverted")
        self.refresh()

    # -- fixes --------------------------------------------------------
    def _on_apply(self, _row) -> None:
        if self._busy:
            return
        self._set_busy(True, _("Applying fixes… (you may be asked to authenticate)"))
        self.bridge.apply_preflight_fixes_async(self._on_applied)

    def _on_applied(self, res: dict | None, err) -> None:
        self._set_busy(False)
        if err is not None or res is None:
            self._toast(f"Couldn't apply the fixes: {err}")
            return
        for entry in res.get("applied") or []:
            self._applied_keys.add(entry.split("=", 1)[0])
        done = ", ".join(res.get("applied") or []) or _("nothing")
        failed = res.get("failed") or []
        self._toast(f"Applied: {done}" + (f" — failed: {', '.join(failed)}" if failed else ""))
        self.refresh()

    # -- busy state -------------------------------------------------
    def _set_busy(self, busy: bool, message: str = "") -> None:
        self._busy = busy
        self._rescan.set_sensitive(not busy)
        self._apply_row.set_sensitive(not busy and bool(getattr(self, "_pending", None)))
        if busy:
            self._spinner.start()
            self._spinner.set_visible(True)
            self._status.set_label(message)
        else:
            self._spinner.stop()
            self._spinner.set_visible(False)

    def _toast(self, text: str) -> None:
        if hasattr(self._window, "toast"):
            self._window.toast(text)


def _code_row(title: str, text: str) -> Adw.ActionRow:
    row = Adw.ActionRow(title=title)
    row.add_css_class("property")
    view = Gtk.TextView(editable=False, monospace=True, top_margin=6, bottom_margin=6,
                        left_margin=8, right_margin=8, wrap_mode=Gtk.WrapMode.WORD_CHAR)
    view.get_buffer().set_text(text)
    scroller = Gtk.ScrolledWindow(min_content_height=52, max_content_height=140,
                                  propagate_natural_height=True)
    scroller.set_child(view)
    row.set_child(scroller)

    copy = Gtk.Button(icon_name="edit-copy-symbolic", valign=Gtk.Align.CENTER, tooltip_text=_("Copy"))
    copy.add_css_class("flat")

    def do_copy(_b):
        buf = view.get_buffer()
        disp = Gdk.Display.get_default()
        if disp is not None:
            disp.get_clipboard().set(
                buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
            )

    copy.connect("clicked", do_copy)
    row.add_suffix(copy)
    row.set_text = lambda t: view.get_buffer().set_text(t)  # type: ignore[attr-defined]
    return row
