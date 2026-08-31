"""Dashboard page - live system stats."""

from __future__ import annotations

import logging
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from goblinmode.i18n import _

from goblinmode.ipc.daemon_bridge import BridgeClient

log = logging.getLogger(__name__)

_GPU_REASON_BITS = {
    0x1: "idle",
    0x2: "app clock limit",
    0x4: "power cap",
    0x8: "HW slowdown",
    0x10: "sync boost",
    0x20: "thermal (SW)",
    0x40: "thermal (HW)",
    0x80: "power brake",
}


def _decode_gpu_reasons(raw) -> str:
    if not raw:
        return "-"
    try:
        bits = int(str(raw), 16) if str(raw).lower().startswith("0x") else int(raw)
    except (TypeError, ValueError):
        return str(raw)
    if bits == 0:
        return "none"
    return ", ".join(label for bit, label in _GPU_REASON_BITS.items() if bits & bit) or "none"


def _row(title: str) -> Adw.ActionRow:
    row = Adw.ActionRow(title=title)
    value = Gtk.Label(label="-")
    value.add_css_class("dim-label")
    value.set_valign(Gtk.Align.CENTER)
    row.add_suffix(value)
    row._value = value  # type: ignore[attr-defined]
    return row


class DashboardPage(Adw.PreferencesPage):
    def __init__(self, bridge: BridgeClient) -> None:
        super().__init__(title=_("Dashboard"), icon_name="utilities-system-monitor-symbolic")
        self.bridge = bridge
        self._nvidia_state: dict[str, Any] = {}

        self._banner = Adw.Banner(title=_("Idle - no game detected"))
        self._banner.set_revealed(True)
        banner_group = Adw.PreferencesGroup()
        banner_group.add(self._banner)
        self.add(banner_group)

        ready = Adw.PreferencesGroup()
        self._health_row = Adw.ActionRow(
            title=_("System readiness"), subtitle=_("checking…"))
        self._health_pill = Gtk.Label(label="—")
        self._health_pill.add_css_class("title-2")
        self._health_pill.set_valign(Gtk.Align.CENTER)
        self._health_row.add_suffix(self._health_pill)
        explain = Gtk.Button(icon_name="help-about-symbolic", valign=Gtk.Align.CENTER)
        explain.add_css_class("flat")
        explain.set_tooltip_text(_("Explain my score"))
        explain.connect("clicked", lambda _b: self._explain_score())
        self._health_row.add_suffix(explain)
        open_check = Gtk.Button(icon_name="go-next-symbolic", valign=Gtk.Align.CENTER)
        open_check.add_css_class("flat")
        open_check.set_tooltip_text(_("Open System Check"))
        open_check.connect("clicked", lambda _b: self._go_system_check())
        self._health_row.add_suffix(open_check)
        ready.add(self._health_row)
        self.add(ready)

        # Distro / kernel setup tips - actionable commands with a copy button,
        # filled in by set_setup_tips() once the capability probe is in.
        self._tips_group = Adw.PreferencesGroup(
            title=_("Setup tips for your system"), visible=False)
        self._tips_rows: list[Adw.ActionRow] = []
        self.add(self._tips_group)

        sysg = Adw.PreferencesGroup(
            title=_("Your hardware"),
            description=_("What Goblin Mode Pro can tune on this machine."),
        )
        self.r_cpu_model = _row(_("Processor"))
        self.r_freq_driver = _row(_("CPU frequency driver"))
        self.r_gpu_model = _row(_("Graphics"))
        self.r_can_govern = _row(_("CPU speed control"))
        self.r_can_power = _row(_("CPU power-limit control"))
        self.r_can_gpu = _row(_("Deep GPU stats"))
        self.r_gamemode = _row(_("GameMode"))
        self.r_controllers = _row(_("Controllers"))
        for r in (self.r_cpu_model, self.r_freq_driver, self.r_gpu_model,
                  self.r_can_govern, self.r_can_power, self.r_can_gpu,
                  self.r_gamemode, self.r_controllers):
            sysg.add(r)
        self.add(sysg)

        cpu = Adw.PreferencesGroup(title=_("CPU"))
        self.r_governor = _row(_("Scaling governor"))
        self.r_epp = _row(_("Energy performance preference"))
        self.r_cpu_temp = _row(_("Package temperature"))
        self.r_power = _row(_("Package power (vs PL1 / PL2)"))
        self.r_pl = _row(_("RAPL limits (PL1 / PL2)"))
        self.r_load = _row(_("Aggregate load"))
        for r in (self.r_governor, self.r_epp, self.r_cpu_temp, self.r_power, self.r_pl, self.r_load):
            cpu.add(r)
        self.add(cpu)

        gpu = Adw.PreferencesGroup(title=_("GPU"))
        self.r_gpu_load = _row(_("Utilisation"))
        self.r_gpu_temp = _row(_("Temperature"))
        self.r_gpu_throttle = _row(_("Throttle reasons"))
        self.r_vram = _row(_("Video memory (used / total)"))
        self.r_pcie = _row(_("PCIe link"))
        self.r_gpuclock = _row(_("Core clock (cur / max)"))
        for r in (self.r_gpu_load, self.r_gpu_temp, self.r_gpu_throttle,
                  self.r_vram, self.r_pcie, self.r_gpuclock):
            gpu.add(r)
        self.add(gpu)

        self._nvidia_group = Adw.PreferencesGroup(
            title=_("NVIDIA driver"),
            description=_("modeset must be on for Wayland and explicit sync; GSP "
                          "firmware is the driver's own offload processor."),
            visible=False,
        )
        self._nvidia_modeset_row = Adw.ActionRow(title=_("nvidia-drm modeset"))
        self._nvidia_modeset_toggle = Gtk.Button(valign=Gtk.Align.CENTER)
        self._nvidia_modeset_toggle.connect("clicked", self._on_toggle_modeset)
        self._nvidia_modeset_row.add_suffix(self._nvidia_modeset_toggle)
        self._nvidia_group.add(self._nvidia_modeset_row)
        self._nvidia_gsp_row = _row(_("GSP firmware"))
        self._nvidia_group.add(self._nvidia_gsp_row)
        self.add(self._nvidia_group)

        fps = Adw.PreferencesGroup(
            title=_("Frame rate"),
            description=_("From the MangoHud log when the watchdog is enabled for a game."),
        )
        self.r_fps = _row(_("avg 60 s / min / 1% low"))
        fps.add(self.r_fps)
        self.add(fps)

        svc = Adw.PreferencesGroup(title=_("Service"))
        self.r_helper = _row(_("Privileged helper"))
        self.r_active = _row(_("Active tweaks"))
        svc.add(self.r_helper)
        svc.add(self.r_active)
        self.add(svc)

    # -- updates ----------------------------------------------------
    def update_status(self, status: dict[str, Any]) -> None:
        games = status.get("active_games") or []
        if not status.get("master_enabled", True):
            self._banner.set_title(_("Optimizations disabled"))
        elif games:
            self._banner.set_title("Boosting: " + ", ".join(games))
        elif status.get("forced_boost"):
            self._banner.set_title(_("Forced performance mode"))
        else:
            self._banner.set_title(_("Idle - no game detected"))

        caps = status.get("capabilities") or {}
        if caps:
            self.r_cpu_model._value.set_label(
                caps.get("cpu_model") or (caps.get("cpu_vendor") or "-").title()
            )
            self.r_freq_driver._value.set_label(caps.get("cpufreq_driver") or "-")
            self.r_gpu_model._value.set_label(
                ", ".join(v.upper() if v in ("amd",) else v.title()
                          for v in (caps.get("gpu_vendors") or [])) or "-"
            )
            self.r_can_govern._value.set_label(
                _("yes — governor + EPP") if caps.get("epp_control")
                else _("yes — governor") if caps.get("governor_control")
                else _("no (needs the helper)")
            )
            self.r_can_power._value.set_label(
                _("yes — Intel RAPL") if caps.get("rapl_control")
                else _("yes — ryzenadj") if caps.get("ryzenadj")
                else _("not on this processor")
            )
            self.r_can_gpu._value.set_label(
                _("yes — NVIDIA") if caps.get("gpu_deep_stats")
                else _("basic (temp / load only)")
            )
            self.set_setup_tips(caps)
            hh = caps.get("handheld")
            if hh:
                pretty = {"steamdeck": "Steam Deck", "rog_ally": "ROG Ally",
                          "legion_go": "Legion Go"}.get(hh, "handheld")
                self.r_freq_driver.set_subtitle(f"{pretty} detected — TDP presets are "
                                                "in each game's power-limit section")

        self.r_governor._value.set_label(str(status.get("governor") or "-"))
        tweaks = status.get("tweaks") or {}
        self.r_epp._value.set_label(_("performance") if tweaks.get("epp_boosted") else _("default"))
        plw = tweaks.get("power_limits_w")
        if plw:
            suffix = "  (overridden)" if tweaks.get("power_limited") else ""
            self.r_pl._value.set_label(f"{plw[0]} / {plw[1]} W{suffix}")
        else:
            self.r_pl._value.set_label("-")
        self.r_helper._value.set_label(
            _("connected") if status.get("helper_available") else _("unavailable (limited mode)")
        )
        active = []
        if tweaks.get("epp_boosted") or tweaks.get("governor") == "performance":
            active.append(_("governor"))
        if tweaks.get("power_limited"):
            active.append(_("power-limit"))
        if tweaks.get("tearing"):
            active.append(_("tearing"))
        if tweaks.get("adaptive_sync"):
            active.append(_("VRR"))
        if tweaks.get("reniced"):
            active.append(f"renice×{len(tweaks['reniced'])}")
        if tweaks.get("mangohud_files"):
            active.append(_("mangohud"))
        self.r_active._value.set_label(", ".join(active) or _("none"))

        g = status.get("gpu") or {}
        used, total, free = g.get("vram_used_mb"), g.get("vram_total_mb"), g.get("vram_free_mb")
        if used is not None and total:
            warn = _("  ⚠ near full") if (free is not None and free < 400) else ""
            self.r_vram._value.set_label(f"{used} / {total} MB{warn}")
        else:
            self.r_vram._value.set_label("-")
        gen, genm = g.get("pcie_gen"), g.get("pcie_gen_max")
        wid, widm = g.get("pcie_width"), g.get("pcie_width_max")
        if gen and genm:
            degraded = _("  ⚠ down-trained") if gen < genm else ""
            self.r_pcie._value.set_label(f"Gen{gen}×{wid or '?'} (max Gen{genm}×{widm or '?'}){degraded}")
        else:
            self.r_pcie._value.set_label("-")
        cg, cgm = g.get("clock_gfx_mhz"), g.get("clock_gfx_max_mhz")
        self.r_gpuclock._value.set_label(f"{cg} / {cgm} MHz" if cg and cgm else "-")

        f = status.get("fps") or {}
        if f.get("fps_avg") is not None:
            dip = _("  ⚠ in dip") if f.get("in_dip") else ""
            self.r_fps._value.set_label(
                f"{f.get('fps_avg')} / {f.get('fps_min')} / {f.get('fps_1low')} fps{dip}"
            )
        else:
            self.r_fps._value.set_label(_("- (watchdog off or no log yet)"))

        if status.get("latest_sample"):
            self.update_sample(status["latest_sample"])

    # -- roadmap: health score, GameMode, controllers, kernel nudge ----
    def _go_system_check(self) -> None:
        win = self.get_root()
        if win is not None and hasattr(win, "set_visible_page") and hasattr(win, "preflight"):
            win.set_visible_page(win.preflight)

    def update_nvidia_state(self, state: dict[str, Any]) -> None:
        """Read-only nvidia_drm.modeset + GSP firmware info - see
        gpu.nvidia_module_state(). modeset is a boot-time modprobe option,
        so the button here writes a persistent config and asks for a
        reboot; it never flips anything live."""
        self._nvidia_state = state or {}
        self._nvidia_group.set_visible(bool(self._nvidia_state.get("present")))
        modeset = self._nvidia_state.get("modeset")
        state = {"Y": _("on"), "N": _("off")}.get(
            modeset, _("unknown (root-only on this driver)"))
        # The read-out is reliable; it's *writing* it (a modprobe.d drop-in via
        # the helper) that has not been verified across drivers and distros.
        self._nvidia_modeset_row.set_subtitle(
            _("{state} — changing it is experimental").format(state=state))
        self._nvidia_modeset_toggle.set_label(
            _("Turn off (needs reboot)") if modeset == "Y" else _("Turn on (needs reboot)"))
        self._nvidia_modeset_toggle.set_sensitive(modeset in ("Y", "N"))
        gsp = self._nvidia_state.get("gsp_firmware_version")
        self._nvidia_gsp_row._value.set_label(gsp or _("off / unreported"))

    def _on_toggle_modeset(self, _btn) -> None:
        target = self._nvidia_state.get("modeset") != "Y"  # flip
        win = self.get_root()
        d = Adw.AlertDialog(
            heading=_("Change nvidia-drm modeset?"),
            body=_(
                "This writes /etc/modprobe.d/goblin-mode-pro-nvidia.conf and takes "
                "effect after a reboot — nothing changes right now. Turning modeset "
                "off can break Wayland sessions on NVIDIA; only do this if you "
                "know why you need to."),
        )
        d.add_response("cancel", _("Cancel"))
        d.add_response("write", _("Write the config"))
        d.set_response_appearance("write", Adw.ResponseAppearance.SUGGESTED)
        d.connect("response", lambda _d, r: r == "write" and self._do_toggle_modeset(target))
        d.present(win)

    def _do_toggle_modeset(self, enabled: bool) -> None:
        try:
            ok = self.bridge.set_nvidia_modeset(enabled)
        except Exception as exc:  # noqa: BLE001
            ok = False
            log.warning("set_nvidia_modeset failed: %s", exc)
        win = self.get_root()
        if hasattr(win, "toast"):
            win.toast(_("Config written — reboot to apply") if ok
                     else _("Couldn't write the config"))

    def _explain_score(self) -> None:
        """Expand the health pill into what each failing/warn check actually
        breaks in-game, instead of just the top-3 'worst' summary."""
        self.bridge.run_preflight_async(self._explain_score_ready)

    def _explain_score_ready(self, results, err) -> None:
        win = self.get_root()
        if err is not None or results is None:
            if win is not None and hasattr(win, "toast"):
                win.toast(f"Couldn't run the check: {err}")
            return

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        notable = [r for r in results if r["status"] in ("warn", "fail")]
        if not notable:
            box.append(Gtk.Label(label=_("Everything checked out — nothing is costing "
                                 "you in-game right now."), wrap=True, xalign=0))
        for r in notable:
            row = Adw.ActionRow(title=r["title"],
                                subtitle=r.get("detail") or r.get("why", ""))
            pill = Gtk.Label(label=_("ACTION") if r["status"] == "fail" else _("CHECK"))
            pill.add_css_class("caption-heading")
            pill.add_css_class("error" if r["status"] == "fail" else "warning")
            pill.set_valign(Gtk.Align.CENTER)
            row.add_prefix(pill)
            group = Adw.PreferencesGroup()
            group.add(row)
            box.append(group)

        d = Adw.AlertDialog(heading=_("What's costing you performance"))
        d.set_extra_child(box)
        d.add_response("ok", _("Close"))
        d.present(win)

    def update_health(self, health: dict[str, Any]) -> None:
        score = (health or {}).get("score")
        if score is None:
            self._health_pill.set_label("—")
            self._health_row.set_subtitle(_("couldn't run the check"))
            return
        self._health_pill.set_label(f"{score:g}/10")
        for cls in ("success", "warning", "error"):
            self._health_pill.remove_css_class(cls)
        self._health_pill.add_css_class(
            "success" if score >= 8.5 else "warning" if score >= 6 else "error")
        n = health.get("counts") or {}
        worst = health.get("worst") or []
        if worst:
            self._health_row.set_subtitle("needs attention: " + ", ".join(worst))
        elif n.get("warn"):
            self._health_row.set_subtitle(f"{n['warn']} thing(s) worth a look — open the check")
        else:
            self._health_row.set_subtitle(_("your machine is game-ready"))

    def update_system_info(self, info: dict[str, Any]) -> None:
        gm = (info or {}).get("gamemode") or {}
        if not gm.get("installed"):
            self.r_gamemode._value.set_label(_("not installed"))
            self.r_gamemode.set_subtitle("")
        elif gm.get("active"):
            self.r_gamemode._value.set_label(_("active"))
        else:
            self.r_gamemode._value.set_label(_("installed, idle"))
        if gm.get("installed"):
            detail = (gm.get("detail") or "").replace("\n", " ").strip()
            self.r_gamemode.set_subtitle(
                detail[:100] if detail
                else _("sets the governor, GPU perf level and ioprio per game"))
        pads = (info or {}).get("controllers") or []
        self.r_controllers._value.set_label(
            ", ".join(p.strip() for p in pads)[:60] if pads else _("none detected"))

    def set_setup_tips(self, caps: dict[str, Any]) -> None:
        """Distro-specific, copy-pasteable one-liners: a gaming kernel if the
        running one is stock, and the driver / namespace fix each distro needs.
        Dismissible by simply ignoring them - never blocks anything."""
        from goblinmode.gui.widgets.snippet import command_row

        for r in self._tips_rows:
            self._tips_group.remove(r)
        self._tips_rows.clear()

        caps = caps or {}
        distro = (caps.get("distro_id") or "").lower()
        tips: list[tuple[str, str]] = []

        if caps.get("kernel_flavor") == "generic":
            from goblinmode.capabilities import kernel_upgrade_tip

            kernel = kernel_upgrade_tip(distro)
            if kernel[1]:
                tips.append(kernel)

        if distro in ("ubuntu", "debian", "pop", "mint", "linuxmint"):
            tips.append((
                _("Some anti-cheats and Steam's container need unprivileged user namespaces"),
                "echo 'kernel.unprivileged_userns_clone=1' | "
                "sudo tee /etc/sysctl.d/99-userns.conf && sudo sysctl --system"))
        if distro == "fedora" and "nvidia" in (caps.get("gpu_vendors") or []):
            tips.append((
                _("NVIDIA users on Fedora need the RPM Fusion driver, not nouveau"),
                "sudo dnf install "
                "https://mirrors.rpmfusion.org/free/fedora/rpmfusion-free-release-$(rpm -E %fedora).noarch.rpm "
                "https://mirrors.rpmfusion.org/nonfree/fedora/rpmfusion-nonfree-release-$(rpm -E %fedora).noarch.rpm "
                "&& sudo dnf install akmod-nvidia"))

        for why, cmd in tips:
            row = command_row(why, cmd)
            self._tips_group.add(row)
            self._tips_rows.append(row)
        self._tips_group.set_visible(bool(self._tips_rows))

    def update_sample(self, s: dict[str, Any]) -> None:
        def fmt(v, unit=""):
            return f"{v}{unit}" if v is not None else "-"

        self.r_cpu_temp._value.set_label(fmt(s.get("cpu_temp"), " °C"))
        pl1, pl2 = s.get("pl1_w"), s.get("pl2_w")
        pwr = s.get("pkg_power_w")
        self.r_power._value.set_label(
            f"{fmt(pwr, ' W')}  ({fmt(pl1)} / {fmt(pl2)} W)"
        )
        self.r_load._value.set_label(fmt(s.get("cpu_load"), " %"))
        self.r_gpu_load._value.set_label(fmt(s.get("gpu_load"), " %"))
        self.r_gpu_temp._value.set_label(fmt(s.get("gpu_temp"), " °C"))
        self.r_gpu_throttle._value.set_label(_decode_gpu_reasons(s.get("gpu_throttle_reasons")))
