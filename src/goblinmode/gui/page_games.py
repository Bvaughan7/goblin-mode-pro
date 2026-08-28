"""Games (Library) page - per-game profiles.

One ``Adw.ExpanderRow`` per game. The row's built-in enable switch toggles the
profile; simple tweaks are ``Adw.ActionRow`` + ``Gtk.Switch``; the MangoHud
configurator and runner variables live in nested ``Adw.ExpanderRow``s to keep
the surface clean (per the brief).
"""

from __future__ import annotations

import logging
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from goblinmode.config import MANGOHUD_TOGGLES, RUNNER_VARS
from goblinmode.ipc.daemon_bridge import BridgeClient
from goblinmode.runner import LAUNCH_OPTION

log = logging.getLogger(__name__)

_MANGOHUD_LABELS = {
    "enabled": "Show overlay",
    "fps": "FPS counter",
    "cpu_temp": "CPU temperature",
    "gpu_temp": "GPU temperature",
    "ram": "RAM usage",
    "frame_timing": "Frame-timing graph",
}
_RUNNER_LABELS = {
    "nvapi": "NVAPI (PROTON_ENABLE_NVAPI + DXVK_ENABLE_NVAPI)",
    "fsync": "Force Fsync (WINEFSYNC=1)",
    "no_esync": "Disable Esync (PROTON_NO_ESYNC=1)",
    "dxvk_async": "Async shader compile (DXVK_ASYNC=1)",
}


class GamesPage(Adw.PreferencesPage):
    def __init__(self, bridge: BridgeClient) -> None:
        super().__init__(title="Games", icon_name="applications-games-symbolic")
        self.bridge = bridge
        self._profiles: dict[str, dict[str, Any]] = {}
        self._building = False
        self._master_enabled = True

        info = Adw.PreferencesGroup(
            title="Steam launch option",
            description=(
                "For Proton games, set the game's launch options to the string "
                "below so Goblin Mode Pro can inject runner variables and capture "
                "the Wine/Proton log."
            ),
        )
        row = Adw.ActionRow(title=LAUNCH_OPTION, subtitle="Right-click → copy")
        row.add_css_class("monospace")
        copy = Gtk.Button(icon_name="edit-copy-symbolic", valign=Gtk.Align.CENTER)
        copy.add_css_class("flat")
        copy.connect("clicked", self._copy_launch_option)
        row.add_suffix(copy)
        info.add(row)
        self.add(info)

        auto = Adw.PreferencesGroup()
        self._auto_row = Adw.SwitchRow(
            title="Auto-detect games",
            subtitle="Optimize any game GMP recognises — Steam / Lutris / Heroic, "
            "or anything doing sustained GPU work — not just the profiles below.",
        )
        self._auto_row.connect(
            "notify::active",
            lambda r, _p: (not self._building) and self._set_auto(r.get_active()),
        )
        auto.add(self._auto_row)
        self.add(auto)

        self._group = Adw.PreferencesGroup(title="Game profiles")
        add_btn = Gtk.Button(icon_name="list-add-symbolic", valign=Gtk.Align.CENTER)
        add_btn.add_css_class("flat")
        add_btn.set_tooltip_text("Add game executable")
        add_btn.connect("clicked", self._on_add_game)
        self._group.set_header_suffix(add_btn)
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
                title="No games yet",
                subtitle="Use the + button to add a game executable",
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
            + ("  ·  auto-detected" if auto else ""),
        )
        exp.set_show_enable_switch(True)
        exp.set_enable_expansion(bool(p.get("enabled", True)))
        exp.connect("notify::enable-expansion", self._on_enable_toggled, exe)

        if auto:
            pill = Gtk.Label(label="AUTO")
            pill.add_css_class("caption-heading")
            pill.add_css_class("accent")
            pill.set_valign(Gtk.Align.CENTER)
            exp.add_suffix(pill)
            keep = Gtk.Button(label="Keep", valign=Gtk.Align.CENTER)
            keep.add_css_class("flat")
            keep.connect("clicked", lambda _b: self._keep(exe))
            exp.add_suffix(keep)
            ignore = Gtk.Button(label="Ignore", valign=Gtk.Align.CENTER)
            ignore.add_css_class("flat")
            ignore.connect("clicked", lambda _b: self._ignore(exe))
            exp.add_suffix(ignore)

        remove = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER)
        remove.add_css_class("flat")
        remove.connect("clicked", self._on_remove, exe)
        exp.add_suffix(remove)

        # -- simple toggles --
        exp.add_row(self._switch_row(
            "Process priority (renice)", p.get("renice_enabled", True),
            lambda v: self._patch(exe, renice_enabled=v),
        ))
        nice = Adw.SpinRow.new_with_range(-10, 19, 1)
        nice.set_title("Nice value")
        nice.set_subtitle("Lower = higher priority (needs the helper)")
        nice.set_value(p.get("nice_value", -5))
        nice.connect("notify::value", lambda r, _p: self._patch(exe, nice_value=int(r.get_value())))
        exp.add_row(nice)

        exp.add_row(self._switch_row(
            "CPU governor boost", p.get("governor_boost", True),
            lambda v: self._patch(exe, governor_boost=v),
        ))
        exp.add_row(self._switch_row(
            "Compositor: allow tearing", p.get("tearing_enabled", True),
            lambda v: self._patch(exe, tearing_enabled=v),
        ))
        exp.add_row(self._switch_row(
            "Compositor: adaptive sync (VRR)", p.get("adaptive_sync_enabled", False),
            lambda v: self._patch(exe, adaptive_sync_enabled=v),
        ))
        focus = self._switch_row(
            "Focus mode", p.get("focus_mode", False),
            lambda v: self._patch(exe, focus_mode=v),
        )
        focus.set_subtitle("Pause the file indexer, turn on Do Not Disturb, inhibit idle")
        exp.add_row(focus)

        # -- Power limit nested expander (needs the helper) --
        pw = Adw.ExpanderRow(
            title="CPU power limits (RAPL)",
            subtitle="Raise PL1/PL2 to fight Dell G7 downclocking — 0 keeps firmware default",
        )
        pw.set_show_enable_switch(True)
        pw.set_enable_expansion(bool(p.get("power_limit_enabled", False)))
        pw.connect(
            "notify::enable-expansion",
            lambda r, _p: (not self._building) and self._patch(exe, power_limit_enabled=r.get_enable_expansion()),
        )
        pl1 = Adw.SpinRow.new_with_range(0, 500, 5)
        pl1.set_title("PL1 — sustained (W)")
        pl1.set_value(p.get("pl1_w", 0))
        pl1.connect("notify::value", lambda r, _p: self._patch(exe, pl1_w=int(r.get_value())))
        pl2 = Adw.SpinRow.new_with_range(0, 500, 5)
        pl2.set_title("PL2 — burst (W)")
        pl2.set_value(p.get("pl2_w", 0))
        pl2.connect("notify::value", lambda r, _p: self._patch(exe, pl2_w=int(r.get_value())))
        pw.add_row(pl1)
        pw.add_row(pl2)
        exp.add_row(pw)

        # -- MangoHud nested expander --
        mh = Adw.ExpanderRow(
            title="MangoHud configurator",
            subtitle="Changes apply on the next launch — mid-game, use the in-game keys below",
        )
        mango = dict(p.get("mangohud", {}))
        for key in MANGOHUD_TOGGLES:
            mh.add_row(self._switch_row(
                _MANGOHUD_LABELS.get(key, key), bool(mango.get(key)),
                lambda v, k=key: self._patch_mangohud(exe, k, v),
            ))
        per_game = self._switch_row(
            "Use a per-game MangoHud.conf", p.get("per_game_mangohud", False),
            lambda v: self._patch(exe, per_game_mangohud=v),
        )
        mh.add_row(per_game)
        wd = self._switch_row(
            "Frame-rate watchdog", p.get("fps_watchdog", False),
            lambda v: self._patch(exe, fps_watchdog=v),
        )
        wd.set_subtitle("Log FPS via MangoHud; raise an incident with GPU state on an extreme dip")
        mh.add_row(wd)
        floor = Adw.SpinRow.new_with_range(5, 120, 1)
        floor.set_title("Dip threshold (fps)")
        floor.set_value(p.get("fps_dip_floor", 22))
        floor.connect("notify::value", lambda r, _p: self._patch(exe, fps_dip_floor=int(r.get_value())))
        mh.add_row(floor)
        keys = Adw.ActionRow(
            title="In-game keys",
            subtitle="Shift_R+F12 hide/show · Shift_L+F2 log on/off · Shift_L+F4 reload",
        )
        keys.add_css_class("dim-label")
        mh.add_row(keys)
        exp.add_row(mh)

        # -- Runner variables nested expander --
        rv = Adw.ExpanderRow(title="Runner variables (Proton/Wine)")
        runner_vars = dict(p.get("runner_vars", {}))
        for key in RUNNER_VARS:
            rv.add_row(self._switch_row(
                _RUNNER_LABELS.get(key, key), bool(runner_vars.get(key)),
                lambda v, k=key: self._patch_runner(exe, k, v),
            ))
        exp.add_row(rv)

        self._group.add(exp)
        return exp

    # -- small helpers -------------------------------------------
    def _switch_row(self, title: str, active: bool, on_change) -> Adw.ActionRow:
        row = Adw.ActionRow(title=title)
        sw = Gtk.Switch(valign=Gtk.Align.CENTER, active=active)
        sw.connect("notify::active", lambda s, _p: (not self._building) and on_change(s.get_active()))
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
            heading="Remove game profile?",
            body=f"“{exe}” will no longer be optimised.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("remove", "Remove")
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
        dialog = Gtk.FileDialog(title="Select game executable")
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

    def _copy_launch_option(self, _btn: Gtk.Button) -> None:
        clip = self.get_clipboard()
        clip.set(LAUNCH_OPTION)
        root = self.get_root()
        if hasattr(root, "toast"):
            root.toast("Launch option copied")
