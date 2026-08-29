"""Dashboard page - live system stats."""

from __future__ import annotations

from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from goblinmode.i18n import _  # noqa: E402

from goblinmode.ipc.daemon_bridge import BridgeClient

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

        self._banner = Adw.Banner(title="Idle - no game detected")
        self._banner.set_revealed(True)
        banner_group = Adw.PreferencesGroup()
        banner_group.add(self._banner)
        self.add(banner_group)

        ready = Adw.PreferencesGroup()
        self._health_row = Adw.ActionRow(
            title="System readiness", subtitle="checking…")
        self._health_pill = Gtk.Label(label="—")
        self._health_pill.add_css_class("title-2")
        self._health_pill.set_valign(Gtk.Align.CENTER)
        self._health_row.add_suffix(self._health_pill)
        open_check = Gtk.Button(icon_name="go-next-symbolic", valign=Gtk.Align.CENTER)
        open_check.add_css_class("flat")
        open_check.set_tooltip_text("Open System Check")
        open_check.connect("clicked", lambda _b: self._go_system_check())
        self._health_row.add_suffix(open_check)
        ready.add(self._health_row)
        self._nudge_row = Adw.ActionRow(visible=False)
        self._nudge_row.add_css_class("dim-label")
        self._nudge_row.set_title_lines(0)
        ready.add(self._nudge_row)
        self.add(ready)

        sysg = Adw.PreferencesGroup(
            title="Your hardware",
            description="What Goblin Mode Pro can tune on this machine.",
        )
        self.r_cpu_model = _row("Processor")
        self.r_freq_driver = _row("CPU frequency driver")
        self.r_gpu_model = _row("Graphics")
        self.r_can_govern = _row("CPU speed control")
        self.r_can_power = _row("CPU power-limit control")
        self.r_can_gpu = _row("Deep GPU stats")
        self.r_gamemode = _row("GameMode")
        self.r_controllers = _row("Controllers")
        for r in (self.r_cpu_model, self.r_freq_driver, self.r_gpu_model,
                  self.r_can_govern, self.r_can_power, self.r_can_gpu,
                  self.r_gamemode, self.r_controllers):
            sysg.add(r)
        self.add(sysg)

        cpu = Adw.PreferencesGroup(title="CPU")
        self.r_governor = _row("Scaling governor")
        self.r_epp = _row("Energy performance preference")
        self.r_cpu_temp = _row("Package temperature")
        self.r_power = _row("Package power (vs PL1 / PL2)")
        self.r_pl = _row("RAPL limits (PL1 / PL2)")
        self.r_load = _row("Aggregate load")
        for r in (self.r_governor, self.r_epp, self.r_cpu_temp, self.r_power, self.r_pl, self.r_load):
            cpu.add(r)
        self.add(cpu)

        gpu = Adw.PreferencesGroup(title="GPU")
        self.r_gpu_load = _row("Utilisation")
        self.r_gpu_temp = _row("Temperature")
        self.r_gpu_throttle = _row("Throttle reasons")
        self.r_vram = _row("Video memory (used / total)")
        self.r_pcie = _row("PCIe link")
        self.r_gpuclock = _row("Core clock (cur / max)")
        for r in (self.r_gpu_load, self.r_gpu_temp, self.r_gpu_throttle,
                  self.r_vram, self.r_pcie, self.r_gpuclock):
            gpu.add(r)
        self.add(gpu)

        fps = Adw.PreferencesGroup(
            title="Frame rate",
            description="From the MangoHud log when the watchdog is enabled for a game.",
        )
        self.r_fps = _row("avg 60 s / min / 1% low")
        fps.add(self.r_fps)
        self.add(fps)

        svc = Adw.PreferencesGroup(title="Service")
        self.r_helper = _row("Privileged helper")
        self.r_active = _row("Active tweaks")
        svc.add(self.r_helper)
        svc.add(self.r_active)
        self.add(svc)

    # -- updates ----------------------------------------------------
    def update_status(self, status: dict[str, Any]) -> None:
        games = status.get("active_games") or []
        if not status.get("master_enabled", True):
            self._banner.set_title("Optimizations disabled")
        elif games:
            self._banner.set_title("Boosting: " + ", ".join(games))
        elif status.get("forced_boost"):
            self._banner.set_title("Forced performance mode")
        else:
            self._banner.set_title("Idle - no game detected")

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
                "yes — governor + EPP" if caps.get("epp_control")
                else "yes — governor" if caps.get("governor_control")
                else "no (needs the helper)"
            )
            self.r_can_power._value.set_label(
                "yes — Intel RAPL" if caps.get("rapl_control")
                else "yes — ryzenadj" if caps.get("ryzenadj")
                else "not on this processor"
            )
            self.r_can_gpu._value.set_label(
                "yes — NVIDIA" if caps.get("gpu_deep_stats")
                else "basic (temp / load only)"
            )
            self.set_kernel_nudge(caps)
            hh = caps.get("handheld")
            if hh:
                pretty = {"steamdeck": "Steam Deck", "rog_ally": "ROG Ally",
                          "legion_go": "Legion Go"}.get(hh, "handheld")
                self.r_freq_driver.set_subtitle(f"{pretty} detected — TDP presets are "
                                                "in each game's power-limit section")

        self.r_governor._value.set_label(str(status.get("governor") or "-"))
        tweaks = status.get("tweaks") or {}
        self.r_epp._value.set_label("performance" if tweaks.get("epp_boosted") else "default")
        plw = tweaks.get("power_limits_w")
        if plw:
            suffix = "  (overridden)" if tweaks.get("power_limited") else ""
            self.r_pl._value.set_label(f"{plw[0]} / {plw[1]} W{suffix}")
        else:
            self.r_pl._value.set_label("-")
        self.r_helper._value.set_label(
            "connected" if status.get("helper_available") else "unavailable (limited mode)"
        )
        active = []
        if tweaks.get("epp_boosted") or tweaks.get("governor") == "performance":
            active.append("governor")
        if tweaks.get("power_limited"):
            active.append("power-limit")
        if tweaks.get("tearing"):
            active.append("tearing")
        if tweaks.get("adaptive_sync"):
            active.append("VRR")
        if tweaks.get("reniced"):
            active.append(f"renice×{len(tweaks['reniced'])}")
        if tweaks.get("mangohud_files"):
            active.append("mangohud")
        self.r_active._value.set_label(", ".join(active) or "none")

        g = status.get("gpu") or {}
        used, total, free = g.get("vram_used_mb"), g.get("vram_total_mb"), g.get("vram_free_mb")
        if used is not None and total:
            warn = "  ⚠ near full" if (free is not None and free < 400) else ""
            self.r_vram._value.set_label(f"{used} / {total} MB{warn}")
        else:
            self.r_vram._value.set_label("-")
        gen, genm = g.get("pcie_gen"), g.get("pcie_gen_max")
        wid, widm = g.get("pcie_width"), g.get("pcie_width_max")
        if gen and genm:
            degraded = "  ⚠ down-trained" if gen < genm else ""
            self.r_pcie._value.set_label(f"Gen{gen}×{wid or '?'} (max Gen{genm}×{widm or '?'}){degraded}")
        else:
            self.r_pcie._value.set_label("-")
        cg, cgm = g.get("clock_gfx_mhz"), g.get("clock_gfx_max_mhz")
        self.r_gpuclock._value.set_label(f"{cg} / {cgm} MHz" if cg and cgm else "-")

        f = status.get("fps") or {}
        if f.get("fps_avg") is not None:
            dip = "  ⚠ in dip" if f.get("in_dip") else ""
            self.r_fps._value.set_label(
                f"{f.get('fps_avg')} / {f.get('fps_min')} / {f.get('fps_1low')} fps{dip}"
            )
        else:
            self.r_fps._value.set_label("- (watchdog off or no log yet)")

        if status.get("latest_sample"):
            self.update_sample(status["latest_sample"])

    # -- roadmap: health score, GameMode, controllers, kernel nudge ----
    def _go_system_check(self) -> None:
        win = self.get_root()
        if win is not None and hasattr(win, "set_visible_page") and hasattr(win, "preflight"):
            win.set_visible_page(win.preflight)

    def update_health(self, health: dict[str, Any]) -> None:
        score = (health or {}).get("score")
        if score is None:
            self._health_pill.set_label("—")
            self._health_row.set_subtitle("couldn't run the check")
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
            self._health_row.set_subtitle("your machine is game-ready")

    def update_system_info(self, info: dict[str, Any]) -> None:
        gm = (info or {}).get("gamemode") or {}
        if not gm.get("installed"):
            self.r_gamemode._value.set_label("not installed")
        elif gm.get("active"):
            self.r_gamemode._value.set_label("active")
        else:
            self.r_gamemode._value.set_label("installed, idle")
        pads = (info or {}).get("controllers") or []
        self.r_controllers._value.set_label(
            ", ".join(p.strip() for p in pads)[:60] if pads else "none detected")

    def set_kernel_nudge(self, caps: dict[str, Any]) -> None:
        if (caps or {}).get("kernel_flavor") == "generic":
            distro = caps.get("distro_id", "")
            hint = {
                "ubuntu": "try the Xanmod or Liquorix kernel",
                "debian": "try the Liquorix kernel (liquorix.net)",
                "fedora": "try the kernel from COPR (e.g. bieszczaders/kernel-cachyos)",
                "arch": "try linux-zen (in the repos) or the CachyOS kernels",
                "cachyos": "",
            }.get(distro, "a gaming-tuned kernel (Zen / Xanmod / CachyOS) can help with stutter")
            if hint:
                self._nudge_row.set_visible(True)
                self._nudge_row.set_title(f"💡 You're on a stock kernel — {hint}.")
                return
        self._nudge_row.set_visible(False)

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
