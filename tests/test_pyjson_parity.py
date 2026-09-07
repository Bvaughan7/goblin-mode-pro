"""The Rust JSON renderer and CPython's `json.dumps` agree, byte for byte.

Two documents this project writes are compared as TEXT rather than as parsed
data: the payload a user pastes into a model, and the applied-state file a cold
`--revert` reads. `serde_json` writes neither of them the way CPython does, and
the difference is not exotic - `ensure_ascii` is CPython's default, so every
character above U+007F becomes a `\\uXXXX` escape, astral characters become a
surrogate PAIR, and DEL is escaped where `serde_json` leaves it alone.

The corpus is generated rather than written out, from an alphabet weighted
towards the characters that decide the answer: accented letters, a trademark
sign, an emoji, a tag character, a combining mark, U+2028, DEL, the control
characters with a shortcut escape and the ones without. A hand-written corpus
covers the cases its author thought of, which is exactly the set a
hand-written escaper already handles.
"""

from __future__ import annotations

import json
import os
import random
import subprocess
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent

#: Printable ASCII, plus every character that has ever made two JSON encoders
#: disagree.
ALPHABET = [chr(i) for i in range(0x20, 0x7F)] + [
    chr(c) for c in (0xE9, 0x2122, 0x20AC, 0x1F525, 0xE0041, 0x00, 0x08, 0x09,
                     0x0A, 0x0C, 0x0D, 0x1B, 0x7F, 0x22, 0x5C, 0x2F, 0xA0,
                     0x2028, 0xFFFD, 0x301)
]

NUMBERS = [0, 1, -7, 1234567890, 2 ** 53, 0.0, 1.5, -0.25, 3.0, 1e16, 1e-7,
           55.15, 2.5, -0.0]


def _binary() -> Path | None:
    override = os.environ.get("GMP_PYJSON_BIN")
    if override:
        return Path(override) if Path(override).exists() else None
    for profile in ("debug", "release"):
        candidate = _REPO / "target" / profile / "examples" / "pyjson"
        if candidate.exists():
            return candidate
    return None


def _corpus(count: int) -> list:
    rng = random.Random(20260906)

    def text():
        return "".join(rng.choice(ALPHABET)
                       for _ in range(rng.randint(0, 14)))

    def value(depth=0):
        kind = rng.random()
        if depth > 2 or kind < 0.35:
            pick = rng.random()
            if pick < 0.3:
                return text()
            if pick < 0.5:
                return rng.choice(NUMBERS)
            if pick < 0.7:
                return rng.choice([True, False])
            return None
        if kind < 0.7:
            return [value(depth + 1) for _ in range(rng.randint(0, 4))]
        return {text(): value(depth + 1) for _ in range(rng.randint(0, 4))}

    return [{"indent": rng.choice([0, 2]), "value": value()}
            for _ in range(count)]


class BothImplementationsRenderTheSameBytes(unittest.TestCase):
    def setUp(self):
        self.binary = _binary()
        if self.binary is None:
            if os.environ.get("GMP_REQUIRE_RUST_HELPER") == "1":
                self.fail("GMP_REQUIRE_RUST_HELPER=1 but the pyjson example is "
                          "not built - run `cargo build -p gmp-core "
                          "--example pyjson`")
            self.skipTest("build it with `cargo build -p gmp-core "
                          "--example pyjson`")

    def _rust(self, documents: list) -> list:
        r = subprocess.run([str(self.binary)],
                           input=json.dumps({"documents": documents}),
                           capture_output=True, text=True, timeout=120,
                           check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_a_generated_corpus_renders_identically(self):
        documents = _corpus(2000)
        want = [json.dumps(d["value"], indent=d["indent"] or None)
                for d in documents]
        got = self._rust(documents)
        self.assertEqual(len(got), len(want))
        for i, (w, g) in enumerate(zip(want, got, strict=True)):
            if w != g:
                self.fail(f"document {i} differs\n  py: {w!r}\n  rs: {g!r}")

    def test_the_characters_that_decide_it(self):
        """Named separately so a failure says which rule broke rather than
        which document number did."""
        cases = {
            "non-ASCII is escaped": "Pok\u00e9mon",
            "astral is a surrogate pair": "\U0001f525",
            "DEL is escaped": "\u007f",
            "shortcut escapes": "\b\f\n\r\t",
            "other control characters": "\u0000\u001b\u001f",
            "a slash is left alone": "/home/x",
            "a quote and a backslash": '"\\',
            "a line separator": "\u2028",
        }
        documents = [{"indent": 0, "value": v} for v in cases.values()]
        got = self._rust(documents)
        for (name, value), rendered in zip(cases.items(), got, strict=True):
            with self.subTest(rule=name):
                self.assertEqual(rendered, json.dumps(value))


if __name__ == "__main__":
    unittest.main()
