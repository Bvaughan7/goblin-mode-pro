"""A small ⓘ button with a plain-language popover, for jargony settings."""

from __future__ import annotations

from gi.repository import Gtk

from goblinmode.i18n import _

#: two-sentence explanations keyed by a short id
HELP = {
    "governor": _("Linux slows your CPU down when it thinks you're idle, to save "
                "power. This keeps it at full speed while the game runs, then puts "
                "it back — it removes the stutter when the CPU 'wakes up' late."),
    "renice": _("Tells the Linux scheduler your game matters more than background "
              "tasks, so updates, indexing and browser tabs stop stealing CPU "
              "mid-fight."),
    "tearing": _("The compositor normally adds a frame of delay to keep the picture "
               "perfectly clean. Allowing tearing removes that delay for lower "
               "input lag — your mouse feels more connected."),
    "vrr": _("Adaptive Sync / VRR makes the monitor match the game's frame rate "
           "instead of a fixed one, which removes a whole class of stutter. Only "
           "works on a VRR-capable display."),
    "focus": _("Pauses the file-search indexer, turns on Do Not Disturb and stops "
             "the screen blanking — removes background hitches and the "
             "'screen dimmed mid-cutscene' problem."),
    "core_pin": _("Pins the game's threads to the fast cores of a hybrid CPU, or to "
                "one CCD on a Ryzen, so it stays off the slow cores and the "
                "cross-CCD latency penalty."),
    "power_limit": _("Laptops and desktops cap how many watts the CPU may draw. If "
                   "your cooling can keep up, raising the cap lets the CPU hold "
                   "its top speed for longer."),
    "gamescope": _("A tiny display layer that gives a rock-solid frame cap, FSR/NIS "
                 "upscaling and alt-tab that doesn't break the game. Needs the "
                 "'gamescope' package."),
    "gpu_tuning": _("Extra environment variables for your graphics driver — small, "
                  "well-known knobs that help specific engines. Safe to leave on."),
    "watchdog": _("Logs your FPS via MangoHud and, if it falls off a cliff and "
                "stays there, captures what the GPU was doing so you can see why."),
    "runner_vars": _("Switches for Proton/Wine that many Windows games need on "
                   "Linux — NVAPI (DLSS/Reflex), Fsync (threading), async shader "
                   "compile (fewer first-run hitches)."),
}


def help_button(key: str) -> Gtk.Widget | None:
    text = HELP.get(key)
    if not text:
        return None
    btn = Gtk.MenuButton(icon_name="help-about-symbolic", valign=Gtk.Align.CENTER)
    btn.add_css_class("flat")
    lbl = Gtk.Label(label=text, wrap=True, xalign=0, max_width_chars=42)
    lbl.set_margin_top(8)
    lbl.set_margin_bottom(8)
    lbl.set_margin_start(10)
    lbl.set_margin_end(10)
    pop = Gtk.Popover()
    pop.set_child(lbl)
    btn.set_popover(pop)
    return btn
