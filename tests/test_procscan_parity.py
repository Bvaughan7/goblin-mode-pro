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

The process table moves while it is being read, so each side is scanned TWICE
and only a process that looked the same to both of its own reads is compared -
a kworker's name ends in the workqueue it is draining and changes several
times a second. A run that finds too few processes in common is a failed run,
not a passed one.
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
        # kworker's comm ends in the workqueue it is currently draining, and
        # that changes several times a second. So BOTH implementations are
        # read twice and only a process that looked the same to each of them
        # is compared - anything that moved under us is a race rather than a
        # disagreement.
        #
        # Reading psutil twice is not enough on its own, and a red CI run
        # proved it: a kworker presented the same name to both psutil reads
        # and a different one to the single Rust scan sandwiched between
        # them, so the pid looked stable and the race was reported as a
        # mismatch. It takes a second read of the side that saw the odd value
        # to notice that the value did not last.
        before = cls._psutil_table()
        first = cls._rust_table()
        after = cls._psutil_table()
        second = cls._rust_table()

        cls.rust = {pid: row for pid, row in first.items()
                    if second.get(pid) == row}
        cls.python = {pid: row for pid, row in after.items()
                      if before.get(pid) == row}
        cls.unstable = sorted((set(before) & set(after) & set(first)
                               & set(second)) - (set(cls.rust) & set(cls.python)))

    @classmethod
    def _rust_table(cls) -> dict:
        proc = subprocess.run([str(cls.binary)], capture_output=True, text=True,
                              timeout=60, check=False)
        assert proc.returncode == 0, proc.stderr
        return {row["pid"]: row for row in json.loads(proc.stdout)}

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

    def _draining(self, pid: int) -> bool:
        """Is this a pool kworker, whose name says what it is doing RIGHT NOW?

        The kernel names a workqueue pool worker `kworker/<cpu>:<id>[H]-<wq>`
        and rewrites the part after the `-` every time it picks up work from a
        different queue - several times a second on a busy box. Two scans of
        the same process therefore disagree without either being wrong, and no
        amount of sandwiching fixes it: reading each side twice cut the false
        mismatches by most but not all of them, because a worker cycling
        between two queues can present one value to both reads on one side and
        the other value to both reads on the other.

        So a pool worker's NAME is not compared. Nothing is lost: it is not an
        identity, the observer can never match one (a kernel thread has no
        `exe` and no command line, so it is never a game), and every other
        long name on the machine still holds this side to the full string -
        including the rescuers, `kworker/R-<name>`, which are named once and
        stay put.
        """
        prefix = "kworker/"
        return any(row[pid]["name"].startswith(prefix)
                   and not row[pid]["name"].startswith(f"{prefix}R-")
                   for row in (self.rust, self.python))

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
            if self._draining(pid):
                continue
            if self.rust[pid]["name"] != self.python[pid]["name"]:
                mismatched.append(
                    f"pid {pid}: rust={self.rust[pid]['name']!r} "
                    f"psutil={self.python[pid]['name']!r}")
        self.assertEqual(mismatched, [], "\n".join(mismatched))

    def test_the_extended_names_are_actually_exercised(self):
        """Any Linux box has some; if it does not, this proves nothing.

        A machine where nothing was extended would let a scanner that returns
        the raw `comm` and nothing else pass this file cleanly.

        The test this replaces asked for a reported name longer than fifteen
        characters, which is not the same question: kernel threads present a
        full-length `comm` - `kworker/R-kvfree_rcu_reclaim` is twenty-eight
        characters and no extension happened - so on any Linux box it was
        satisfied without the rule under test ever running. What makes an
        extension an extension is the reported name DIFFERING from the raw
        `comm`, so that is what is counted, and against the same `comm` file
        the scanner reads rather than against a length.
        """
        extended = []
        for pid in self._common():
            try:
                comm = Path(f"/proc/{pid}/comm").read_text().rstrip("\n")
            except OSError:
                continue  # exited since the scans; it proves nothing either way
            if self.python[pid]["name"] != comm:
                extended.append((pid, comm, self.python[pid]["name"]))
        self.assertTrue(
            extended,
            "no process on this machine had its comm extended from its command "
            "line, so the extension rule went unexercised",
        )

    def test_a_long_name_with_no_command_line_is_still_compared(self):
        """The pool kworkers skipped above are not the only long kernel names.

        A scanner that truncated to the kernel's fifteen characters would be
        caught by the extension rule on the userspace side, but a kernel
        thread has no command line to extend FROM - `pool_workqueue_release`
        and `rcu_exp_gp_kthread_worker` are simply that long in `comm`. If
        every long name with no argv were skipped as volatile, that would go
        untested.
        """
        long_kthreads = [self.python[pid]["name"] for pid in self._common()
                         if not self._draining(pid)
                         and not self.python[pid]["cmdline"]
                         and len(self.python[pid]["name"]) > 15]
        self.assertTrue(long_kthreads,
                        "no stable kernel thread on this machine has a name "
                        "past the comm limit, so a truncating scanner would "
                        "only be caught by the extension rule")

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

    def test_no_process_gains_an_empty_trailing_argument(self):
        """The kernel writes a NUL after the last argument.

        Splitting on it naively leaves a phantom empty argument, which the
        observer would then see as a candidate name.

        A trailing empty is not wrong in itself, which is the correction this
        test carries: a process that rewrote its own title pads the argv area
        with NULs, so `php-fpm: master process (...)` on a CI runner really
        does end in twenty-two empty arguments and psutil reports every one of
        them. `read_cmdline` drops exactly ONE trailing separator for that
        reason. So the mistake to catch is not an empty argument, it is an
        empty argument PSUTIL DID NOT ALSO REPORT - a phantom this side
        manufactured by splitting.
        """
        for pid in self._common():
            rust, python = self.rust[pid]["cmdline"], self.python[pid]["cmdline"]
            if rust and rust[-1] == "" and not (python and python[-1] == ""):
                self.fail(f"pid {pid}: rust={rust!r} psutil={python!r}")

    def test_ordinary_command_lines_are_the_bulk_of_the_corpus(self):
        """The check above is only worth something on processes that have one.

        Every kernel thread has an empty `cmdline`, and they are a third of
        this table; a corpus that was all kernel threads would pass the
        trailing-argument check without ever splitting anything.
        """
        with_args = [pid for pid in self._common() if self.rust[pid]["cmdline"]]
        self.assertGreaterEqual(len(with_args), MIN_COMMON // 2,
                                f"only {len(with_args)} of {len(self._common())} "
                                "stable processes had a command line at all")


if __name__ == "__main__":
    unittest.main()
