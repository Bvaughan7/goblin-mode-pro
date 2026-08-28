"""Goblin Mode Pro - a native Linux gaming performance and diagnostics utility."""

from goblinmode.__about__ import __version__

__all__ = ["__version__"]

#: GApplication id for the GUI. Kept distinct from the daemon<->GUI bridge name
#: below, because GApplication registers org.gtk.Application on this name.
APP_ID = "com.goblinmode.Pro"

#: Session-bus name and object path the daemon exposes to the GUI.
BRIDGE_BUS_NAME = "com.goblinmode.Pro.Daemon"
BRIDGE_OBJECT_PATH = "/com/goblinmode/Pro/Daemon"

#: System-bus interface of the privileged helper.
HELPER_BUS_NAME = "com.goblinmode.ProHelper"
HELPER_OBJECT_PATH = "/com/goblinmode/ProHelper"
HELPER_IFACE = "com.goblinmode.ProHelper.Manager"
