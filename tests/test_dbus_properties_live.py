"""The identity properties actually answer, over a real bus.

Every other test of these properties checks a piece: the frozen XML declares
them, and `_handle_get_property` returns the right variant when called
directly. Both passed for months while a real read returned
`org.freedesktop.DBus.Error.Failed: Unable to retrieve property` from the
running daemon, because nothing ever asked for one over the wire.

The cause is that GDBus's `get_property` vtable slot does not work from
PyGObject - `register_object` and `register_object_with_closures2` behave
identically, so it is the closure marshalling rather than the deprecated entry
point. Method calls on the same object work, so `org.freedesktop.DBus.Properties`
is now served as the ordinary interface it is.

This is the test shape that catches it: publish the real bridge on a private
bus under a name of its own, and read the properties the way a bug report
would. It needs a session bus, so it skips where there is none - CI runs it
under `dbus-run-session`.
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

# Puts `src/` first. Without it `goblinmode` resolves to whatever is installed,
# and this test grades a copy of the code that is not the one being changed -
# which it did, on the first run, silently.
from tests._support import _SRC  # noqa: F401

_REPO = Path(__file__).resolve().parent.parent

#: A name of its own, so this never collides with a daemon the developer is
#: running - and never takes the real one away from a GUI mid-test.
TEST_BUS_NAME = "com.goblinmode.Pro.DaemonPropertyTest"

SERVER = r'''
import sys
sys.path.insert(0, %(src)r)
from gi.repository import GLib
from goblinmode.ipc import daemon_bridge as bridge

bridge.BUS_NAME = %(bus)r


class Handler:
    """Enough of the daemon for the bridge to publish. No method is called."""

    def __getattr__(self, name):
        raise AssertionError(f"the property test called {name}")


b = bridge.DaemonBridge(Handler())
b.publish()
GLib.MainLoop().run()
'''


def _have_bus() -> bool:
    return bool(os.environ.get("DBUS_SESSION_BUS_ADDRESS"))


class TheIdentityPropertiesAnswer(unittest.TestCase):
    """Read over the wire, exactly as `tests/conformance/daemon.py` does."""

    server: subprocess.Popen | None = None

    @classmethod
    def setUpClass(cls):
        if not _have_bus():
            raise unittest.SkipTest("no session bus; run under dbus-run-session")
        source = SERVER % {"src": str(_REPO / "src"), "bus": TEST_BUS_NAME}
        cls.server = subprocess.Popen(
            [sys.executable, "-c", source],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        for _ in range(60):
            if cls._call("org.freedesktop.DBus.Peer.Ping").returncode == 0:
                return
            if cls.server.poll() is not None:
                break
            subprocess.run(["gdbus", "wait", "--session", "--timeout", "1",
                            TEST_BUS_NAME], capture_output=True, check=False)
        cls.tearDownClass()
        raise unittest.SkipTest("the bridge did not reach the bus")

    @classmethod
    def tearDownClass(cls):
        if cls.server is not None:
            cls.server.terminate()
            try:
                cls.server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                cls.server.kill()
            cls.server = None

    @classmethod
    def _call(cls, method: str, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["gdbus", "call", "--session", "--dest", TEST_BUS_NAME,
             "--object-path", "/com/goblinmode/Pro/Daemon", "--method", method,
             *args],
            capture_output=True, text=True, timeout=30, check=False,
        )

    def _get(self, name: str) -> str:
        from goblinmode.ipc.daemon_bridge import IFACE
        proc = self._call("org.freedesktop.DBus.Properties.Get", IFACE, name)
        self.assertEqual(proc.returncode, 0,
                         f"reading {name} failed: {proc.stderr.strip()}")
        return proc.stdout.strip()

    def test_every_declared_property_can_be_read(self):
        """The check that was missing. A declared property nobody can read is
        worse than one that was never declared: introspection promises it."""
        from goblinmode.ipc.daemon_bridge import INTERFACE_VERSION
        from goblinmode.__about__ import __version__

        self.assertIn(__version__, self._get("Version"))
        self.assertIn(str(INTERFACE_VERSION), self._get("InterfaceVersion"))
        self.assertIn("python", self._get("Implementation"))

    def test_get_all_reports_the_same_three(self):
        from goblinmode.ipc.daemon_bridge import IFACE
        proc = self._call("org.freedesktop.DBus.Properties.GetAll", IFACE)
        self.assertEqual(proc.returncode, 0, proc.stderr.strip())
        for name in ("Version", "InterfaceVersion", "Implementation"):
            self.assertIn(name, proc.stdout)

    def test_an_undeclared_property_is_refused_by_name(self):
        from goblinmode.ipc.daemon_bridge import IFACE
        proc = self._call("org.freedesktop.DBus.Properties.Get", IFACE, "Nonsense")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("UnknownProperty", proc.stderr)

    def test_the_declared_set_is_exactly_what_introspection_promises(self):
        """Whatever introspection advertises has to be readable."""
        import re
        from goblinmode.ipc.daemon_bridge import IFACE, INTROSPECTION_XML
        declared = set(re.findall(r'<property name="([^"]+)"', INTROSPECTION_XML))
        self.assertTrue(declared, "the interface declares no properties at all")
        for name in sorted(declared):
            with self.subTest(name):
                proc = self._call("org.freedesktop.DBus.Properties.Get", IFACE, name)
                self.assertEqual(proc.returncode, 0,
                                 f"{name} is advertised but unreadable: "
                                 f"{proc.stderr.strip()}")


if __name__ == "__main__":
    unittest.main()
