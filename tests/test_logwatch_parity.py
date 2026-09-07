"""The Rust and Python Proton-log watchers read the same thing.

Neither of this module's failure modes announces itself. A watcher that reads
the same bytes twice reports the same fault twice; one that mishandles a
truncation stops reporting anything at all and looks exactly like a quiet
machine.

So the Python is driven for real - a file on disk, the watcher's own `poll`,
its own position bookkeeping - and the port is asked the same questions from
the same bytes. The corpus covers the three arithmetic cases (an ordinary
read, a file that shrank, a backlog past the cap) and the two rules about
what a poll reports: at most one fault, and every line still reaching the tail.
"""

from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tests._support import _SRC  # noqa: F401

from goblinmode import logwatch

_REPO = Path(__file__).resolve().parent.parent

FAULT = "err:seh:vkQueueSubmit VK_ERROR_DEVICE_LOST"
#: a second, different fault - the two must not be reported together
OTHER_FAULT = "wine: Unhandled page fault in Wow.exe"
NOISE = "info: DXVK: Creating pipeline"


def _binary() -> Path | None:
    override = os.environ.get("GMP_LOGWATCH_BIN")
    if override:
        return Path(override) if Path(override).exists() else None
    for profile in ("debug", "release"):
        candidate = _REPO / "target" / profile / "examples" / "logwatch"
        if candidate.exists():
            return candidate
    return None


def _python_poll(content: str, pos: int, now: float, last_hit_at: float,
                 cooldown: float, recent: list) -> dict:
    with TemporaryDirectory() as tmp:
        directory = Path(tmp)
        path = directory / "game.log"
        path.write_text(content)
        watcher = logwatch.LogWatcher(cooldown=cooldown)
        watcher._path = path
        watcher._pos = pos
        watcher._recent = list(recent)
        watcher._last_hit_at = last_hit_at
        with patch.object(logwatch, "GAME_LOG_DIR", directory), \
                patch("goblinmode.logwatch.time.monotonic", lambda: now):
            hit = watcher.poll()
        return {
            "hit": None if hit is None else {
                "label": hit.label, "line": hit.line, "context": hit.context},
            "recent": watcher._recent,
            "pos": watcher._pos,
        }


def _cases():
    yield {"content": "", "pos": 0}
    yield {"content": f"{NOISE}\n", "pos": 0}
    yield {"content": f"{NOISE}\n{FAULT}\n{NOISE}\n", "pos": 0}
    # Two faults in one poll: only the first is reported, both are remembered.
    yield {"content": f"{FAULT}\n{NOISE}\n{OTHER_FAULT}\n", "pos": 0}
    # Already read part of it.
    yield {"content": f"{NOISE}\n{FAULT}\n", "pos": len(NOISE) + 1}
    # The file shrank: read it all again.
    yield {"content": f"{FAULT}\n", "pos": 10_000}
    # Position exactly at the end.
    yield {"content": f"{FAULT}\n", "pos": len(FAULT) + 1}
    # Trailing whitespace, and a line with no newline at the end.
    yield {"content": f"{NOISE}   \t\n{FAULT}", "pos": 0}
    # A backlog past the cap: the oldest part is skipped and the first line
    # read is a fragment.
    big = "x" * 600_000
    yield {"content": f"{FAULT}\n{big}\n{NOISE}\n", "pos": 0}
    # ... and one where the fault is inside the part that survives the skip.
    yield {"content": f"{big}\n{FAULT}\n", "pos": 0}
    # More than the tail holds.
    yield {"content": "".join(f"line{i}\n" for i in range(300)), "pos": 0}
    # Terminators `str.splitlines` breaks on and `str::lines` does not. A
    # Proton log is raw output from somebody else\'s program; a form feed or a
    # NEL in it splits one line into two on the Python side, and a tail that
    # disagreed about that would quote the wrong context into an incident.
    for terminator in ("\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85",
                       "\u2028", "\u2029", "\r"):
        yield {"content": f"{NOISE}{terminator}{FAULT}\n", "pos": 0}
        yield {"content": f"{FAULT}{terminator}{NOISE}\n", "pos": 0}


class BothImplementationsReadTheSameThing(unittest.TestCase):
    def setUp(self):
        self.binary = _binary()
        if self.binary is None:
            if os.environ.get("GMP_REQUIRE_RUST_HELPER") == "1":
                self.fail("GMP_REQUIRE_RUST_HELPER=1 but the logwatch example "
                          "is not built - run `cargo build -p gmp-core "
                          "--example logwatch`")
            self.skipTest("build it with `cargo build -p gmp-core "
                          "--example logwatch`")

    def _rust(self, payload: dict) -> dict:
        r = subprocess.run([str(self.binary)], input=json.dumps(payload),
                           capture_output=True, text=True, timeout=60,
                           check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_every_case_polls_the_same(self):
        # Two cooldowns, and zero is not a curiosity: it is the only value at
        # which "one fault per poll" and "one fault per cooldown" are
        # different rules, and this module enforces the first one directly
        # rather than leaning on the second.
        for case in _cases():
          for cooldown in (30.0, 0.0):
            payload = {"now": 100.0, "last_hit_at": 0.0, "cooldown": cooldown,
                       "recent": [], **case}
            with self.subTest(size=len(case["content"]), pos=case["pos"],
                              cooldown=cooldown):
                self.assertEqual(
                    self._rust(payload),
                    _python_poll(payload["content"], payload["pos"],
                                 payload["now"], payload["last_hit_at"],
                                 payload["cooldown"], payload["recent"]))

    def test_only_the_first_fault_in_a_poll_is_reported(self):
        """With no cooldown at all this is the only rule left standing, and it
        is the one the module states directly. Two different faults in one
        read must still produce one incident, naming the first."""
        payload = {"content": f"{FAULT}\n{NOISE}\n{OTHER_FAULT}\n", "pos": 0,
                   "now": 100.0, "last_hit_at": 0.0, "cooldown": 0.0,
                   "recent": []}
        rust = self._rust(payload)
        self.assertEqual(rust["hit"]["line"], FAULT)
        self.assertEqual(rust, _python_poll(**payload))

    def test_a_fault_inside_the_cooldown_is_remembered_but_not_reported(self):
        """A Proton log that starts failing repeats the same line hundreds of
        times a second. One incident per line would bury the one that
        mattered - but the lines still have to reach the tail, because the
        next incident quotes them."""
        payload = {"content": f"{FAULT}\n", "pos": 0, "now": 100.0,
                   "last_hit_at": 95.0, "cooldown": 30.0, "recent": []}
        rust = self._rust(payload)
        self.assertIsNone(rust["hit"])
        self.assertEqual(rust["recent"], [FAULT])
        self.assertEqual(rust, _python_poll(**{k: payload[k] for k in
                                               ("content", "pos", "now",
                                                "last_hit_at", "cooldown",
                                                "recent")}))

    def test_the_offending_line_is_the_last_of_its_own_context(self):
        payload = {"content": "".join(f"n{i}\n" for i in range(20))
                              + f"{FAULT}\n",
                   "pos": 0, "now": 100.0, "last_hit_at": 0.0,
                   "cooldown": 30.0, "recent": []}
        rust = self._rust(payload)
        self.assertEqual(rust["hit"]["context"][-1], FAULT)
        self.assertEqual(len(rust["hit"]["context"]), logwatch.CONTEXT_LINES)
        self.assertEqual(rust, _python_poll(**{k: payload[k] for k in
                                               ("content", "pos", "now",
                                                "last_hit_at", "cooldown",
                                                "recent")}))


if __name__ == "__main__":
    unittest.main()
