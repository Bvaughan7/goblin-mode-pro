"""Games (Library) page - per-game profiles.

One ``Adw.ExpanderRow`` per game. The row's built-in enable switch toggles the
profile; simple tweaks are ``Adw.ActionRow`` + ``Gtk.Switch``; the MangoHud
configurator and runner variables live in nested ``Adw.ExpanderRow``s to keep
the surface clean (per the brief).
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from goblinmode.i18n import _  # noqa: E402

from goblinmode.config import GPU_TUNING_VARS, MANGOHUD_TOGGLES, RUNNER_VARS
from goblinmode.gui.widgets.buttonrow import button_row
from goblinmode.gui.widgets.help import help_button
from goblinmode.ipc.daemon_bridge import BridgeClient
from goblinmode.runner import LAUNCH_OPTION

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


class GamesPage(Adw.PreferencesPage):
    def __init__(self, bridge: BridgeClient) -> None:
        super().__init__(title=_("Games"), icon_name="applications-games-symbolic")
        self.bridge = bridge
        self._profiles: dict[str, dict[str, Any]] = {}
        self._building = False
        self._master_enabled = True
        self._caps: dict[str, Any] = {}

        info = Adw.PreferencesGroup(
            title=_("Steam launch option"),
            description=_(
                "For Proton games, set the game's launch options to the string "
                "below so Goblin Mode Pro can inject runner variables and capture "
                "the Wine/Proton log."
            ),
        )
        row = Adw.ActionRow(title=LAUNCH_OPTION, subtitle=_("Right-click → copy"))
        row.add_css_class("monospace")
        copy = Gtk.Button(icon_name="edit-copy-symbolic", valign=Gtk.Align.CENTER)
        copy.add_css_class("flat")
        copy.connect("clicked", self._copy_launch_option)
        row.add_suffix(copy)
        info.add(row)
        self.add(info)

        auto = Adw.PreferencesGroup()
        self._auto_row = Adw.SwitchRow(
            title=_("Auto-detect games"),
            subtitle=_("Optimize any game GMP recognises — Steam / Lutris / Heroic, "
            "or anything doing sustained GPU work — not just the profiles below."),
        )
        self._auto_row.connect(
            "notify::active",
            lambda r, _p: (not self._building) and self._set_auto(r.get_active()),
        )
        auto.add(self._auto_row)
        self.add(auto)

        self._group = Adw.PreferencesGroup(title=_("Game profiles"))
        hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        community_btn = Gtk.Button(icon_name="folder-download-symbolic", valign=Gtk.Align.CENTER)
        community_btn.add_css_class("flat")
        community_btn.set_tooltip_text(_("Browse community profiles"))
        community_btn.connect("clicked", self._on_community)
        hdr.append(community_btn)
        import_btn = Gtk.Button(icon_name="document-open-symbolic", valign=Gtk.Align.CENTER)
        import_btn.add_css_class("flat")
        import_btn.set_tooltip_text(_("Import a shared profile (.json)"))
        import_btn.connect("clicked", self._on_import)
        hdr.append(import_btn)
        add_btn = Gtk.Button(icon_name="list-add-symbolic", valign=Gtk.Align.CENTER)
        add_btn.add_css_class("flat")
        add_btn.set_tooltip_text(_("Add game executable"))
        add_btn.connect("clicked", self._on_add_game)
        hdr.append(add_btn)
        self._group.set_header_suffix(hdr)
        self.add(self._group)

        self._rows: list[Gtk.Widget] = []

    def _set_auto(self, on: bool) -> None:
        try:
            self.bridge.set_auto_detect(on)
        except Exception as exc:  # noqa: BLE001
            log.warning("set_auto_detect failed: %s", exc)

    def _keep(self, exe: str) -> None:
        try:
            self.bridge.keep_game(exe)
            self._profiles.get(exe, {})["auto_created"] = False
            self._rebuild()
        except Exception as exc:  # noqa: BLE001
            log.warning("keep_game failed: %s", exc)

    def _ignore(self, exe: str) -> None:
        try:
            self.bridge.ignore_game(exe)
            self._profiles.pop(exe, None)
            self._rebuild()
        except Exception as exc:  # noqa: BLE001
            log.warning("ignore_game failed: %s", exc)

    # -- external updates ------------------------------------------
    def update_status(self, status: dict[str, Any]) -> None:
        self._master_enabled = status.get("master_enabled", True)
        caps = status.get("capabilities") or {}
        if caps != self._caps:
            self._caps = caps
            self._profiles = {}  # force a rebuild so capability gating re-applies
        self._building = True
        self._auto_row.set_active(status.get("auto_detect", True))
        self._building = False
        if status.get("profiles"):
            self.load_profiles(status["profiles"])

    def load_profiles(self, profiles: list[dict[str, Any]]) -> None:
        new = {p["exe"]: p for p in profiles if p.get("exe") != "__forced__"}
        if new == self._profiles:
            return
        self._profiles = new
        self._rebuild()

    # -- rebuild ---------------------------------------------------
    def _rebuild(self) -> None:
        self._building = True
        for row in self._rows:
            self._group.remove(row)
        self._rows.clear()

        if not self._profiles:
            empty = Adw.ActionRow(
                title=_("No games yet"),
                subtitle=_("Use the + button to add a game executable"),
            )
            self._group.add(empty)
            self._rows.append(empty)
            self._building = False
            return

        for exe, profile in sorted(self._profiles.items()):
            self._rows.append(self._build_profile_row(exe, profile))
        self._building = False

    def _build_profile_row(self, exe: str, p: dict[str, Any]) -> Adw.ExpanderRow:
        auto = p.get("auto_created", False)
        exp = Adw.ExpanderRow(
            title=p.get("display_name") or exe,
            subtitle=f"{exe}  ·  match: {p.get('match_mode', 'exact')}"
            + (_("  ·  auto-detected") if auto else ""),
        )
        exp.set_show_enable_switch(True)
        exp.set_enable_expansion(bool(p.get("enabled", True)))
        exp.connect("notify::enable-expansion", self._on_enable_toggled, exe)

        if auto:
            pill = Gtk.Label(label=_("AUTO"))
            pill.add_css_class("caption-heading")
            pill.add_css_class("accent")
            pill.set_valign(Gtk.Align.CENTER)
            exp.add_suffix(pill)
            keep = Gtk.Button(label=_("Keep"), valign=Gtk.Align.CENTER)
            keep.add_css_class("flat")
            keep.connect("clicked", lambda _b: self._keep(exe))
            exp.add_suffix(keep)
            ignore = Gtk.Button(label=_("Ignore"), valign=Gtk.Align.CENTER)
            ignore.add_css_class("flat")
            ignore.connect("clicked", lambda _b: self._ignore(exe))
            exp.add_suffix(ignore)

        share = Gtk.Button(icon_name="send-to-symbolic", valign=Gtk.Align.CENTER)
        share.add_css_class("flat")
        share.set_tooltip_text(_("Export this profile to share it"))
        share.connect("clicked", lambda _b: self._on_export(exe))
        exp.add_suffix(share)

        remove = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER)
        remove.add_css_class("flat")
        remove.connect("clicked", self._on_remove, exe)
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
                _("Set the wattage the APU may sustain — via ryzenadj (experimental)")
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
                    "crashes; if that happens, edit or delete that file."))
            auv.set_subtitle(_("Uses the offsets in /etc/goblin-mode-pro/amd-undervolt.conf, "
                              "never ours"))
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
                    "setting on exit. This is best-effort and unverified across "
                    "hardware — if a fan behaves oddly, disable this and file an "
                    "issue."))
            fan.set_subtitle(_("Gets ahead of thermal throttling instead of reacting to it"))
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
        share.connect("activated", lambda _r, e=exe: self._on_share_works_for_me(e))
        comp.add_row(share)
        exp.add_row(comp)

        self._group.add(exp)
        return exp

    # -- small helpers -------------------------------------------
    def _switch_row(self, title: str, active: bool, on_change, help: str = "") -> Adw.ActionRow:
        row = Adw.ActionRow(title=title)
        if help:
            hb = help_button(help)
            if hb is not None:
                row.add_suffix(hb)
        sw = Gtk.Switch(valign=Gtk.Align.CENTER, active=active)
        sw.connect("notify::active", lambda s, _p: (not self._building) and on_change(s.get_active()))
        row.add_suffix(sw)
        row.set_activatable_widget(sw)
        return row

    def _switch_row_confirmed(self, title: str, active: bool, on_change,
                              warning: str, help: str = "") -> Adw.ActionRow:
        """Like _switch_row, but turning it *on* first shows an "I understand
        the risk" confirm dialog - cancelling snaps the switch back off
        without calling on_change. Turning it off never needs confirming."""
        row = Adw.ActionRow(title=title)
        if help:
            hb = help_button(help)
            if hb is not None:
                row.add_suffix(hb)
        sw = Gtk.Switch(valign=Gtk.Align.CENTER, active=active)

        def _toggled(s, _p):
            if self._building:
                return
            if not s.get_active():
                on_change(False)
                return
            win = self.get_root()
            d = Adw.MessageDialog(
                transient_for=win,
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
            d.present()

        sw.connect("notify::active", _toggled)
        row.add_suffix(sw)
        row.set_activatable_widget(sw)
        return row

    def _patch(self, exe: str, **changes) -> None:
        if self._building:
            return
        p = dict(self._profiles.get(exe, {}))
        p.update(changes)
        self._profiles[exe] = p
        self._save(p)

    def _patch_mangohud(self, exe: str, key: str, value: bool) -> None:
        if self._building:
            return
        p = dict(self._profiles.get(exe, {}))
        mango = dict(p.get("mangohud", {}))
        mango[key] = value
        p["mangohud"] = mango
        self._profiles[exe] = p
        self._save(p)

    def _patch_runner(self, exe: str, key: str, value: bool) -> None:
        if self._building:
            return
        p = dict(self._profiles.get(exe, {}))
        rv = dict(p.get("runner_vars", {}))
        rv[key] = value
        p["runner_vars"] = rv
        self._profiles[exe] = p
        self._save(p)

    def _patch_gamescope(self, exe: str, key: str, value: Any) -> None:
        self._patch_dict(exe, "gamescope", key, value)

    def _patch_dict(self, exe: str, field: str, key: str, value: Any) -> None:
        if self._building:
            return
        p = dict(self._profiles.get(exe, {}))
        d = dict(p.get(field, {}))
        d[key] = value
        p[field] = d
        self._profiles[exe] = p
        self._save(p)

    # -- compatibility check (ProtonDB + anti-cheat) --------------
    def _on_compat_check(self, exe: str, result_row: Adw.ActionRow) -> None:
        p = self._profiles.get(exe, {})
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

    # -- telemetry-free "works for me" report ----------------------
    def _on_share_works_for_me(self, exe: str) -> None:
        d = Adw.MessageDialog(
            transient_for=self.get_root(),
            heading=_("Share what worked"),
            body=_(
                "Opens a pre-filled GitHub issue with your system info and this "
                "game's tuning settings (no undervolt/fan-control values, no "
                "usernames or paths) — nothing is sent anywhere until you post it "
                "yourself. Add a note if you like:"),
        )
        entry = Gtk.Entry(placeholder_text=_("e.g. rock solid after enabling DXVK async"))
        d.set_extra_child(entry)
        d.add_response("cancel", _("Cancel"))
        d.add_response("share", _("Open the issue form"))
        d.set_response_appearance("share", Adw.ResponseAppearance.SUGGESTED)
        d.connect("response", self._works_for_me_response, exe, entry)
        d.present()

    def _works_for_me_response(self, _d, response, exe: str, entry: Gtk.Entry) -> None:
        if response != "share":
            return
        note = entry.get_text()
        self.bridge.build_works_for_me_async(exe, note, self._works_for_me_ready)

    def _works_for_me_ready(self, result, err) -> None:
        win = self.get_root()
        if err is not None or not result:
            if hasattr(win, "toast"):
                win.toast(_("Couldn't build the report: {err}").format(err=err))
            return
        Gio.AppInfo.launch_default_for_uri(result["url"], None)

    def _save(self, profile: dict[str, Any]) -> None:
        try:
            self.bridge.set_profile(profile)
        except Exception as exc:  # noqa: BLE001
            log.warning("set_profile failed: %s", exc)

    # -- signal handlers ---------------------------------------
    def _on_enable_toggled(self, exp: Adw.ExpanderRow, _param, exe: str) -> None:
        if self._building:
            return
        self._patch(exe, enabled=exp.get_enable_expansion())

    def _on_remove(self, _btn: Gtk.Button, exe: str) -> None:
        dialog = Adw.MessageDialog(
            transient_for=self.get_root(),
            heading=_("Remove game profile?"),
            body=f"“{exe}” will no longer be optimised.",
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("remove", _("Remove"))
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self._on_remove_response, exe)
        dialog.present()

    def _on_remove_response(self, _dialog, response: str, exe: str) -> None:
        if response == "remove":
            try:
                self.bridge.remove_profile(exe)
                self._profiles.pop(exe, None)
                self._rebuild()
            except Exception as exc:  # noqa: BLE001
                log.warning("remove_profile failed: %s", exc)

    def _on_add_game(self, _btn: Gtk.Button) -> None:
        dialog = Gtk.FileDialog(title=_("Select game executable"))
        dialog.open(self.get_root(), None, self._on_file_chosen)

    def _on_file_chosen(self, dialog: Gtk.FileDialog, result) -> None:
        try:
            gfile = dialog.open_finish(result)
        except GLib.Error:
            return
        if not gfile:
            return
        name = gfile.get_basename() or "game"
        profile = {
            "exe": name,
            "display_name": name,
            "enabled": True,
            "match_mode": "exact" if name.lower().endswith(".exe") else "substring",
        }
        self._profiles[name] = profile
        self._save(profile)
        self._rebuild()

    # -- profile sharing (export / import) ---------------------
    _SHARE_KEYS = (
        "match_mode", "renice_enabled", "nice_value", "use_gamemode", "core_pin",
        "gpu_tuning", "steam_app_id", "notes", "tearing_enabled",
        "adaptive_sync_enabled", "governor_boost", "focus_mode",
        "power_limit_enabled", "pl1_w", "pl2_w", "per_game_mangohud", "mangohud",
        "fps_watchdog", "fps_dip_floor", "fps_dip_ratio", "runner_vars",
        "gamescope_enabled", "gamescope",
    )

    def _on_export(self, exe: str) -> None:
        p = self._profiles.get(exe, {})
        payload = {
            "goblin_mode_pro_profile": 1,
            "exe": exe,
            "display_name": p.get("display_name") or exe,
            **{k: p[k] for k in self._SHARE_KEYS if k in p},
        }
        dialog = Gtk.FileDialog(title=_("Export profile"), initial_name=f"{exe}.gmp.json")
        blob = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        dialog.save(self.get_root(), None, self._on_export_chosen, blob)

    def _on_export_chosen(self, dialog: Gtk.FileDialog, result, blob: str) -> None:
        try:
            gfile = dialog.save_finish(result)
        except GLib.Error:
            return
        try:
            gfile.replace_contents(
                blob.encode(), None, False,
                Gio.FileCreateFlags.REPLACE_DESTINATION, None,
            )
        except GLib.Error as exc:
            log.warning("profile export failed: %s", exc)
            return
        root = self.get_root()
        if hasattr(root, "toast"):
            root.toast(_("Profile exported"))

    def _on_import(self, _btn: Gtk.Button) -> None:
        dialog = Gtk.FileDialog(title=_("Import a shared profile"))
        dialog.open(self.get_root(), None, self._on_import_chosen)

    def _on_import_chosen(self, dialog: Gtk.FileDialog, result) -> None:
        try:
            gfile = dialog.open_finish(result)
        except GLib.Error:
            return
        if not gfile:
            return
        try:
            ok, data, _etag = gfile.load_contents(None)
            raw = json.loads(bytes(data)[:65536].decode("utf-8", "replace")) if ok else None
        except (GLib.Error, ValueError) as exc:
            log.warning("profile import: unreadable file: %s", exc)
            self._import_toast(_("Couldn't read that file"))
            return
        if not isinstance(raw, dict) or not raw.get("exe"):
            self._import_toast(_("Not a Goblin Mode Pro profile"))
            return
        # Keep only the shareable fields; the daemon re-validates everything.
        profile = {
            "exe": raw["exe"],
            "display_name": raw.get("display_name") or raw["exe"],
            "enabled": True,
            **{k: raw[k] for k in self._SHARE_KEYS if k in raw},
        }
        if self.bridge.set_profile(profile):
            self._import_toast(f"Imported “{profile['display_name']}”")
        else:
            self._import_toast(_("That profile was rejected as invalid"))

    def _import_toast(self, msg: str) -> None:
        root = self.get_root()
        if hasattr(root, "toast"):
            root.toast(msg)

    # -- community profiles -------------------------------------
    def _on_community(self, _btn: Gtk.Button) -> None:
        self._import_toast(_("Fetching community profiles…"))

        def work() -> None:
            from goblinmode import community
            try:
                index = community.fetch_index()
                err = None
            except Exception as exc:  # noqa: BLE001
                index, err = None, str(exc)
            GLib.idle_add(self._community_index_ready, index, err)

        threading.Thread(target=work, name="gmp-community", daemon=True).start()

    def _community_index_ready(self, index, err) -> bool:
        if err or not index:
            self._import_toast(f"Couldn't reach the community profiles ({err})"
                               if err else _("No community profiles listed"))
            return False
        dialog = Adw.MessageDialog(
            transient_for=self.get_root(),
            heading=_("Community profiles"),
            body=_("Downloaded from the project repo. Applying one overwrites that "
            "game's tweaks (it never touches your other games)."),
        )
        group = Adw.PreferencesGroup()
        for entry in index:
            row = Adw.ActionRow(title=entry["display_name"],
                                subtitle=entry.get("note") or entry["exe"])
            get = Gtk.Button(label=_("Apply"), valign=Gtk.Align.CENTER)
            get.add_css_class("flat")
            get.connect("clicked", lambda _b, e=entry: (dialog.close(), self._fetch_community(e)))
            row.add_suffix(get)
            group.add(row)
        # The list is longer than a dialog: scroll it, and keep the dialog's
        # own Close button reachable at the bottom.
        scroller = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            propagate_natural_height=True,
            max_content_height=420,
            child=group,
        )
        dialog.set_extra_child(scroller)
        dialog.add_response("close", _("Close"))
        dialog.set_default_response("close")
        dialog.set_close_response("close")
        dialog.present()
        return False

    def _fetch_community(self, entry: dict) -> None:
        self._import_toast(f"Fetching “{entry['display_name']}”…")

        def work() -> None:
            from goblinmode import community
            try:
                prof = community.fetch_profile(entry["slug"])
                err = None
            except Exception as exc:  # noqa: BLE001
                prof, err = None, str(exc)
            GLib.idle_add(self._community_profile_ready, prof, err)

        threading.Thread(target=work, name="gmp-community", daemon=True).start()

    def _community_profile_ready(self, prof, err) -> bool:
        if err or not prof:
            self._import_toast(f"Fetch failed ({err})" if err else _("Empty profile"))
            return False
        note = prof.pop("note", "")
        exe = prof.get("exe", "?")
        existing = exe in self._profiles
        d = Adw.MessageDialog(
            transient_for=self.get_root(),
            heading=f"Apply community settings for {prof.get('display_name') or exe}?",
            body=(note + "\n\n" if note else "")
            + (f"This replaces your current tweaks for {exe}."
               if existing else f"This adds a new profile for {exe}."),
        )
        d.add_response("cancel", _("Cancel"))
        d.add_response("apply", _("Apply"))
        d.set_response_appearance("apply", Adw.ResponseAppearance.SUGGESTED)
        d.connect("response", lambda _dd, resp: resp == "apply" and self._apply_community(prof))
        d.present()
        return False

    def _apply_community(self, prof: dict) -> None:
        prof.setdefault("enabled", True)
        if self.bridge.set_profile(prof):
            self._import_toast(f"Applied community settings for {prof.get('exe')}")
        else:
            self._import_toast(_("The daemon rejected that profile"))

    def _copy_launch_option(self, _btn: Gtk.Button) -> None:
        clip = self.get_clipboard()
        clip.set(LAUNCH_OPTION)
        root = self.get_root()
        if hasattr(root, "toast"):
            root.toast(_("Launch option copied"))
