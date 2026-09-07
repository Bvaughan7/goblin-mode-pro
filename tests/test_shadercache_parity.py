"""The Rust and Python shader-cache pre-warms decide the same thing.

Every outcome here is `False` except one, and none of them is actionable by
the caller - "nothing to do" and "it failed" are the same answer with
different words. The words are the whole of what a person gets when a launch
felt slow, so they are what is compared, alongside the command line.

The Python is driven through `prewarm_shader_cache` itself, with the tool
lookup, the archive glob and the subprocess replaced - so the order the
questions are asked in is the real one.
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

from goblinmode import shadercache

_REPO = Path(__file__).resolve().parent.parent

TOOL = "/usr/bin/fossilize_replay"


def _binary() -> Path | None:
    override = os.environ.get("GMP_SHADERCACHE_BIN")
    if override:
        return Path(override) if Path(override).exists() else None
    for profile in ("debug", "release"):
        candidate = _REPO / "target" / profile / "examples" / "shadercache"
        if candidate.exists():
            return candidate
    return None


def _cases():
    archive_sets = [[], ["/cache/0.foz"],
                    ["/cache/0.foz", "/cache/1.foz", "/cache/2.foz"]]
    for app_id, tool, archives, code in itertools.product(
            ("", "1091500"), (None, TOOL), archive_sets, (0, 1, -11)):
        yield {"app_id": app_id, "tool": tool, "archives": archives,
               "exit_code": code}


def _python(case) -> dict:
    ran: list = []

    class _Completed:
        def __init__(self, code):
            self.returncode = code
            self.stderr = ""

    def fake_run(argv, **kw):
        ran.append(list(argv))
        return _Completed(case["exit_code"])

    with patch.object(shadercache, "_fossilize_replay", lambda: case["tool"]), \
            patch.object(shadercache, "_shader_archives",
                         lambda _a: [Path(p) for p in case["archives"]]), \
            patch.object(shadercache.subprocess, "run", fake_run):
        ok, message = shadercache.prewarm_shader_cache(case["app_id"])
    return {"ok": ok, "message": message, "argv": ran[0] if ran else []}


class BothImplementationsDecideTheSame(unittest.TestCase):
    def setUp(self):
        self.binary = _binary()
        if self.binary is None:
            if os.environ.get("GMP_REQUIRE_RUST_HELPER") == "1":
                self.fail("GMP_REQUIRE_RUST_HELPER=1 but the shadercache "
                          "example is not built - run `cargo build -p gmp-core "
                          "--example shadercache`")
            self.skipTest("build it with `cargo build -p gmp-core "
                          "--example shadercache`")

    def _rust(self, cases) -> list:
        r = subprocess.run([str(self.binary)],
                           input=json.dumps({"cases": cases}),
                           capture_output=True, text=True, timeout=60,
                           check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_every_case_decides_and_says_the_same(self):
        cases = list(_cases())
        got = self._rust(cases)
        for case, answer in zip(cases, got, strict=True):
            with self.subTest(**case):
                self.assertEqual(answer, _python(case))

    def test_the_refusals_are_told_apart_only_by_their_words(self):
        """All three are `False`. If two of them said the same thing, a log
        would not distinguish "you have no AppID" from "Steam has not
        downloaded anything yet"."""
        refusals = [
            {"app_id": "", "tool": TOOL, "archives": ["/a.foz"], "exit_code": 0},
            {"app_id": "1", "tool": None, "archives": ["/a.foz"], "exit_code": 0},
            {"app_id": "1", "tool": TOOL, "archives": [], "exit_code": 0},
        ]
        messages = [row["message"] for row in self._rust(refusals)]
        self.assertEqual(len(set(messages)), 3)
        self.assertEqual(messages, [_python(c)["message"] for c in refusals])


if __name__ == "__main__":
    unittest.main()
