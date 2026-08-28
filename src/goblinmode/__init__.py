"""Goblin Mode Pro - a lightweight native Linux gaming performance tinkerer."""

from goblinmode.__about__ import __version__

__all__ = ["__version__"]

APP_ID = "com.goblinmode.Pro"  # the GUI's GApplication id - must stay unique to it

# The daemon<->GUI bridge uses its OWN name so it doesn't collide with the GUI's
# GApplication registration (which exposes org.gtk.Application on APP_ID).
BRIDGE_BUS_NAME = "com.goblinmode.Pro.Daemon"
BRIDGE_OBJECT_PATH = "/com/goblinmode/Pro/Daemon"

HELPER_BUS_NAME = "com.goblinmode.ProHelper"
HELPER_OBJECT_PATH = "/com/goblinmode/ProHelper"
HELPER_IFACE = "com.goblinmode.ProHelper.Manager"
POLKIT_ACTION = "com.goblinmode.pro.manage-performance"
