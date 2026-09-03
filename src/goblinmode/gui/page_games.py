"""Games (Library) page - per-game profiles.

One ``Adw.ExpanderRow`` per game. The row's built-in enable switch toggles the
profile; simple tweaks are ``Adw.SwitchRow``; the MangoHud configurator and
runner variables live in nested ``Adw.ExpanderRow``s to keep the surface clean
(per the brief).
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk

from goblinmode.i18n import _

from goblinmode.gui.widgets.profile_editor import EditorActions, ProfileEditor
from goblinmode.ipc.daemon_bridge import BridgeClient
from goblinmode.runner import LAUNCH_OPTION

log = logging.getLogger(__name__)



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

        # Ignoring a game used to be a one-way door: the row vanished and
        # nothing anywhere could bring it back, so a mis-click meant editing
        # config.json by hand. This group is the way back.
        self._ignored_group = Adw.PreferencesGroup(
            title=_("Ignored games"),
            description=_("GMP leaves these alone. Restore one to start "
                          "optimizing it again."),
        )
        self._ignored_group.set_visible(False)
        self._ignored_rows: list[Gtk.Widget] = []
        self._ignored: list[str] = []

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
        self.add(self._ignored_group)

        self._rows: list[Gtk.Widget] = []
        self._editors: dict[str, ProfileEditor] = {}
        # The whole coupling between a per-game row and this page, named in
        # one place. See gui/widgets/profile_editor.py.
        self._actions = EditorActions(
            save=self._save,
            keep=self._keep,
            ignore=self._ignore,
            remove=self._on_remove,
            export=self._on_export,
            share=self._on_share_works_for_me,
            enable_toggled=self._on_enable_toggled,
        )

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
        self.load_ignored(status.get("ignored_games") or [])
        if status.get("profiles"):
            self.load_profiles(status["profiles"])

    def load_profiles(self, profiles: list[dict[str, Any]]) -> None:
        new = {p["exe"]: p for p in profiles if p.get("exe") != "__forced__"}
        if new == self._profiles:
            return
        self._profiles = new
        self._rebuild()

    def load_ignored(self, ignored: list[str]) -> None:
        if list(ignored) == self._ignored:
            return
        self._ignored = list(ignored)
        self._rebuild_ignored()

    def _rebuild_ignored(self) -> None:
        for row in self._ignored_rows:
            self._ignored_group.remove(row)
        self._ignored_rows.clear()
        for exe in sorted(self._ignored):
            row = Adw.ActionRow(title=exe)
            restore = Gtk.Button(label=_("Restore"), valign=Gtk.Align.CENTER)
            restore.add_css_class("flat")
            restore.connect("clicked", lambda _b, e=exe: self._unignore(e))
            row.add_suffix(restore)
            self._ignored_group.add(row)
            self._ignored_rows.append(row)
        self._ignored_group.set_visible(bool(self._ignored))

    def _unignore(self, exe: str) -> None:
        try:
            self.bridge.unignore_game(exe)
        except Exception as exc:                             # noqa: BLE001
            log.warning("unignore_game failed: %s", exc)
            return
        self._ignored = [g for g in self._ignored if g != exe]
        self._rebuild_ignored()

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

        self._editors.clear()
        for exe, profile in sorted(self._profiles.items()):
            editor = ProfileEditor(exe, profile, self._caps, self._actions)
            row = editor.build()
            self._group.add(row)
            self._editors[exe] = editor
            self._rows.append(row)
        self._building = False

    # -- small helpers -------------------------------------------
    # -- telemetry-free "works for me" report ----------------------
    def _on_share_works_for_me(self, exe: str) -> None:
        d = Adw.AlertDialog(
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
        d.present(self.get_root())

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
        editor = self._editors.get(exe)
        if editor is None:
            return
        editor.patch(enabled=exp.get_enable_expansion())

    def _on_remove(self, _btn: Gtk.Button, exe: str) -> None:
        dialog = Adw.AlertDialog(
            heading=_("Remove game profile?"),
            body=f"“{exe}” will no longer be optimised.",
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("remove", _("Remove"))
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self._on_remove_response, exe)
        dialog.present(self.get_root())

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
        dialog = Adw.AlertDialog(
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
        dialog.present(self.get_root())
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
        d = Adw.AlertDialog(
            heading=f"Apply community settings for {prof.get('display_name') or exe}?",
            body=(note + "\n\n" if note else "")
            + (f"This replaces your current tweaks for {exe}."
               if existing else f"This adds a new profile for {exe}."),
        )
        d.add_response("cancel", _("Cancel"))
        d.add_response("apply", _("Apply"))
        d.set_response_appearance("apply", Adw.ResponseAppearance.SUGGESTED)
        d.connect("response", lambda _dd, resp: resp == "apply" and self._apply_community(prof))
        d.present(self.get_root())
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
