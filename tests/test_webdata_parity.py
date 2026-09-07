"""The Rust and Python web-lookup checks agree.

Two datasets fetched from a fixed host allowlist, size-capped, cached. The
fetching stays in Python; what is compared is every check and every projection
that runs on what comes back.

The digit sweep is the part worth having. `str.isdigit` is neither
`is_ascii_digit` nor `is_numeric`: it takes Arabic-Indic numerals and the
circled and superscript forms, and leaves out fractions and Roman numerals. The
filter it drives builds a URL, so the two implementations agreeing about it is
not a detail.
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

from goblinmode import webdata

_REPO = Path(__file__).resolve().parent.parent

URLS = [
    "https://www.protondb.com/api/v1/reports/summaries/1091500.json",
    "https://raw.githubusercontent.com/AreWeAntiCheatYet/x/HEAD/games.json",
    "http://www.protondb.com/x",
    "https://protondb.com/x",
    "https://evil.example/x",
    "https://www.protondb.com@evil.example/x",
    "https://evil.example/www.protondb.com/x",
    "https://www.protondb.com:443/x",
    "www.protondb.com/x",
    "",
    "https://",
    "https://www.protondb.com",
]

APPIDS = [
    "1091500", " 109 1500 ", "app/1091500/", "none", "", "0",
    "1234567890123456", 1091500, 0, None, True, 2.5,
    # Digits Python takes and ASCII intuition does not.
    "\u0663\u0664", "\u00b2\u00b3", "\u2460", "\u2160", "\u00bd",
    "12\u066334",
]

SUMMARIES = [
    {"tier": "gold", "score": 0.8, "total": 500, "confidence": "good",
     "trendingTier": "gold", "bestReportedTier": "platinum"},
    {"tier": "gold"},
    {"score": 9},
    {},
    {"tier": None},
    [],
    "not an object",
    {"tier": "gold", "extra": "dropped"},
]

DB = [
    {"name": "Apex Legends", "status": "Denied",
     "storeIds": {"steam": "1172470"},
     "anticheats": ["Easy Anti-Cheat"], "reference": "https://x"},
    {"name": "Other Game", "status": "Supported", "storeIds": {}},
    {"name": "Bare", "storeIds": {"steam": "5"}},
    {"name": "Noisy", "storeIds": {"steam": "6"},
     "anticheats": ["a", 1, "b", None, "c", "d", "e", "f", "g", "h"]},
    {"name": " Spaced Out ", "storeIds": {"steam": " 7 "}},
    "not an object",
    {"storeIds": {"steam": "8"}},
]

LOOKUPS = [
    {"name": "", "app_id": "1172470"},
    {"name": "  APEX legends ", "app_id": ""},
    {"name": "apex legends", "app_id": "1172470"},
    {"name": "", "app_id": ""},
    {"name": "Nothing", "app_id": ""},
    {"name": "", "app_id": "999"},
    {"name": "", "app_id": "5"},
    {"name": "", "app_id": "6"},
    {"name": "spaced out", "app_id": ""},
    {"name": "", "app_id": "7"},
    {"name": "", "app_id": "8"},
]

#: Every printable character below U+3000, then a stride over the rest.
SWEEP = "".join(
    [chr(c) for c in range(0x20, 0x3000) if chr(c).isprintable()]
    + [chr(c) for c in range(0x3000, 0x110000, 3)
       if chr(c).isprintable() and not 0xD800 <= c <= 0xDFFF]
)


#: (mtime, now, ttl) - either side of the boundary, and exactly on it.
CACHES = [
    (100.0, 150.0, 60.0),
    (100.0, 159.9, 60.0),
    (100.0, 160.0, 60.0),
    (100.0, 160.1, 60.0),
    (100.0, 200.0, 60.0),
    (100.0, 100.0, 60.0),
    (100.0, 90.0, 60.0),
    (100.0, 150.0, 0.0),
]


def _binary() -> Path | None:
    override = os.environ.get("GMP_WEBDATA_BIN")
    if override:
        return Path(override) if Path(override).exists() else None
    for profile in ("debug", "release"):
        candidate = _REPO / "target" / profile / "examples" / "webdata"
        if candidate.exists():
            return candidate
    return None


def _python_url(url: str) -> bool:
    host = url.split("/", 3)[2] if "://" in url else ""
    return host in webdata._ALLOWED_HOSTS and url.startswith("https://")


def _python_appids(value):
    digits = "".join(c for c in str(value) if c.isdigit())
    return [digits[:12], digits]


def _python_summary(data):
    if not isinstance(data, dict) or "tier" not in data:
        return None
    return {k: data.get(k) for k in
            ("tier", "score", "total", "confidence", "trendingTier",
             "bestReportedTier")}


def _python_lookup(name: str, app_id: str):
    with patch.object(webdata, "_anticheat_db", lambda: DB):
        return webdata.anticheat_status(name=name, app_id=app_id)


def _python_cache_fresh(mtime: float, now: float, ttl: float) -> bool:
    """Whether `_cached_json` would use the file, asked through the real
    thing: a file on disk with that mtime, and a clock that says `now`."""
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "cache.json"
        path.write_text(json.dumps({"cached": True}))
        os.utime(path, (mtime, mtime))
        with patch("goblinmode.webdata.time.time", lambda: now):
            return webdata._cached_json(path, ttl) is not None


class BothImplementationsCheckTheSameWay(unittest.TestCase):
    def setUp(self):
        self.binary = _binary()
        if self.binary is None:
            if os.environ.get("GMP_REQUIRE_RUST_HELPER") == "1":
                self.fail("GMP_REQUIRE_RUST_HELPER=1 but the webdata example "
                          "is not built - run `cargo build -p gmp-core "
                          "--example webdata`")
            self.skipTest("build it with `cargo build -p gmp-core "
                          "--example webdata`")

    def _rust(self, payload: dict) -> dict:
        r = subprocess.run([str(self.binary)], input=json.dumps(payload),
                           capture_output=True, text=True, timeout=120,
                           check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_every_url_is_allowed_or_refused_the_same(self):
        got = self._rust({"urls": URLS})["urls"]
        for url, answer in zip(URLS, got, strict=True):
            with self.subTest(url=url):
                self.assertEqual(answer, _python_url(url))

    def test_every_app_id_is_reduced_the_same(self):
        got = self._rust({"appids": APPIDS})["appids"]
        for value, answer in zip(APPIDS, got, strict=True):
            with self.subTest(value=value):
                self.assertEqual(answer, _python_appids(value))

    def test_the_digit_test_agrees_across_a_unicode_sweep(self):
        got = self._rust({"digits": SWEEP})["digits"]
        want = "".join("1" if c.isdigit() else "0" for c in SWEEP)
        if got != want:
            wrong = [f"U+{ord(c):04X}" for c, a, b in
                     zip(SWEEP, got, want, strict=True) if a != b]
            self.fail(f"{len(wrong)} of {len(SWEEP)} characters differ: "
                      f"{wrong[:8]}")

    def test_a_cache_goes_stale_at_the_same_moment(self):
        """Asked through the real reader, which is where the comparison and
        the clock both live. Exactly on the TTL is stale, not fresh."""
        payload = [{"mtime": m, "now": n, "ttl": t} for m, n, t in CACHES]
        got = self._rust({"caches": payload})["caches"]
        for (mtime, now, ttl), answer in zip(CACHES, got, strict=True):
            with self.subTest(mtime=mtime, now=now, ttl=ttl):
                self.assertEqual(answer, _python_cache_fresh(mtime, now, ttl))

    def test_every_summary_is_projected_the_same(self):
        got = self._rust({"summaries": SUMMARIES})["summaries"]
        for data, answer in zip(SUMMARIES, got, strict=True):
            with self.subTest(data=data):
                self.assertEqual(answer, _python_summary(data))

    def test_every_lookup_finds_the_same_entry(self):
        payload = [{"db": DB, **row} for row in LOOKUPS]
        got = self._rust({"lookups": payload})["lookups"]
        for row, answer in zip(LOOKUPS, got, strict=True):
            with self.subTest(**row):
                self.assertEqual(answer,
                                 _python_lookup(row["name"], row["app_id"]))


if __name__ == "__main__":
    unittest.main()
