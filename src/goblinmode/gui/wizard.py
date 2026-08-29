"""First-run wizard — a short guided setup shown once.

Four steps: welcome → system check (with one-click fixes) → set the launch
wrapper for your launcher → done. Skippable at any point. A marker file records
that it's been seen so it never nags.
"""

from __future__ import annotations

import logging

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from goblinmode.paths import ONBOARDED_MARKER, ensure_user_dirs
from goblinmode.runner import LAUNCH_OPTION

log = logging.getLogger(__name__)

_LAUNCHERS = {
    "Steam": ("Right-click the game → Properties → General → Launch Options, and "
              "paste:", LAUNCH_OPTION),
    "Lutris": ("Right-click the game → Configure → System options → "
               "Command prefix:", "goblin-run"),
    "Heroic": ("Game → Settings → Advanced → Wrapper command:", "goblin-run"),
    "Bottles / other": ("Set the runner's wrapper or command prefix to:", "goblin-run"),
}


def should_show() -> bool:
    return not ONBOARDED_MARKER.exists()


def mark_done() -> None:
    try:
        ensure_user_dirs()
        ONBOARDED_MARKER.touch()
    except OSError as exc:  # noqa: BLE001
        log.warning("could not write onboarding marker: %s", exc)


class FirstRunWizard(Adw.Window):
    def __init__(self, parent, bridge) -> None:
        super().__init__(transient_for=parent, modal=True, title="Welcome",
                         default_width=560, default_height=520)
        self.bridge = bridge
        self._stack = Adw.ViewStack()

        tbv = Adw.ToolbarView()
        header = Adw.HeaderBar()
        skip = Gtk.Button(label="Skip")
        skip.connect("clicked", lambda _b: self._finish())
        header.pack_end(skip)
        tbv.add_top_bar(header)
        tbv.set_content(self._stack)
        self.set_content(tbv)

        self._stack.add_named(self._welcome_page(), "welcome")
        self._stack.add_named(self._check_page(), "check")
        self._stack.add_named(self._launcher_page(), "launcher")
        self._stack.add_named(self._done_page(), "done")
        self._go("welcome")

    def _go(self, name: str) -> None:
        self._stack.set_visible_child_name(name)
        if name == "check":
            self._run_check()

    # -- pages -----------------------------------------------------
    def _page(self, icon: str, title: str, body: str) -> Adw.StatusPage:
        return Adw.StatusPage(icon_name=icon, title=title, description=body)

    def _welcome_page(self) -> Gtk.Widget:
        p = self._page("com.goblinmode.Pro",
                       "Goblin Mode Pro",
                       "Three quick steps: check your system is game-ready, wire up "
                       "your game launcher, and you're done. Takes about a minute.")
        btn = Gtk.Button(label="Get started", halign=Gtk.Align.CENTER)
        btn.add_css_class("suggested-action")
        btn.add_css_class("pill")
        btn.connect("clicked", lambda _b: self._go("check"))
        p.set_child(btn)
        return p

    def _check_page(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14,
                      margin_top=24, margin_bottom=24, margin_start=24, margin_end=24,
                      valign=Gtk.Align.CENTER)
        self._check_icon = Gtk.Image(icon_name="emblem-ok-symbolic", pixel_size=48)
        self._check_title = Gtk.Label(label="Checking your system…")
        self._check_title.add_css_class("title-1")
        self._check_detail = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER)
        self._check_detail.add_css_class("dim-label")
        self._fix_btn = Gtk.Button(label="Apply the safe fixes", halign=Gtk.Align.CENTER,
                                   visible=False)
        self._fix_btn.add_css_class("pill")
        self._fix_btn.connect("clicked", self._on_fix)
        nxt = Gtk.Button(label="Next", halign=Gtk.Align.CENTER)
        nxt.add_css_class("pill")
        nxt.connect("clicked", lambda _b: self._go("launcher"))
        for w in (self._check_icon, self._check_title, self._check_detail,
                  self._fix_btn, nxt):
            box.append(w)
        return box

    def _launcher_page(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      margin_top=24, margin_bottom=24, margin_start=24, margin_end=24)
        head = Gtk.Label(label="Wire up your launcher")
        head.add_css_class("title-1")
        sub = Gtk.Label(wrap=True, label="Goblin Mode Pro needs a small wrapper on "
                        "your game's launch command so it can inject settings and "
                        "read the Proton log. Pick your launcher:")
        sub.add_css_class("dim-label")
        box.append(head)
        box.append(sub)
        self._launcher_combo = Gtk.DropDown.new_from_strings(list(_LAUNCHERS))
        self._launcher_combo.connect("notify::selected", lambda *_: self._update_launcher())
        box.append(self._launcher_combo)
        self._launcher_help = Gtk.Label(wrap=True, xalign=0)
        box.append(self._launcher_help)
        row = Gtk.Box(spacing=8)
        self._launcher_code = Gtk.Entry(editable=False, hexpand=True)
        self._launcher_code.add_css_class("monospace")
        copy = Gtk.Button(icon_name="edit-copy-symbolic")
        copy.connect("clicked", lambda _b: self._copy(self._launcher_code.get_text()))
        row.append(self._launcher_code)
        row.append(copy)
        box.append(row)
        nxt = Gtk.Button(label="Done", halign=Gtk.Align.CENTER)
        nxt.add_css_class("suggested-action")
        nxt.add_css_class("pill")
        nxt.connect("clicked", lambda _b: self._go("done"))
        box.append(nxt)
        self._update_launcher()
        return box

    def _done_page(self) -> Gtk.Widget:
        p = self._page("emblem-ok-symbolic", "You're set",
                       "Launch a game and Goblin Mode Pro takes over automatically. "
                       "Open it any time from the tray icon or your app menu to tune "
                       "per-game settings.")
        btn = Gtk.Button(label="Finish", halign=Gtk.Align.CENTER)
        btn.add_css_class("suggested-action")
        btn.add_css_class("pill")
        btn.connect("clicked", lambda _b: self._finish())
        p.set_child(btn)
        return p

    # -- behaviour ----------------------------------------------
    def _run_check(self) -> None:
        self.bridge.get_health_async(self._check_ready)

    def _check_ready(self, health, err) -> None:
        h = health or {}
        score = h.get("score")
        if score is None:
            self._check_title.set_label("Couldn't run the check")
            return
        self._check_title.set_label(f"System readiness: {score:g}/10")
        worst = h.get("worst") or []
        n = h.get("counts") or {}
        if worst:
            self._check_detail.set_label("Needs attention: " + ", ".join(worst)
                                         + ".\nWe can fix the safe ones now.")
            self._fix_btn.set_visible(True)
            self._check_icon.set_from_icon_name("dialog-warning-symbolic")
        elif n.get("warn"):
            self._check_detail.set_label(f"{n['warn']} thing(s) worth a look — you "
                                         "can review them later in System Check.")
        else:
            self._check_detail.set_label("Your machine is game-ready. 🎮")

    def _on_fix(self, _btn) -> None:
        self._fix_btn.set_sensitive(False)
        self._fix_btn.set_label("Applying…")
        self.bridge.apply_preflight_fixes_async(
            lambda res, err: (self._fix_btn.set_visible(False), self._run_check()))

    def _update_launcher(self) -> None:
        name = list(_LAUNCHERS)[self._launcher_combo.get_selected()]
        help_text, code = _LAUNCHERS[name]
        self._launcher_help.set_label(help_text)
        self._launcher_code.set_text(code)

    def _copy(self, text: str) -> None:
        disp = Gdk.Display.get_default()
        if disp is not None:
            disp.get_clipboard().set(text)

    def _finish(self) -> None:
        mark_done()
        self.close()
