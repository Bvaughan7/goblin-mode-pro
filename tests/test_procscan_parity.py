"""The Rust process scan agrees with psutil, on this machine's real table.

The observer's judgement is diffed from fixtures elsewhere. This is the input
to that judgement, and it is the one part of the port that cannot be checked
from a fixture: it can only be compared against a real ``/proc``.

The field worth the trouble is ``name``. ``/proc/<pid>/comm`` is truncated to
15 characters by the kernel, and psutil does not hand that straight back —
when the truncated name is at the limit and the basename of ``cmdline[0]``
starts with it, psutil returns the longer basename instead. Every comparison
the observer makes against a process name therefore sees the full name on the
Python side.

That is not cosmetic. Three entries on the observer's Wine/Steam blocklist are
longer than fifteen characters — ``gameoverlayui.exe``, ``steamwebhelper.exe``
and ``wine64-preloader`` — so a scanner returning the raw ``comm`` would never
match them. They would stop being filtered, and because the observer picks the
*fattest* matching process, ``steamwebhelper.exe`` would beat the game it is
drawing an overlay on and take the renice.

The process table moves while it is being read, so only processes both scans
saw are compared, and the comparison is on the stable fields. A run that finds
too few processes in common is a failed run, not a passed one.
"""

from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path

from tests._support import _SRC  # noqa: F401

_REPO = Path(__file__).resolve().parent.parent

#: Below this many processes seen by both scans, the comparison is not
#: evidence of anything and the test says so instead of passing.
MIN_COMMON = 25


def _binary() -> Path | None:
    override = os.environ.get("GMP_PROCSCAN_BIN")
    if override:
        return Path(override) if Path(override).exists() else None
    for profile in ("debug", "release"):
        candidate = _REPO / "target" / profile / "examples" / "procscan"
        if candidate.exists():
            return candidate
    return None


class BothScansAgree(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        cls.binary = _binary()
        if cls.binary is None:
            if os.environ.get("GMP_REQUIRE_RUST_HELPER") == "1":
                cls.fail(cls, "GMP_REQUIRE_RUST_HELPER=1 but the procscan example "
                              "is not built - run `cargo build -p gmp-daemon "
                              "--example procscan`")
            raise unittest.SkipTest("build it with `cargo build -p gmp-daemon "
                                    "--example procscan`")
        try:
            import psutil  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("psutil is not installed") from None

        # The table is moving while it is read, and some of it moves fast: a
        # kworker's comm carries a `-`/`+` that flips as it goes idle. So
        # psutil is read on BOTH sides of the Rust scan and only a process
        # that looked the same in both is compared - anything that changed
        # under us is a race rather than a disagreement.
        before = cls._psutil_table()
        proc = subprocess.run([str(cls.binary)], capture_output=True, text=True,
                              timeout=60, check=False)
        assert proc.returncode == 0, proc.stderr
        after = cls._psutil_table()

        cls.rust = {row["pid"]: row for row in json.loads(proc.stdout)}
        cls.python = {pid: row for pid, row in after.items()
                      if before.get(pid) == row}
        cls.unstable = sorted(set(after) & set(before)
                              - set(cls.python))

    @staticmethod
    def _psutil_table() -> dict:
        import psutil
        table = {}
        for process in psutil.process_iter(["pid", "name", "exe", "cmdline"]):
            try:
                info = process.info
            except psutil.Error:
                continue
            table[info["pid"]] = {
                "pid": info["pid"],
                "name": info["name"] or "",
                "exe": info["exe"] or "",
                "cmdline": list(info["cmdline"] or []),
            }
        return table

    def _common(self) -> list[int]:
        return sorted(set(self.rust) & set(self.python))

    def test_the_two_scans_overlap_enough_to_be_worth_comparing(self):
        """A run that compared three processes would pass and prove nothing."""
        self.assertGreaterEqual(
            len(self._common()), MIN_COMMON,
            f"only {len(self._common())} processes were stable across both "
            f"scans ({len(self.unstable)} changed under us)",
        )

    def test_every_process_has_the_name_psutil_reports(self):
        """Including the extended ones, which is the whole point."""
        mismatched = []
        for pid in self._common():
            if self.rust[pid]["name"] != self.python[pid]["name"]:
                mismatched.append(
                    f"pid {pid}: rust={self.rust[pid]['name']!r} "
                    f"psutil={self.python[pid]['name']!r}")
        self.assertEqual(mismatched, [], "\n".join(mismatched))

    def test_the_extended_names_are_actually_exercised(self):
        """Any Linux box has some; if it does not, this proves nothing.

        A machine where no process name is over fifteen characters would let a
        scanner that never extends anything pass this file cleanly.
        """
        extended = [pid for pid in self._common()
                    if len(self.python[pid]["name"]) > 15]
        self.assertTrue(
            extended,
            "no process on this machine has a name past the comm limit, so the "
            "extension rule went unexercised",
        )

    def test_command_lines_match(self):
        mismatched = []
        for pid in self._common():
            if self.rust[pid]["cmdline"] != self.python[pid]["cmdline"]:
                mismatched.append(
                    f"pid {pid} ({self.python[pid]['name']}): "
                    f"rust={self.rust[pid]['cmdline']!r} "
                    f"psutil={self.python[pid]['cmdline']!r}")
        self.assertEqual(mismatched, [], "\n".join(mismatched[:5]))

    def test_executables_match_where_both_could_read_them(self):
        """`/proc/<pid>/exe` is unreadable for another user's process.

        Both sides report that as empty, so a disagreement is only meaningful
        where one of them managed to read it.
        """
        mismatched = []
        for pid in self._common():
            rust, python = self.rust[pid]["exe"], self.python[pid]["exe"]
            if rust and python and rust != python:
                mismatched.append(f"pid {pid}: rust={rust!r} psutil={python!r}")
        self.assertEqual(mismatched, [], "\n".join(mismatched[:5]))

    def test_no_process_carries_an_empty_trailing_argument(self):
        """The kernel writes a NUL after the last argument.

        Splitting on it naively leaves a phantom empty argument, which the
        observer would then see as a candidate name.
        """
        for pid in self._common():
            cmdline = self.rust[pid]["cmdline"]
            if cmdline:
                self.assertNotEqual(cmdline[-1], "", f"pid {pid}: {cmdline!r}")


if __name__ == "__main__":
    unittest.main()
