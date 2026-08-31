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
from gi.repository import Adw, Gdk, Gtk

from goblinmode import capabilities
from goblinmode.gui.widgets.snippet import command_row
from goblinmode.i18n import _
from goblinmode.paths import ONBOARDED_MARKER, ensure_user_dirs
from goblinmode.runner import LAUNCH_OPTION

log = logging.getLogger(__name__)

_LAUNCHERS = {
    "Steam": (_("Right-click the game → Properties → General → Launch Options, and "
              "paste:"), LAUNCH_OPTION),
    "Lutris": (_("Right-click the game → Configure → System options → "
               "Command prefix:"), "goblin-run"),
    "Heroic": (_("Game → Settings → Advanced → Wrapper command:"), "goblin-run"),
    "Bottles / other": (_("Set the runner's wrapper or command prefix to:"), "goblin-run"),
}


def should_show() -> bool:
    return not ONBOARDED_MARKER.exists()


def mark_done() -> None:
    try:
        ensure_user_dirs()
        ONBOARDED_MARKER.touch()
    except OSError as exc:
        log.warning("could not write onboarding marker: %s", exc)


class FirstRunWizard(Adw.Window):
    def __init__(self, parent, bridge) -> None:
        super().__init__(transient_for=parent, modal=True, title=_("Welcome"),
                         default_width=560, default_height=520)
        self.bridge = bridge
        self._stack = Adw.ViewStack()

        tbv = Adw.ToolbarView()
        header = Adw.HeaderBar()
        skip = Gtk.Button(label=_("Skip"))
        skip.connect("clicked", lambda _b: self._finish())
        header.pack_end(skip)
        tbv.add_top_bar(header)
        tbv.set_content(self._stack)
        self.set_content(tbv)

        self._stack.add_named(self._welcome_page(), "welcome")
        self._stack.add_named(self._check_page(), "check")
        self._stack.add_named(self._install_page(), "install")
        self._stack.add_named(self._launcher_page(), "launcher")
        self._stack.add_named(self._done_page(), "done")
        self._go("welcome")

    def _go(self, name: str) -> None:
        self._stack.set_visible_child_name(name)
        if name == "check":
            self._run_check()
        elif name == "install":
            self._populate_install()

    # -- pages -----------------------------------------------------
    def _page(self, icon: str, title: str, body: str) -> Adw.StatusPage:
        return Adw.StatusPage(icon_name=icon, title=title, description=body)

    def _welcome_page(self) -> Gtk.Widget:
        p = self._page("com.goblinmode.Pro",
                       "Goblin Mode Pro",
                       _("Three quick steps: check your system is game-ready, wire up "
                       "your game launcher, and you're done. Takes about a minute."))
        btn = Gtk.Button(label=_("Get started"), halign=Gtk.Align.CENTER)
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
        self._check_title = Gtk.Label(label=_("Checking your system…"))
        self._check_title.add_css_class("title-1")
        self._check_detail = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER)
        self._check_detail.add_css_class("dim-label")
        self._fix_btn = Gtk.Button(label=_("Apply the safe fixes"), halign=Gtk.Align.CENTER,
                                   visible=False)
        self._fix_btn.add_css_class("pill")
        self._fix_btn.connect("clicked", self._on_fix)
        nxt = Gtk.Button(label=_("Next"), halign=Gtk.Align.CENTER)
        nxt.add_css_class("pill")
        nxt.connect("clicked", lambda _b: self._go("install"))
        for w in (self._check_icon, self._check_title, self._check_detail,
                  self._fix_btn, nxt):
            box.append(w)
        return box

    def _install_page(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      margin_top=24, margin_bottom=24, margin_start=24, margin_end=24)
        head = Gtk.Label(label=_("Missing pieces"))
        head.add_css_class("title-1")
        box.append(head)
        self._install_sub = Gtk.Label(wrap=True, xalign=0)
        self._install_sub.add_css_class("dim-label")
        box.append(self._install_sub)
        self._install_group = Adw.PreferencesGroup()
        box.append(self._install_group)
        self._install_rows: list[Adw.ActionRow] = []
        nxt = Gtk.Button(label=_("Next"), halign=Gtk.Align.CENTER)
        nxt.add_css_class("suggested-action")
        nxt.add_css_class("pill")
        nxt.connect("clicked", lambda _b: self._go("launcher"))
        box.append(nxt)
        return box

    def _populate_install(self) -> None:
        for r in self._install_rows:
            self._install_group.remove(r)
        self._install_rows.clear()

        caps = capabilities.detect()
        pm = caps.get("package_manager")
        missing = []
        if not caps.get("mangohud"):
            missing.append(("mangohud", _("The FPS overlay and frame-rate watchdog need it")))
        if not caps.get("gamemode"):
            missing.append(("gamemode", _("Per-game governor/priority/GPU tuning most launchers expect")))

        rows: list[tuple[str, str]] = []
        for pkg, why in missing:
            cmd = capabilities.install_command(pm, pkg)
            if cmd:
                rows.append((why, cmd))
            else:
                rows.append((f"{why} - install the '{pkg}' package for your distro.", ""))

        if caps.get("kernel_flavor") == "generic":
            why, cmd = capabilities.kernel_upgrade_tip((caps.get("distro_id") or "").lower())
            if cmd:
                rows.append((why, cmd))

        if not rows:
            self._install_sub.set_label(_(
                "Nothing missing - MangoHud, GameMode and your kernel all look good."))
        else:
            self._install_sub.set_label(_(
                "Copy these into a terminal when you get a chance. None of this runs "
                "automatically - Goblin Mode Pro never installs packages on its own."))

        for why, cmd in rows:
            row = command_row(why, cmd) if cmd else Adw.ActionRow(title=why)
            self._install_group.add(row)
            self._install_rows.append(row)

    def _launcher_page(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      margin_top=24, margin_bottom=24, margin_start=24, margin_end=24)
        head = Gtk.Label(label=_("Wire up your launcher"))
        head.add_css_class("title-1")
        sub = Gtk.Label(wrap=True, label=_("Goblin Mode Pro needs a small wrapper on "
                        "your game's launch command so it can inject settings and "
                        "read the Proton log. Pick your launcher:"))
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
        nxt = Gtk.Button(label=_("Done"), halign=Gtk.Align.CENTER)
        nxt.add_css_class("suggested-action")
        nxt.add_css_class("pill")
        nxt.connect("clicked", lambda _b: self._go("done"))
        box.append(nxt)
        self._update_launcher()
        return box

    def _done_page(self) -> Gtk.Widget:
        p = self._page("emblem-ok-symbolic", _("You're set"),
                       _("Launch a game and Goblin Mode Pro takes over automatically. "
                       "Open it any time from the tray icon or your app menu to tune "
                       "per-game settings."))
        btn = Gtk.Button(label=_("Finish"), halign=Gtk.Align.CENTER)
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
            self._check_title.set_label(_("Couldn't run the check"))
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
            self._check_detail.set_label(_("Your machine is game-ready. 🎮"))

    def _on_fix(self, _btn) -> None:
        self._fix_btn.set_sensitive(False)
        self._fix_btn.set_label(_("Applying…"))
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
