"""Text the GUI shows, built without touching GTK.

Separate from the widgets on purpose. Everything here is a pure function of a
daemon reply, so it can be asked without a display - and, more to the point,
without PyGObject, which is not installed everywhere the tests run.
"""

from __future__ import annotations

from goblinmode import textfmt
from goblinmode.i18n import _


def active_tweak_labels(tweaks) -> list[str]:
    """What is currently applied, as the dashboard names it.

    Its own list rather than the session fingerprint's, because these are
    words a person reads and those are tokens stored with a session - but the
    two answer the same question and are worth keeping in step. The scheduler
    is named the way the CLI names it (`scx_lavd`) rather than translated: it
    is a scheduler's name, not prose.

    Separate from the widget so it can be asked without a display.
    """
    tweaks = textfmt.fields(tweaks)
    out: list[str] = []
    # The governor counts when only the finer EPP knob moved - on intel_pstate
    # that happens without the governor being pinned.
    if tweaks.get("epp_boosted") or tweaks.get("governor") == "performance":
        out.append(_("governor"))
    if tweaks.get("power_limited"):
        out.append(_("power-limit"))
    if tweaks.get("tearing"):
        out.append(_("tearing"))
    if tweaks.get("adaptive_sync"):
        out.append(_("VRR"))
    reniced = textfmt.fields(tweaks.get("reniced"))
    if reniced:
        out.append(f"renice\u00d7{len(reniced)}")
    # Was missing entirely: the row was built from a fixed list of keys that
    # did not include the one lever that replaces the kernel's scheduler for
    # the whole machine.
    if tweaks.get("scx_scheduler"):
        out.append(f"scx_{textfmt.name(tweaks['scx_scheduler'])}")
    if tweaks.get("mangohud_files"):
        out.append(_("mangohud"))
    return out
