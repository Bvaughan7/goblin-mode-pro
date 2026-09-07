"""The Rust and Python notifications carry the same arguments.

Compared as the tuple the Python packs, in order, because that is what crosses
the bus. Two of these fields decide whether somebody is interrupted or merely
informed, and one decides whether a stream of warnings is one bubble or twenty.

The Python is driven through `send` with the proxy replaced, so the packing is
the real one rather than a copy of it.
"""

from __future__ import annotations

import itertools
import json
import os
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from tests._support import _SRC  # noqa: F401

from goblinmode import notify

_REPO = Path(__file__).resolve().parent.parent


def _binary() -> Path | None:
    override = os.environ.get("GMP_NOTIFY_BIN")
    if override:
        return Path(override) if Path(override).exists() else None
    for profile in ("debug", "release"):
        candidate = _REPO / "target" / profile / "examples" / "notify"
        if candidate.exists():
            return candidate
    return None


def _cases():
    sents = [{}, {"status": 42}, {"session": 7},
             {"status": 42, "session": 7, "incident": 9}]
    for title, body, replace, urgency, tag, sent in itertools.product(
            ("Performance mode on \u2014 WoW", ""),
            ("Boosting: governor", ""),
            (True, False),
            (0, 1, 2, 3, -1, 9999),
            ("status", "session", "incident", "clip", "detect"),
            sents):
        yield {"title": title, "body": body, "replace": replace,
               "urgency": urgency, "tag": tag, "sent": sent}


def _python(case) -> list:
    """Drive the real `send` and read the variant it packed."""
    packed: list = []

    class _Proxy:
        def call_sync(self, method, variant, flags, timeout, cancellable):
            packed.append(variant.unpack())
            from gi.repository import GLib
            return GLib.Variant("(u)", (1,))

    with patch.object(notify, "_get_proxy", lambda: _Proxy()), \
            patch.dict(notify._ids, case["sent"], clear=True):
        notify.send(case["title"], case["body"], replace=case["replace"],
                    urgency=case["urgency"], tag=case["tag"])
    app, rid, icon, title, body, actions, hints, timeout = packed[0]
    return [app, rid, icon, title, body, list(actions),
            {"urgency": hints["urgency"], "desktop-entry": hints["desktop-entry"]},
            timeout]


class BothImplementationsPackTheSameCall(unittest.TestCase):
    def setUp(self):
        self.binary = _binary()
        if self.binary is None:
            if os.environ.get("GMP_REQUIRE_RUST_HELPER") == "1":
                self.fail("GMP_REQUIRE_RUST_HELPER=1 but the notify example is "
                          "not built - run `cargo build -p gmp-core "
                          "--example notify`")
            self.skipTest("build it with `cargo build -p gmp-core "
                          "--example notify`")

    def _rust(self, cases) -> list:
        r = subprocess.run([str(self.binary)],
                           input=json.dumps({"cases": cases}),
                           capture_output=True, text=True, timeout=120,
                           check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_every_call_is_packed_the_same(self):
        cases = list(_cases())
        got = self._rust(cases)
        for case, answer in zip(cases, got, strict=True):
            with self.subTest(**case):
                self.assertEqual(answer, _python(case))

    def test_a_tag_never_overwrites_another_tags_bubble(self):
        """A thermal warning must not eat the benchmark result on screen."""
        sent = {"status": 42}
        cases = [{"title": "t", "body": "", "replace": True, "urgency": 1,
                  "tag": tag, "sent": sent} for tag in ("status", "session")]
        got = self._rust(cases)
        self.assertEqual(got[0][1], 42)
        self.assertEqual(got[1][1], 0)
        self.assertEqual(got, [_python(c) for c in cases])


if __name__ == "__main__":
    unittest.main()
