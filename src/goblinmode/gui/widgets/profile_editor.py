"""The per-game profile editor - one ``Adw.ExpanderRow`` per game.

Split out of :mod:`goblinmode.gui.page_games`, which had grown to hold both
this and the list around it. The seam is what the code touches: everything here
concerns *one* game and the widgets that edit it, and reaches the outside world
only through :class:`EditorActions`. The page keeps what is about the *set* of
games - the list, add/remove, import/export and the community fetch.

Rows are built from the capability dict, so a machine without (say) RAPL or a
hybrid CPU never sees those controls at all rather than seeing them fail.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any
from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

from goblinmode.i18n import _

from goblinmode.config import GPU_TUNING_VARS, MANGOHUD_TOGGLES, RUNNER_VARS
from goblinmode.gui.widgets.buttonrow import button_row
from goblinmode.gui.widgets.help import help_button

log = logging.getLogger(__name__)

_MANGOHUD_LABELS = {
    "enabled": _("Show overlay"),
    "fps": _("FPS counter"),
    "cpu_temp": _("CPU temperature"),
    "gpu_temp": _("GPU temperature"),
    "ram": _("RAM usage"),
    "frame_timing": _("Frame-timing graph"),
}
_RUNNER_LABELS = {
    "nvapi": _("NVAPI (PROTON_ENABLE_NVAPI + DXVK_ENABLE_NVAPI)"),
    "fsync": _("Force Fsync (WINEFSYNC=1)"),
    "no_esync": _("Disable Esync (PROTON_NO_ESYNC=1)"),
    "dxvk_async": _("Async shader compile (DXVK_ASYNC=1) — only affects "
                   "async-patched DXVK forks, not stock DXVK / current Proton-GE"),
}


@dataclass(frozen=True)
class EditorActions:
    """What an editor can ask the page to do.

    Explicit callbacks rather than a reference back to the page: these six are
    the entire coupling between a row and the list that owns it, and naming
    them is what keeps the split honest.
    """
    save: Callable[[dict[str, Any]], None]
    keep: Callable[[str], None]
    ignore: Callable[[str], None]
    remove: Callable[[Gtk.Button, str], None]
    export: Callable[[str], None]
    share: Callable[[str], None]
    enable_toggled: Callable[[Adw.ExpanderRow, Any, str], None]


class ProfileEditor:
    """Builds and owns the widgets for one game's profile."""

    def __init__(self, exe: str, profile: dict[str, Any],
                 caps: dict[str, Any], actions: EditorActions) -> None:
        self.exe = exe
        self.profile = dict(profile)
        self._caps = caps
        self._actions = actions
        #: set while widgets are being constructed, so setting a switch's
        #: initial state doesn't fire a save for a change the user didn't make
        self._building = False

    def build(self) -> Adw.ExpanderRow:
        """Construct this game's ExpanderRow. Called once per rebuild."""
        exe, p = self.exe, self.profile
        auto = p.get("auto_created", False)
        exp = Adw.ExpanderRow(
            title=p.get("display_name") or exe,
            subtitle=f"{exe}  ·  match: {p.get('match_mode', 'exact')}"
            + (_("  ·  auto-detected") if auto else ""),
        )
        exp.set_show_enable_switch(True)
        exp.set_enable_expansion(bool(p.get("enabled", True)))
        exp.connect("notify::enable-expansion", self._actions.enable_toggled, exe)

        if auto:
            pill = Gtk.Label(label=_("AUTO"))
            pill.add_css_class("caption-heading")
            pill.add_css_class("accent")
            pill.set_valign(Gtk.Align.CENTER)
            exp.add_suffix(pill)
            keep = Gtk.Button(label=_("Keep"), valign=Gtk.Align.CENTER)
            keep.add_css_class("flat")
            keep.connect("clicked", lambda _b: self._actions.keep(exe))
            exp.add_suffix(keep)
            ignore = Gtk.Button(label=_("Ignore"), valign=Gtk.Align.CENTER)
            ignore.add_css_class("flat")
            ignore.connect("clicked", lambda _b: self._actions.ignore(exe))
            exp.add_suffix(ignore)

        share = Gtk.Button(icon_name="send-to-symbolic", valign=Gtk.Align.CENTER)
        share.add_css_class("flat")
        share.set_tooltip_text(_("Export this profile to share it"))
        share.connect("clicked", lambda _b: self._actions.export(exe))
        exp.add_suffix(share)

        remove = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER)
        remove.add_css_class("flat")
        remove.connect("clicked", self._actions.remove, exe)
        exp.add_suffix(remove)

        # -- simple toggles --
        exp.add_row(self._switch_row(
            _("Process priority (renice)"), p.get("renice_enabled", True),
            lambda v: self._patch(exe, renice_enabled=v), help="renice",
        ))
        nice = Adw.SpinRow.new_with_range(-10, 19, 1)
        nice.set_title(_("Nice value"))
        nice.set_subtitle(_("Lower = higher priority (needs the helper)"))
        nice.set_value(p.get("nice_value", -5))
        nice.connect("notify::value", lambda r, _p: self._patch(exe, nice_value=int(r.get_value())))
        exp.add_row(nice)

        if self._caps.get("gamemode"):
            gm = self._switch_row(
                _("Wrap with GameMode"), p.get("use_gamemode", True),
                lambda v: self._patch(exe, use_gamemode=v))
            gm.set_subtitle(_("Turn off if ananicy-cpp is running — both manage "
                              "process niceness"))
            exp.add_row(gm)

        layout = self._caps.get("core_layout") or {}
        pin_opts = [("off", _("Off — use every core"))]
        if layout.get("performance"):
            pin_opts.append(("performance", f"Fast cores only ({len(layout['performance'])} of {len(layout.get('online', []))})"))
        if layout.get("cache_groups"):
            pin_opts.append(("cache0", f"One cache group / CCD ({len(layout['cache_groups'][0])} cores)"))
        if len(pin_opts) > 1:
            pin = Adw.ComboRow(title=_("Pin to CPU cores"))
            pin.set_subtitle(_("Keep the game's threads off the slow cores / the cross-CCD hop"))
            pin.set_model(Gtk.StringList.new([label for _k, label in pin_opts]))
            keys = [k for k, _l in pin_opts]
            cur = p.get("core_pin", "off")
            pin.set_selected(keys.index(cur) if cur in keys else 0)
            pin.connect("notify::selected",
                        lambda r, _p: self._patch(exe, core_pin=keys[r.get_selected()]))
            exp.add_row(pin)

        scx_caps = self._caps.get("sched_ext") or {}
        if scx_caps.get("available") and scx_caps.get("schedulers"):
            # Short name -> label. The one-liners matter: "scx_lavd" tells a
            # player nothing, and picking a kernel scheduler off a bare list is
            # exactly the kind of choice this app exists to make legible.
            blurbs = {
                "lavd": _("latency-first, built for games"),
                "bpfland": _("prioritises interactive tasks"),
                "flash": _("low-latency, deadline based"),
                "rusty": _("general purpose, multi-domain"),
                "rustland": _("general purpose, userspace"),
                "cosmos": _("hybrid, tuned for mixed loads"),
                "p2dq": _("balanced throughput and latency"),
                "tickless": _("fewer timer interrupts"),
                "flow": _("throughput oriented"),
                "cake": _("desktop responsiveness"),
                "beerland": _("simple round-robin"),
                "forge": _("experimental"),
                "pandemonium": _("experimental"),
                "chaos": _("stress-testing, not for daily use"),
                "layered": _("configurable layers, needs setup"),
            }
            scx_opts = [("", _("Off — leave the kernel scheduler alone"))]
            scx_opts += [(n, f"scx_{n} — {blurbs[n]}" if n in blurbs else f"scx_{n}")
                         for n in scx_caps["schedulers"]]
            scx = Adw.ComboRow(title=_("CPU scheduler (sched_ext)"))
            scx.set_subtitle(_("Swaps the kernel's whole CPU scheduler while this "
                               "game runs and puts it back on exit. System-wide "
                               "while it's active, and the first switch each "
                               "session asks for your password"))
            scx.set_model(Gtk.StringList.new([label for _k, label in scx_opts]))
            scx_keys = [k for k, _l in scx_opts]
            cur_scx = p.get("scx_scheduler", "")
            scx.set_selected(scx_keys.index(cur_scx) if cur_scx in scx_keys else 0)
            scx.connect("notify::selected",
                        lambda r, _p: self._patch(
                            exe, scx_scheduler=scx_keys[r.get_selected()]))
            scx.set_title_lines(0)
            exp.add_row(scx)

        exp.add_row(self._switch_row(
            _("CPU governor boost"), p.get("governor_boost", True),
            lambda v: self._patch(exe, governor_boost=v), help="governor",
        ))
        exp.add_row(self._switch_row(
            _("Compositor: allow tearing"), p.get("tearing_enabled", True),
            lambda v: self._patch(exe, tearing_enabled=v), help="tearing",
        ))
        exp.add_row(self._switch_row(
            _("Compositor: adaptive sync (VRR)"), p.get("adaptive_sync_enabled", False),
            lambda v: self._patch(exe, adaptive_sync_enabled=v), help="vrr",
        ))
        focus = self._switch_row(
            _("Focus mode"), p.get("focus_mode", False),
            lambda v: self._patch(exe, focus_mode=v), help="focus",
        )
        focus.set_subtitle(_("Pause the file indexer, turn on Do Not Disturb, inhibit idle"))
        exp.add_row(focus)

        # -- Power limit / TDP nested expander --
        rapl_ok = self._caps.get("rapl_control", True)
        tdp_backend = self._caps.get("tdp_control")  # "rapl" | "ryzenadj" | None
        tdp_ok = rapl_ok or tdp_backend == "ryzenadj"
        amd = tdp_backend == "ryzenadj" and not rapl_ok
        pw = Adw.ExpanderRow(
            title=_("TDP / power limit") if amd else _("CPU power limit"),
            subtitle=(
                _("Experimental — sets the wattage the APU may sustain, via "
                  "ryzenadj. Check it works on your machine with "
                  "`goblin-mode-pro-cli selftest --apply`")
                if amd else
                _("Let the CPU draw more watts to hold its speed under load")
            ) if tdp_ok else _("Not available on this processor"),
        )
        pw.set_sensitive(tdp_ok)
        if tdp_ok:
            pw.set_show_enable_switch(True)
            pw.set_enable_expansion(bool(p.get("power_limit_enabled", False)))
            pw.connect(
                "notify::enable-expansion",
                lambda r, _p: (not self._building) and self._patch(exe, power_limit_enabled=r.get_enable_expansion()),
            )
            # Preset selector — sets both limits at once.
            presets = [(_("Custom"), 0), ("15 W", 15), ("25 W", 25),
                       ("35 W", 35), ("45 W", 45), ("65 W", 65)]
            combo = Adw.ComboRow(title=_("Preset"))
            combo.set_model(Gtk.StringList.new([lbl for lbl, _w in presets]))
            cur_w = p.get("pl1_w", 0)
            combo.set_selected(next((i for i, (_l, w) in enumerate(presets) if w == cur_w), 0))

            pl1 = Adw.SpinRow.new_with_range(0, 250, 1)
            pl1.set_title(_("Sustained (watts)"))
            pl1.set_subtitle(_("0 leaves the factory value"))
            pl1.set_value(cur_w)
            pl2 = Adw.SpinRow.new_with_range(0, 250, 1)
            pl2.set_title(_("Short burst (watts)"))
            pl2.set_value(p.get("pl2_w", 0))

            def _apply_preset(row, _p, _pl1=pl1, _pl2=pl2, _ps=presets):
                w = _ps[row.get_selected()][1]
                if w:
                    _pl1.set_value(w)
                    _pl2.set_value(min(250, w + 15))
            combo.connect("notify::selected", _apply_preset)
            pl1.connect("notify::value", lambda r, _p: self._patch(exe, pl1_w=int(r.get_value())))
            pl2.connect("notify::value", lambda r, _p: self._patch(exe, pl2_w=int(r.get_value())))
            pw.add_row(combo)
            pw.add_row(pl1)
            if not amd:  # ryzenadj takes a single sustained figure
                pw.add_row(pl2)
        if self._caps.get("undervolt") == "intel-undervolt":
            uv = self._switch_row(
                _("Re-apply my undervolt on launch"),
                p.get("undervolt_reapply", False),
                lambda v: self._patch(exe, undervolt_reapply=v))
            uv.set_subtitle(_("Runs `intel-undervolt apply` — uses the offsets you set "
                            "in /etc/intel-undervolt.conf, never ours"))
            uv.set_title_lines(0)
            pw.add_row(uv) if pw.get_sensitive() else exp.add_row(uv)
        if self._caps.get("amd_undervolt") == "ryzenadj":
            auv = self._switch_row_confirmed(
                _("Re-apply my Curve Optimizer offsets on launch"),
                p.get("amd_undervolt_reapply", False),
                lambda v: self._patch(exe, amd_undervolt_reapply=v),
                warning=_(
                    "This re-applies the Curve Optimizer offsets from "
                    "/etc/goblin-mode-pro/amd-undervolt.conf on launch — a file "
                    "you write yourself. Goblin Mode Pro never picks these "
                    "values. An aggressive undervolt can cause instability or "
                    "crashes; if that happens, edit or delete that file.\n\n"
                    "Experimental: this path has not been verified on real AMD "
                    "hardware. Check it works on your machine with "
                    "`goblin-mode-pro-cli selftest --apply`."))
            auv.set_subtitle(_("Experimental — uses the offsets in "
                               "/etc/goblin-mode-pro/amd-undervolt.conf, never ours"))
            auv.set_title_lines(0)
            pw.add_row(auv) if pw.get_sensitive() else exp.add_row(auv)
        exp.add_row(pw)

        if self._caps.get("fan_control"):
            fan = self._switch_row_confirmed(
                _("Spin up the fans on launch"),
                p.get("fan_spinup_enabled", False),
                lambda v: self._patch(exe, fan_spinup_enabled=v),
                warning=_(
                    "Forces every controllable fan to a manual, high-speed duty "
                    "cycle when this game launches, and restores the previous "
                    "setting on exit. Fan behaviour varies a lot between "
                    "machines — if a fan behaves oddly, disable this and file "
                    "an issue."))
            fan.set_subtitle(_("Gets ahead of thermal throttling instead of "
                               "reacting to it. Most laptops let the EC own the "
                               "fan curve — `goblin-mode-pro-cli selftest "
                               "--apply` tells you whether yours does"))
            fan.set_title_lines(0)
            exp.add_row(fan)

        # -- MangoHud nested expander --
        mh = Adw.ExpanderRow(
            title=_("MangoHud configurator"),
            subtitle=_("Changes apply on the next launch — mid-game, use the in-game keys below"),
        )
        mango = dict(p.get("mangohud", {}))
        for key in MANGOHUD_TOGGLES:
            mh.add_row(self._switch_row(
                _MANGOHUD_LABELS.get(key, key), bool(mango.get(key)),
                lambda v, k=key: self._patch_mangohud(exe, k, v),
            ))
        per_game = self._switch_row(
            _("Use a per-game MangoHud.conf"), p.get("per_game_mangohud", False),
            lambda v: self._patch(exe, per_game_mangohud=v),
        )
        mh.add_row(per_game)
        wd = self._switch_row(
            _("Frame-rate watchdog"), p.get("fps_watchdog", False),
            lambda v: self._patch(exe, fps_watchdog=v), help="watchdog",
        )
        wd.set_subtitle(_("Log FPS via MangoHud; raise an incident with GPU state on an extreme dip"))
        mh.add_row(wd)
        if self._caps.get("session_recorder") == "gpu-screen-recorder":
            clip = self._switch_row(
                _("Auto-clip a problem"), p.get("clip_on_incident", False),
                lambda v: self._patch(exe, clip_on_incident=v))
            clip.set_subtitle(_("Keep a 30 s replay buffer; save it when the watchdog "
                              "fires or a GPU fault appears (→ ~/Videos)"))
            clip.set_title_lines(0)
            mh.add_row(clip)
        floor = Adw.SpinRow.new_with_range(5, 120, 1)
        floor.set_title(_("Dip threshold (fps)"))
        floor.set_value(p.get("fps_dip_floor", 22))
        floor.connect("notify::value", lambda r, _p: self._patch(exe, fps_dip_floor=int(r.get_value())))
        mh.add_row(floor)
        keys = Adw.ActionRow(
            title=_("In-game keys"),
            subtitle=_("Shift_R+F12 hide/show · Shift_L+F2 log on/off · Shift_L+F4 reload"),
        )
        keys.add_css_class("dim-label")
        mh.add_row(keys)
        exp.add_row(mh)

        # -- Runner variables nested expander --
        rv = Adw.ExpanderRow(title=_("Runner variables (Proton/Wine)"))
        runner_vars = dict(p.get("runner_vars", {}))
        for key in RUNNER_VARS:
            rv.add_row(self._switch_row(
                _RUNNER_LABELS.get(key, key), bool(runner_vars.get(key)),
                lambda v, k=key: self._patch_runner(exe, k, v),
            ))
        exp.add_row(rv)

        # -- gamescope nested expander --
        gs_ok = self._caps.get("gamescope", True)
        gs = Adw.ExpanderRow(
            title="gamescope",
            subtitle=_("A solid frame limiter, FSR/NIS upscaling and clean alt-tab")
            if gs_ok else _("gamescope is not installed"),
        )
        gcfg = dict(p.get("gamescope", {}))
        gs.set_sensitive(gs_ok)
        if gs_ok:
            gs.set_show_enable_switch(True)
            gs.set_enable_expansion(bool(p.get("gamescope_enabled", False)))
            gs.connect("notify::enable-expansion",
                       lambda r, _p: (not self._building) and self._patch(exe, gamescope_enabled=r.get_enable_expansion()))
            for k, title, lo, hi, step in (("w", _("Width (0 = auto)"), 0, 7680, 10),
                                           ("h", _("Height (0 = auto)"), 0, 4320, 10),
                                           ("refresh", _("Refresh / FPS cap (0 = off)"), 0, 360, 1)):
                sr = Adw.SpinRow.new_with_range(lo, hi, step)
                sr.set_title(title)
                sr.set_value(gcfg.get(k, 0) or 0)
                sr.connect("notify::value", lambda r, _p, kk=k: self._patch_gamescope(exe, kk, int(r.get_value())))
                gs.add_row(sr)
            up = Adw.ComboRow(title=_("Upscaling"))
            up.set_model(Gtk.StringList.new([_("off"), "FSR", "NIS", _("integer")]))
            up.set_selected({"off": 0, "fsr": 1, "nis": 2, "integer": 3}.get(gcfg.get("upscale", "off"), 0))
            up.connect("notify::selected", lambda r, _p: self._patch_gamescope(
                exe, "upscale", ["off", "fsr", "nis", "integer"][r.get_selected()]))
            gs.add_row(up)
            gs.add_row(self._switch_row(_("HDR (needs an HDR display)"), gcfg.get("hdr", False),
                                        lambda v: self._patch_gamescope(exe, "hdr", v)))
        exp.add_row(gs)

        # -- GPU driver tuning (vendor-specific) --
        vendors = [v for v in (self._caps.get("gpu_vendors") or []) if v in GPU_TUNING_VARS]
        if vendors:
            gt = Adw.ExpanderRow(
                title=_("GPU driver tuning"),
                subtitle="Extra " + " / ".join(v.upper() if v == "amd" else v.title()
                                               for v in vendors) + " driver knobs",
            )
            tuning = dict(p.get("gpu_tuning", {}))
            for vendor in vendors:
                for key, (label, _env) in GPU_TUNING_VARS[vendor].items():
                    gt.add_row(self._switch_row(
                        label, bool(tuning.get(key)),
                        lambda v, k=key: self._patch_dict(exe, "gpu_tuning", k, v),
                    ))
            exp.add_row(gt)

        # -- Compatibility: Steam AppID + ProtonDB / anti-cheat --
        comp = Adw.ExpanderRow(
            title=_("Compatibility check"),
            subtitle=_("ProtonDB rating and anti-cheat status for this game"),
        )
        appid = Adw.EntryRow(title=_("Steam AppID (optional)"))
        appid.set_text(str(p.get("steam_app_id", "")))
        appid.connect("changed", lambda r: self._patch(
            exe, steam_app_id="".join(c for c in r.get_text() if c.isdigit())[:12]))
        comp.add_row(appid)
        check = button_row(_("Check this game"), "system-search-symbolic")
        result = Adw.ActionRow(visible=False)
        result.set_css_classes(["dim-label"])
        result.set_title_lines(0)
        check.connect("activated", lambda _r, e=exe, rr=result: self._on_compat_check(e, rr))
        comp.add_row(check)
        comp.add_row(result)
        share = button_row(_("Share what worked"), "send-to-symbolic")
        share.connect("activated", lambda _r, e=exe: self._actions.share(e))
        comp.add_row(share)
        exp.add_row(comp)

        return exp

    def _switch_row(self, title: str, active: bool, on_change, help: str = "") -> Adw.SwitchRow:
        row = Adw.SwitchRow(title=title, active=active)
        if help:
            hb = help_button(help)
            if hb is not None:
                row.add_suffix(hb)
        row.connect("notify::active",
                    lambda r, _p: (not self._building) and on_change(r.get_active()))
        return row

    def _switch_row_confirmed(self, title: str, active: bool, on_change,
                              warning: str, help: str = "") -> Adw.SwitchRow:
        """Like _switch_row, but turning it *on* first shows an "I understand
        the risk" confirm dialog - cancelling snaps the switch back off
        without calling on_change. Turning it off never needs confirming."""
        row = Adw.SwitchRow(title=title, active=active)
        if help:
            hb = help_button(help)
            if hb is not None:
                row.add_suffix(hb)

        def _toggled(s, _p):
            if self._building:
                return
            if not s.get_active():
                on_change(False)
                return
            win = self.get_root()
            d = Adw.AlertDialog(
                heading=_("Are you sure?"),
                body=warning,
            )
            d.add_response("cancel", _("Cancel"))
            d.add_response("enable", _("I understand, enable it"))
            d.set_response_appearance("enable", Adw.ResponseAppearance.DESTRUCTIVE)

            def _respond(_d, response):
                if response == "enable":
                    on_change(True)
                else:
                    self._building = True
                    s.set_active(False)
                    self._building = False

            d.connect("response", _respond)
            d.present(win)

        row.connect("notify::active", _toggled)
        return row

    def patch(self, **changes: Any) -> None:
        """Change fields on this profile and persist it.

        The public form of _patch, for the page: the expander's built-in
        enable switch belongs to the row but is handled by the list (it is
        also what remove/keep/ignore act on), so the page needs a way in.
        """
        self._patch(self.exe, **changes)

    # -- persistence -------------------------------------------------
    # Each of these mutates this row's profile and hands the whole thing back
    # to the page to persist. The `exe` argument is redundant now that an
    # editor owns exactly one game, but it is kept so the widget callbacks
    # above read unchanged - and so a stale closure can never write to the
    # wrong profile, because the assertion below would catch it.
    def _patch(self, exe: str, **changes) -> None:
        if self._building:
            return
        self.profile.update(changes)
        self._commit(exe)

    def _patch_mangohud(self, exe: str, key: str, value: bool) -> None:
        self._patch_dict(exe, "mangohud", key, value)

    def _patch_runner(self, exe: str, key: str, value: bool) -> None:
        self._patch_dict(exe, "runner_vars", key, value)

    def _patch_gamescope(self, exe: str, key: str, value: Any) -> None:
        self._patch_dict(exe, "gamescope", key, value)

    def _patch_dict(self, exe: str, field: str, key: str, value: Any) -> None:
        if self._building:
            return
        d = dict(self.profile.get(field, {}))
        d[key] = value
        self.profile[field] = d
        self._commit(exe)

    def _commit(self, exe: str) -> None:
        assert exe == self.exe, f"editor for {self.exe} asked to write {exe}"
        self._actions.save(dict(self.profile))

    # -- compatibility check (ProtonDB + anti-cheat) --------------
    def _on_compat_check(self, exe: str, result_row: Adw.ActionRow) -> None:
        p = self.profile
        app_id = p.get("steam_app_id", "")
        name = p.get("display_name") or exe
        result_row.set_visible(True)
        result_row.set_title(_("Checking…"))

        def work() -> None:
            from goblinmode import webdata
            lines = []
            try:
                if app_id:
                    t = webdata.protondb_tier(app_id)
                    lines.append(f"ProtonDB: {str(t.get('tier','?')).title()} "
                                 f"({t.get('total', 0)} reports, "
                                 f"{t.get('confidence', '?')} confidence)")
                else:
                    lines.append(_("ProtonDB: add the Steam AppID above to check"))
            except Exception as exc:  # noqa: BLE001
                lines.append(f"ProtonDB: {exc}")
            try:
                ac = webdata.anticheat_status(name=name, app_id=app_id)
                if ac:
                    lines.append(f"Anti-cheat: {ac['status']}"
                                 + (f" — {', '.join(ac['anticheats'])}" if ac['anticheats'] else ""))
                else:
                    lines.append(_("Anti-cheat: not listed (likely none, or works fine)"))
            except Exception as exc:  # noqa: BLE001
                lines.append(f"Anti-cheat: {exc}")
            GLib.idle_add(lambda: result_row.set_title("\n".join(lines)) or False)

        threading.Thread(target=work, name="gmp-compat", daemon=True).start()
