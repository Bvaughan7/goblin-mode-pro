"""Both implementations agree on THIS machine's real state.

Every other parity harness asks questions somebody chose. This one asks the
machine: its actual MangoHud config, its actual log directories, its actual
core layout, the builds and shader caches it actually has, the running
helper's real capabilities, what polkit really says, the daemon's own status,
the session history on disk and the profile index in the repo.

That is not redundant with a corpus. A corpus contains the cases its author
thought of; a machine contains what is there. The `(deleted)` marker on
`/proc/<pid>/exe` - which would have made a game updated mid-session look like
it had exited - was found exactly this way, by a harness reading the real
`/proc` on a day the machine had been updated, and by nothing else.

NOTHING HERE WRITES. Every check reads, asks both implementations, and
compares. Each skips on its own when the thing it reads is not present, so
this is useful on a developer's machine and quiet everywhere else.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from tests._support import _SRC  # noqa: F401

_REPO = Path(__file__).resolve().parent.parent


def _example(name: str) -> Path | None:
    for profile in ("debug", "release"):
        candidate = _REPO / "target" / profile / "examples" / name
        if candidate.exists():
            return candidate
    return None


def _run(name: str, payload: dict, raw: bool = False):
    binary = _example(name)
    if binary is None:
        raise unittest.SkipTest(f"build it with `cargo build -p gmp-core "
                                f"--example {name}`")
    r = subprocess.run([str(binary)], input=json.dumps(payload),
                       capture_output=True, text=True, timeout=120, check=False)
    assert r.returncode == 0, r.stderr
    return r.stdout if raw else json.loads(r.stdout)


class ThisMachineAgrees(unittest.TestCase):
    def test_the_real_mangohud_config_reverts_the_same(self):
        from goblinmode import mangohud

        conf = mangohud.MANGOHUD_CONF
        if not conf.exists():
            self.skipTest("no MangoHud.conf on this machine")
        text = conf.read_text()
        lines = text.splitlines()
        stripped = mangohud._Conf(list(lines))
        stripped.strip_gmp_block()
        want = (text if stripped.lines == lines
                else "\n".join(stripped.lines).rstrip("\n") + "\n")
        self.assertEqual(_run("mangohud", {"which": "revert", "existing": text},
                              raw=True), want)

    def test_the_real_log_directories_would_be_pruned_the_same(self):
        """Reads the directories and compares the DECISION. Nothing is
        deleted - the Python's pruner is not called at all."""
        from goblinmode.paths import GAME_LOG_DIR, MANGOHUD_LOG_DIR

        looked = 0
        for directory, pattern in ((GAME_LOG_DIR, "*.log"),
                                   (MANGOHUD_LOG_DIR, "*.csv")):
            if not directory.is_dir():
                continue
            looked += 1
            entries = []
            for path in directory.glob(pattern):
                if path.is_file():
                    stat = path.stat()
                    entries.append({"name": path.name, "mtime": stat.st_mtime,
                                    "size": stat.st_size})
            got = set(_run("housekeeping", {"files": entries, "keep_newest": 40,
                                            "max_bytes": 500 * 1024 * 1024}))
            by_name = {e["name"]: e for e in entries}
            newest = sorted(by_name, key=lambda n: by_name[n]["mtime"], reverse=True)
            running, want = 0, set()
            for i, name in enumerate(newest):
                running += by_name[name]["size"]
                if i < 40 and running <= 500 * 1024 * 1024:
                    continue
                want.add(name)
            with self.subTest(directory=directory.name, files=len(entries)):
                self.assertEqual(got, want)
        if not looked:
            self.skipTest("neither log directory exists yet")

    def test_this_cpus_real_layout_pins_the_same(self):
        from goblinmode import capabilities, cpuset

        layout = capabilities.detect().get("core_layout", {})
        modes = ["performance", "cache0", "off"]
        cases = [{"mode": mode, "layout": layout} for mode in modes]
        self.assertEqual(_run("cpuset", {"cases": cases}),
                         [cpuset.target_cpus(mode, layout) for mode in modes])

    def test_the_real_proton_builds_and_caches_agree(self):
        from goblinmode import proton

        candidates = []
        for directory in proton._COMPAT_DIRS:
            if not directory.is_dir():
                continue
            for entry in sorted(directory.iterdir()):
                if not entry.is_dir():
                    continue
                candidates.append({
                    "name": entry.name, "path": str(entry),
                    "has_proton": (entry / "proton").exists(),
                    "has_bin_wine": (entry / "bin/wine").exists(),
                    "has_version": (entry / "version").exists(),
                    "mtime": entry.stat().st_mtime})
        caches = []
        for label, paths in proton._CACHE_DIRS.items():
            for path in paths:
                if path.is_dir():
                    caches.append({"label": label, "path": str(path),
                                   "real": str(path.resolve()),
                                   "bytes": proton._dir_size(path)})
        if not candidates and not caches:
            self.skipTest("no Steam or compatibility directories here")
        got = _run("proton", {"candidates": candidates, "caches": caches})
        self.assertEqual([(b["name"], b["kind"]) for b in got["builds"]],
                         [(b["name"], b["kind"]) for b in proton.installed_builds()])
        self.assertEqual(got["caches"], proton.shader_caches())

    def test_the_running_helpers_real_capabilities_decode_the_same(self):
        from goblinmode import selftest

        pid = selftest._helper_pid()
        if pid is None:
            self.skipTest("the privileged helper is not running")
        status = Path(f"/proc/{pid}/status").read_text()
        got = _run("selftest", {"statuses": [{"status": status, "field": "CapEff"},
                                             {"status": status, "field": "CapPrm"}]})
        self.assertEqual(got["sets"], [selftest._read_cap_set(pid, "CapEff"),
                                       selftest._read_cap_set(pid, "CapPrm")])
        mask = selftest._read_cap_set(pid, "CapEff") or 0
        self.assertEqual(_run("selftest", {"masks": [mask]})["caps"][0],
                         selftest._decode_caps(mask))

    def test_what_polkit_really_says_is_read_the_same(self):
        """`pkcheck` without `--allow-user-interaction`, so this never
        prompts."""
        import shutil

        from goblinmode import selftest

        if not shutil.which("pkcheck"):
            self.skipTest("pkcheck is not installed")
        action = "com.goblinmode.pro.manage-performance"
        code, output = selftest._run(
            ["pkcheck", "--action-id", action, "--process", str(os.getpid())])
        got = _run("selftest", {"pkchecks": [{"installed": True, "code": code,
                                              "output": output}]})["pkchecks"][0]
        self.assertEqual(got, list(selftest._pkcheck(action)))

    def test_the_live_daemons_own_status_exports_the_same(self):
        from goblinmode import exporter

        probe = subprocess.run(
            ["gdbus", "call", "--session", "-d", "com.goblinmode.Pro.Daemon",
             "-o", "/com/goblinmode/Pro/Daemon",
             "-m", "com.goblinmode.Pro.Daemon.GetStatus"],
            capture_output=True, text=True)
        if probe.returncode != 0:
            self.skipTest("the daemon is not on the session bus")
        raw = probe.stdout.strip()
        status = json.loads(raw[2:raw.rindex("',")].encode().decode("unicode_escape"))
        self.assertEqual(_run("exporter", {"which": "render",
                                           "statuses": [status]})[0],
                         exporter.render(status))

    def _gmp_cli(self):
        for profile in ("debug", "release"):
            candidate = _REPO / "target" / profile / "gmp-cli"
            if candidate.exists():
                return candidate
        self.skipTest("build it with `cargo build -p gmp-cli`")
        return None

    def test_the_rust_cli_prints_what_the_python_cli_prints(self):
        """End to end, against whichever daemon is running: the Rust binary
        connects to the session bus, calls the method, parses the JSON string
        the interface answers with and renders it - and every line has to be
        the line the Python CLI prints from the same daemon.

        This is the first thing in the port that is a CLIENT of the running
        system rather than a function asked a question, so it is the first
        check that the seam works in the direction a cutover needs.

        Read-only commands only. Nothing here changes a setting.
        """
        binary = self._gmp_cli()
        probe = subprocess.run(
            ["gdbus", "call", "--session", "-d", "com.goblinmode.Pro.Daemon",
             "-o", "/com/goblinmode/Pro/Daemon",
             "-m", "com.goblinmode.Pro.Daemon.GetStatus"],
            capture_output=True, text=True)
        if probe.returncode != 0:
            self.skipTest("the daemon is not on the session bus")

        env = dict(os.environ, PYTHONPATH=str(_REPO / "src"))
        for argv in (["status"], ["health"], ["games"], ["preflight"],
                     ["sessions", "--limit", "3"], ["sessions", "--limit", "0"]):
            with self.subTest(command=" ".join(argv)):
                rust = subprocess.run([str(binary), *argv], capture_output=True,
                                      text=True, timeout=120)
                python = subprocess.run(
                    [sys.executable, "-m", "goblinmode.cli", *argv],
                    capture_output=True, text=True, timeout=120, env=env)
                self.assertEqual(rust.returncode, 0, rust.stderr)
                self.assertEqual(python.returncode, 0, python.stderr)
                self.assertEqual(rust.stdout.splitlines(),
                                 python.stdout.splitlines())

    def test_the_rust_cli_says_so_when_nothing_is_listening(self):
        """A CLI that silently starts a background service because it was
        asked for a status line is doing something nobody asked for. This one
        connects without auto-starting, so a missing daemon is a message."""
        binary = self._gmp_cli()
        empty = dict(os.environ)
        empty["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=/nonexistent/gmp-no-bus"
        out = subprocess.run([str(binary), "status"], capture_output=True,
                             text=True, timeout=60, env=empty)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("gmp-cli:", out.stderr)
        self.assertEqual(out.stdout, "")

    def test_the_real_session_history_compares_the_same(self):
        from goblinmode import benchmarkcard
        from goblinmode.paths import DATA_DIR

        history = DATA_DIR / "sessions.jsonl"
        if not history.exists():
            self.skipTest("no session history yet")
        rows = []
        for line in history.read_text().splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
        if len(rows) < 2:
            self.skipTest("fewer than two sessions recorded")
        pairs = [(rows[i], rows[i + 1]) for i in range(min(8, len(rows) - 1))]
        got = _run("benchmarkcard",
                   {"pairs": [{"a": a, "b": b} for a, b in pairs]})
        self.assertEqual(got, [benchmarkcard.diff_sessions(a, b)
                               for a, b in pairs])

    def test_the_repos_own_profile_index_is_filtered_the_same(self):
        from goblinmode import community

        index = _REPO / "profiles/index.json"
        if not index.exists():
            self.skipTest("no profiles/index.json in the tree")
        data = json.loads(index.read_text())
        want = []
        for entry in data:
            if isinstance(entry, dict) and entry.get("slug") and entry.get("exe"):
                want.append({
                    "slug": community._safe_slug(str(entry["slug"])),
                    "exe": str(entry["exe"])[:128],
                    "display_name": str(entry.get("display_name")
                                        or entry["exe"])[:200],
                    "note": str(entry.get("note") or "")[:280]})
        self.assertEqual(_run("community", {"indexes": [data]})["indexes"][0],
                         want)


if __name__ == "__main__":
    unittest.main()
