"""Translation plumbing.

Import ``_`` (and ``ngettext``) from here and wrap user-facing strings:

    from goblinmode.i18n import _
    label = _("Dashboard")

Translations live in ``/usr/share/locale/<lang>/LC_MESSAGES/goblin-mode-pro.mo``
(or ``$GOBLIN_LOCALEDIR`` when running from a source tree). With no catalogue
installed everything falls back to the English source string, so wrapping is
always safe.

See ``po/README.md`` for how to add a language.
"""

from __future__ import annotations

import gettext
import os
from pathlib import Path

DOMAIN = "goblin-mode-pro"

_candidates = [
    os.environ.get("GOBLIN_LOCALEDIR"),
    "/usr/share/locale",
    "/usr/local/share/locale",
    str(Path(__file__).resolve().parents[2] / "po" / "_build"),  # source tree
]
_localedir = next((d for d in _candidates if d and Path(d).is_dir()), None)

_t = gettext.translation(DOMAIN, localedir=_localedir, fallback=True)
_ = _t.gettext
ngettext = _t.ngettext
