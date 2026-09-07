"""What the helper contributes to a status agrees between the two.

Three shapes, and the third is the one a rewrite forgets: a helper that answers
`available()` and then stops answering mid-status. What the status reports is
what the READS did, not what the probe claimed - because unavailable is what
the next caller will find when it tries to change something. The governor it
managed to answer before it stopped is still reported, since the two reads are
separate calls and the helper can die between them.

The watt conversion is diffed over a corpus of microwatt values weighted
towards half-watt ties, because `round()` goes to EVEN there and the result
ends up both on screen and in the `pl:45/60` token stored with every session.
"""

from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path

from tests._support import _SRC  # noqa: F401

from goblinmode.ipc.helper_client import HelperUnavailable
from goblinmode.payload import PerformancePayload

_REPO = Path(__file__).resolve().parent.parent

#: Microwatt pairs. Whole watts, half-watt ties either side of even, and the
#: values a real RAPL limit holds.
PAIRS = [
    (0, 0),
    (45_000_000, 60_000_000),
    (500_000, 1_500_000),
    (2_500_000, 3_500_000),
    (45_500_000, 46_500_000),
    (15_000_000, 25_000_000),
    (1, 999_999),
    (7_400_000, 12_600_000),
    (-500_000, -1_500_000),
    (500_000_000, 0),
]


def _binary() -> Path | None:
    override = os.environ.get("GMP_STATUSREADS_BIN")
    if override:
        return Path(override) if Path(override).exists() else None
    for profile in ("debug", "release"):
        candidate = _REPO / "target" / profile / "examples" / "statusreads"
        if candidate.exists():
            return candidate
    return None


class _Helper:
    """A helper client that behaves the way the reply describes."""

    def __init__(self, reply):
        self._reply = reply

    def available(self):
        return self._reply["kind"] != "unavailable"

    def get_governor(self):
        if self._reply["kind"] == "failed" and self._reply.get("governor") is None:
            raise HelperUnavailable("gone")
        return self._reply.get("governor")

    def get_power_limits(self):
        if self._reply["kind"] == "failed":
            raise HelperUnavailable("gone")
        return tuple(self._reply["power_limits_uw"])


def _python(reply) -> dict:
    p = PerformancePayload.__new__(PerformancePayload)
    p.helper = _Helper(reply)
    p._governor_applied = False
    p._tearing_applied = False
    p._vrr_applied = False
    p._focus_applied = False
    p._power_applied = False
    p._reniced = {}
    p._pinned = {}
    p._mangohud_files = set()
    p._scx_applied = None
    status = p.status()
    return {
        "governor": status.governor,
        "power_limits_w": list(status.power_limits_w)
        if status.power_limits_w else None,
        "helper_available": status.helper_available,
        "limited_mode": status.limited_mode,
    }


def _replies():
    yield {"kind": "unavailable"}
    yield {"kind": "failed", "governor": None}
    yield {"kind": "failed", "governor": "performance"}
    for pl1, pl2 in PAIRS:
        yield {"kind": "answered", "governor": "performance",
               "power_limits_uw": [pl1, pl2]}
        yield {"kind": "answered", "governor": None,
               "power_limits_uw": [pl1, pl2]}


class BothImplementationsAgree(unittest.TestCase):
    def setUp(self):
        self.binary = _binary()
        if self.binary is None:
            if os.environ.get("GMP_REQUIRE_RUST_HELPER") == "1":
                self.fail("GMP_REQUIRE_RUST_HELPER=1 but the statusreads "
                          "example is not built - run `cargo build -p gmp-core "
                          "--example statusreads`")
            self.skipTest("build it with `cargo build -p gmp-core "
                          "--example statusreads`")

    def _rust(self, replies) -> list:
        r = subprocess.run([str(self.binary)],
                           input=json.dumps({"replies": replies}),
                           capture_output=True, text=True, timeout=60,
                           check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_every_reply_reads_the_same(self):
        replies = list(_replies())
        for reply, answer in zip(replies, self._rust(replies), strict=True):
            with self.subTest(reply=reply):
                self.assertEqual(answer, _python(reply))

    def test_a_half_watt_limit_rounds_to_even_on_both_sides(self):
        """Named on its own: it is the only arithmetic here, it decides a
        token stored in the session history, and away-from-zero is the
        rounding a port reaches for."""
        replies = [{"kind": "answered", "governor": None,
                    "power_limits_uw": [45_500_000, 46_500_000]}]
        self.assertEqual(self._rust(replies)[0]["power_limits_w"], [46, 46])
        self.assertEqual(_python(replies[0])["power_limits_w"], [46, 46])


if __name__ == "__main__":
    unittest.main()
