"""The Rust and Python Proton discovery agree.

The Python walks real directories, so this builds them: a temporary tree with
the marker files each case describes, then `installed_builds` and
`shader_caches` against it. What is compared is the answer, not the walking -
which of two entries sharing a name wins, which order they come back in, and
which paths a "clear this cache" request may name.

That last one is an allowlist drawn from the answer this module just gave. The
caller is about to delete the contents of whatever it names.
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

from goblinmode import proton

_REPO = Path(__file__).resolve().parent.parent

#: (name, markers, mtime) - markers out of {proton, bin/wine, version}.
BUILD_CASES = [
    [],
    [("GE-Proton9-1", {"proton"}, 3.0)],
    [("wine-tkg", {"bin/wine"}, 2.0)],
    [("proton-lite", {"version"}, 1.0)],
    [("notes", set(), 1.0)],
    # A Proton build ships a Wine inside it.
    [("GE-Proton9-1", {"proton", "bin/wine"}, 1.0)],
    [("GE-Proton9-1", {"proton", "bin/wine", "version"}, 1.0)],
    # Newest first.
    [("old", {"proton"}, 1.0), ("new", {"proton"}, 3.0), ("mid", {"proton"}, 2.0)],
    # Ties keep the order they were found in.
    [("a", {"proton"}, 5.0), ("b", {"proton"}, 5.0), ("c", {"proton"}, 5.0)],
    # A mix, including one that is not a build at all.
    [("notes", set(), 9.0), ("wine-tkg", {"bin/wine"}, 2.0),
     ("GE-Proton9-1", {"proton"}, 3.0)],
    # The same build installed in two compatibility directories. The first
    # found wins, and it is the older one here - so a rule of "keep the
    # newest" would pick the other and look just as sensible.
    [("GE-Proton9-1", {"proton"}, 1.0), ("GE-Proton9-1", {"proton"}, 99.0, 1)],
    # ... and the kinds differ too, so the wrong winner is visible twice.
    [("dup", {"proton"}, 1.0), ("dup", {"bin/wine"}, 99.0, 1)],
]

CLEAR = ["/a", "/b", "", "/a/..", "/home/x", "/mnt/games/shader"]


def _binary() -> Path | None:
    override = os.environ.get("GMP_PROTON_BIN")
    if override:
        return Path(override) if Path(override).exists() else None
    for profile in ("debug", "release"):
        candidate = _REPO / "target" / profile / "examples" / "proton"
        if candidate.exists():
            return candidate
    return None


def _python_builds(case) -> list:
    """Drive the real `installed_builds` against real directory trees.

    An entry's optional fourth field is which compatibility directory it lives
    in, defaulting to the first. Two directories is not a curiosity - it is how
    a build ends up installed twice, which is the only way the
    first-one-wins rule is observable.
    """
    with TemporaryDirectory() as tmp:
        roots = [Path(tmp) / "compat0", Path(tmp) / "compat1"]
        for root in roots:
            root.mkdir(parents=True)
        for entry_spec in case:
            name, markers, mtime = entry_spec[:3]
            root = roots[entry_spec[3] if len(entry_spec) > 3 else 0]
            entry = root / name
            entry.mkdir()
            if "proton" in markers:
                (entry / "proton").write_text("#!/bin/sh\n")
            if "bin/wine" in markers:
                (entry / "bin").mkdir()
                (entry / "bin" / "wine").write_text("")
            if "version" in markers:
                (entry / "version").write_text("9.1\n")
            os.utime(entry, (mtime, mtime))
        with patch.object(proton, "_COMPAT_DIRS", roots):
            builds = proton.installed_builds()
        # The path is a temporary directory; compare by name and kind, and
        # check the ordering separately through the names.
        return [{"name": b["name"], "kind": b["kind"], "mtime": b["mtime"]}
                for b in builds]


def _rust_builds(case, binary) -> list:
    # The Python walks the compatibility directories in order and each one's
    # entries sorted by name, so the port is handed them the same way.
    ordered = sorted(
        case,
        key=lambda spec: (spec[3] if len(spec) > 3 else 0, spec[0]),
    )
    payload = {"candidates": [
        {"name": spec[0],
         "path": f"/compat{spec[3] if len(spec) > 3 else 0}/{spec[0]}",
         "has_proton": "proton" in spec[1],
         "has_bin_wine": "bin/wine" in spec[1],
         "has_version": "version" in spec[1],
         "mtime": spec[2]}
        for spec in ordered]}
    r = subprocess.run([str(binary)], input=json.dumps(payload),
                       capture_output=True, text=True, timeout=60, check=False)
    assert r.returncode == 0, r.stderr
    return [{"name": b["name"], "kind": b["kind"], "mtime": b["mtime"]}
            for b in json.loads(r.stdout)["builds"]]


def _python_caches_live(spec) -> tuple[list, list]:
    """Drive the real `shader_caches` against directories that exist.

    `spec` is [(label, dirname, target_or_None, size)] - a target makes the
    entry a SYMLINK to another entry, which is the case the resolve-and-dedup
    exists for. Returns the Python's answer and the (label, path, real, bytes)
    rows to hand the port, so both sides see the same directories.
    """
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        # Build the whole tree first. A symlink listed before its target is
        # exactly the case being tested, and sizing it as it is created would
        # measure a link to a directory that does not exist yet.
        cache_dirs: dict = {}
        for label, dirname, target, size in spec:
            path = root / dirname
            if target is None:
                path.mkdir()
                if size:
                    (path / "cache.bin").write_bytes(b"x" * size)
            else:
                path.symlink_to(root / target, target_is_directory=True)
            cache_dirs.setdefault(label, []).append(path)
        rows = [{"label": label, "path": str(root / dirname),
                 "real": str((root / dirname).resolve()),
                 "bytes": proton._dir_size(root / dirname)}
                for label, dirname, _target, _size in spec]
        with patch.object(proton, "_CACHE_DIRS", cache_dirs):
            answer = proton.shader_caches()
        return answer, rows


#: (label, dirname, symlink target or None, bytes of content)
LIVE_CACHE_CASES = [
    [],
    [("DXVK", "dxvk", None, 10)],
    [("DXVK", "dxvk", None, 10), ("Steam", "steam", None, 500),
     ("VKD3D", "vkd3d", None, 100)],
    # The same directory reached twice, once through a symlink.
    [("Steam", "real", None, 500), ("Steam", "link", "real", 0)],
    # ... and the other way round, which is the only order in which the
    # reported path and the resolved path differ. Without it, "report the
    # resolved spelling" is indistinguishable from reporting the listed one.
    [("Steam", "link", "real", 0), ("Steam", "real", None, 500)],
    # Equal sizes, which must keep the order they were found in.
    [("A", "a", None, 10), ("B", "b", None, 10)],
    [("Empty", "empty", None, 0)],
]


class BothImplementationsAgree(unittest.TestCase):
    def setUp(self):
        self.binary = _binary()
        if self.binary is None:
            if os.environ.get("GMP_REQUIRE_RUST_HELPER") == "1":
                self.fail("GMP_REQUIRE_RUST_HELPER=1 but the proton example is "
                          "not built - run `cargo build -p gmp-core "
                          "--example proton`")
            self.skipTest("build it with `cargo build -p gmp-core "
                          "--example proton`")

    def _rust(self, payload: dict) -> dict:
        r = subprocess.run([str(self.binary)], input=json.dumps(payload),
                           capture_output=True, text=True, timeout=60,
                           check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_every_directory_tree_yields_the_same_builds(self):
        for case in BUILD_CASES:
            with self.subTest(case=[c[0] for c in case]):
                self.assertEqual(_rust_builds(case, self.binary),
                                 _python_builds(case))

    def test_every_cache_listing_is_deduped_and_sorted_the_same(self):
        for spec in LIVE_CACHE_CASES:
            with self.subTest(spec=[s[1] for s in spec]):
                want, rows = _python_caches_live(spec)
                self.assertEqual(self._rust({"caches": rows})["caches"], want)

    def test_a_directory_reached_through_a_symlink_is_counted_once(self):
        """Named on its own because it is what the resolve is for: a Steam
        library reached both ways is one directory, and counting it twice
        would double the number somebody is being asked to act on."""
        want, rows = _python_caches_live(LIVE_CACHE_CASES[3])
        self.assertEqual(len(want), 1)
        self.assertEqual(self._rust({"caches": rows})["caches"], want)

    def test_only_a_listed_path_may_be_cleared(self):
        """The check the delete path leans on. A path that was never shown is
        refused, including the resolved spelling of one that was."""
        want, rows = _python_caches_live(LIVE_CACHE_CASES[3])
        listed = {c["path"] for c in want}
        probes = [*CLEAR, *(row["path"] for row in rows),
                  *(row["real"] for row in rows)]
        self.assertEqual(self._rust({"caches": rows, "clear": probes})["clear"],
                         [path in listed for path in probes])


if __name__ == "__main__":
    unittest.main()
