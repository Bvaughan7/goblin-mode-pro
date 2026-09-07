"""The Rust and Python community-profile checks agree.

These are the lines that decide what a file fetched from outside is allowed to
turn into. The host and the path are pinned, nothing is applied without the
user confirming, and the daemon re-validates every field through `GameProfile`
before saving - so these checks are the first of several layers rather than the
only one. That is what makes them worth diffing exactly: a layer that differs
between implementations is a layer somebody will reason about wrongly.

The slug corpus sweeps Unicode rather than sticking to ASCII. `str.isalnum` and
`char::is_alphanumeric` are both Unicode-aware and are not obviously the same
function - one is Python's union of `isalpha`/`isdecimal`/`isdigit`/`isnumeric`
and the other is `Alphabetic` plus the numeric categories - and the result goes
into a URL path.
"""

from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path

from tests._support import _SRC  # noqa: F401

from goblinmode import community

_REPO = Path(__file__).resolve().parent.parent

SLUGS = [
    "wow", "wow-classic_1.2", "a" * 100, "", "..", "a..b", ".hidden", "-flag",
    "_under", "///", "wow/../etc", "a b c", "hell%20o", "WOW", "w0w",
    "profile.json", "a.b.c", "-", ".", "_", "a-", "a.", "a_",
    # Dots that only become adjacent once what sits between them is dropped.
    # This is why the traversal check runs on the FILTERED string: checking the
    # raw one would let these through.
    "a.%.b", "a. .b", "a./.b", "wow.\u00a0.json", ".%.", "a.\u0000.b",
    # Shapes that only differ if the character filter differs.
    "caf\u00e9", "\u00fcber", "\u4e2d\u6587", "\u0663\u0664",
    "\u00b2\u00b3", "\u2160", "\u3007", "\u1d7ce", "\U0001d7ec",
    "\u2460", "\u00bd", "\u06f1", "\u0f20", "e\u0301",
    "wow\u200bclassic", "wow\ufeff", "\U0001f525", "wow\U0001f525",
]

#: A sweep, so the filter is compared on far more than the shapes I thought of:
#: every printable character below U+3000, then a stride across the rest of
#: Unicode including the astral planes, where the numeric categories live that
#: neither implementation's ASCII intuitions cover.
SWEEP = (
    [chr(c) for c in range(0x20, 0x3000) if chr(c).isprintable()]
    + [chr(c) for c in range(0x3000, 0x110000, 7)
       if chr(c).isprintable() and not 0xD800 <= c <= 0xDFFF]
)

INDEXES = [
    [],
    [{"slug": "a", "exe": "A.exe"}],
    [{"slug": "a", "exe": "A.exe", "display_name": "Game A", "note": "hi"}],
    # Missing fields, empty fields, and a row that is not an object.
    [{"slug": "b"}, {"exe": "C.exe"}, {"slug": "", "exe": "D.exe"},
     "not an object", 5, None,
     {"slug": "ok", "exe": "OK.exe"}],
    # A display name present but empty falls back to the exe.
    [{"slug": "a", "exe": "A.exe", "display_name": ""}],
    [{"slug": "a", "exe": "A.exe", "display_name": None}],
    # The caps, counted in characters.
    [{"slug": "a", "exe": "\u00e9" * 200, "display_name": "\u00e9" * 400,
      "note": "\u00e9" * 400}],
    # Wrong-typed scalars, which `str()` renders rather than refuses.
    [{"slug": "a", "exe": 5}],
    [{"slug": "a", "exe": True}],
    [{"slug": "a", "exe": 2.5}],
    [{"slug": 7, "exe": "A.exe"}],
    # A slug that cannot be made safe fails the whole catalogue.
    [{"slug": "fine", "exe": "A.exe"}, {"slug": "..", "exe": "B.exe"}],
]

PROFILES = [
    {"exe": "Wow.exe"},
    {"exe": "Wow.exe", "nice_value": -5, "auto_created": True,
     "scx_scheduler": "lavd", "enabled": False},
    {"nice_value": -5},
    {"exe": ""},
    {"exe": "Wow.exe", "mangohud": {"enabled": True}, "runner_vars": {"fsync": True}},
    {k: 1 for k in sorted(community.SHAREABLE)},
]

URLS = [
    f"{community._BASE}/wow.json",
    f"{community._BASE}/",
    community._BASE,
    f"{community._BASE}-evil/wow.json",
    "https://example.com/wow.json",
    "http://raw.githubusercontent.com/Bvaughan7/goblin-mode-pro/main/profiles/x.json",
    "",
]


def _binary() -> Path | None:
    override = os.environ.get("GMP_COMMUNITY_BIN")
    if override:
        return Path(override) if Path(override).exists() else None
    for profile in ("debug", "release"):
        candidate = _REPO / "target" / profile / "examples" / "community"
        if candidate.exists():
            return candidate
    return None


def _python_slug(slug: str):
    try:
        return community._safe_slug(slug)
    except community.CommunityError:
        return None


def _python_index(rows):
    """`fetch_index` fetches, so its filtering is repeated here against the
    module's own helpers. The one part that is not shared is the loop, and it
    is four lines - short enough that a copy is safer than a network call in a
    unit test."""
    out = []
    for entry in rows:
        if isinstance(entry, dict) and entry.get("slug") and entry.get("exe"):
            try:
                slug = community._safe_slug(str(entry["slug"]))
            except community.CommunityError:
                return None
            out.append({
                "slug": slug,
                "exe": str(entry["exe"])[:128],
                "display_name": str(entry.get("display_name") or entry["exe"])[:200],
                "note": str(entry.get("note") or "")[:280],
            })
    return out


def _python_profile(data):
    if not isinstance(data, dict) or not data.get("exe"):
        return None
    return {k: v for k, v in data.items() if k in community.SHAREABLE}


class BothImplementationsCheckTheSameWay(unittest.TestCase):
    def setUp(self):
        self.binary = _binary()
        if self.binary is None:
            if os.environ.get("GMP_REQUIRE_RUST_HELPER") == "1":
                self.fail("GMP_REQUIRE_RUST_HELPER=1 but the community example "
                          "is not built - run `cargo build -p gmp-core "
                          "--example community`")
            self.skipTest("build it with `cargo build -p gmp-core "
                          "--example community`")

    def _rust(self, payload: dict) -> dict:
        r = subprocess.run([str(self.binary)], input=json.dumps(payload),
                           capture_output=True, text=True, timeout=120,
                           check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_every_slug_is_judged_the_same(self):
        got = self._rust({"slugs": SLUGS})["slugs"]
        for slug, answer in zip(SLUGS, got, strict=True):
            with self.subTest(slug=slug):
                self.assertEqual(answer, _python_slug(slug))

    def test_the_character_filter_agrees_across_a_unicode_sweep(self):
        """`isalnum` and `is_alphanumeric` are both Unicode-aware and are not
        the same function by construction. Each character is asked on its own,
        wrapped so the answer is about the filter rather than about the
        leading-dot rule."""
        slugs = [f"a{c}b" for c in SWEEP]
        got = self._rust({"slugs": slugs})["slugs"]
        disagreed = [(c, a, _python_slug(s))
                     for c, s, a in zip(SWEEP, slugs, got, strict=True)
                     if a != _python_slug(s)]
        self.assertEqual(
            disagreed[:8], [],
            f"{len(disagreed)} of {len(SWEEP)} characters are filtered "
            f"differently")

    def test_a_traversal_hidden_between_dropped_characters_is_caught(self):
        """The check runs on the filtered string, not the raw one. `a.%.b` has
        no `..` in it until the `%` is dropped, and then it does."""
        self.assertIsNone(_python_slug("a.%.b"))
        self.assertEqual(self._rust({"slugs": ["a.%.b"]})["slugs"], [None])

    def test_every_catalogue_is_filtered_the_same(self):
        got = self._rust({"indexes": INDEXES})["indexes"]
        for rows, answer in zip(INDEXES, got, strict=True):
            with self.subTest(rows=rows):
                self.assertEqual(answer, _python_index(rows))

    def test_every_profile_keeps_the_same_fields(self):
        got = self._rust({"profiles": PROFILES})["profiles"]
        for profile, answer in zip(PROFILES, got, strict=True):
            with self.subTest(profile=profile):
                self.assertEqual(answer, _python_profile(profile))

    def test_every_url_is_allowed_or_refused_the_same(self):
        got = self._rust({"urls": URLS})["urls"]
        for url, answer in zip(URLS, got, strict=True):
            with self.subTest(url=url):
                self.assertEqual(answer, url.startswith(community._BASE + "/"))

    def test_a_container_in_a_catalogue_field_is_a_recorded_difference(self):
        """Python renders it through `repr` - `[1, 2]`, `{'a': 1}` - and the
        port does not reproduce that. Our own index.json holds strings, so
        modelling Python's repr would be machinery for a shape that does not
        occur. Recorded here rather than left for somebody to find."""
        rows = [{"slug": "a", "exe": [1, 2]}]
        self.assertEqual(_python_index(rows)[0]["exe"], "[1, 2]")
        rust = self._rust({"indexes": [rows]})["indexes"][0]
        self.assertNotEqual(rust[0]["exe"], "[1, 2]")


if __name__ == "__main__":
    unittest.main()
