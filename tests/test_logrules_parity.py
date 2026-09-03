"""The Rust and Python log rules give the same answers.

This is the domain-logic equivalent of the D-Bus interface freeze. Rather than
asserting that the Rust port is correct - which its own tests can only ever
agree with - it asks both implementations the same question and diffs the
answers. A rule whose regex differs by one character between them is otherwise
invisible: both sides pass their own tests and the two disagree about a log.

The Rust side answers through `cargo run -p gmp-core --example analyze`, which
reads a log on stdin and prints its findings as JSON. That example exists for
this test and is not shipped.

The corpus below is the fixture both sides are graded on. Adding a rule to
`logrules` means adding a line here that trips it.
"""

from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path

from tests._support import _SRC  # noqa: F401

from goblinmode import logrules

_REPO = Path(__file__).resolve().parent.parent

#: One line per rule, plus the awkward cases: repeats, a line that trips two
#: rules at once, leading whitespace, and a very long line.
CORPUS = "\n".join([
    "esync: up to 4096 handles",
    "fsync: warning falling back",
    "VK_ERROR_OUT_OF_DEVICE_MEMORY allocating a texture",
    "VK_ERROR_DEVICE_LOST in vkQueueSubmit",
    "VK_ERROR_DEVICE_LOST again",
    "std::bad_alloc thrown",
    "EasyAntiCheat failed to init",
    "wine: failed to load mscoree",
    "err:module:import_dll api-ms-win-crt-runtime-l1-1-0.dll not found",
    # the same rule via its other alternative - a rule with alternatives is
    # only really covered when each branch is exercised, or a change to one
    # of them hides behind the others still matching
    "vcruntime140.dll not found",
    "err:module:MSVCP140 missing",
    "Failed to create D3D11 device",
    "Failed to load vulkan loader",
    "Shader cache disabled, read-only",
    "pressure-vessel: failed to start",
    "   wine: Unhandled page fault in module x   ",
    "amdgpu: ring gfx timeout, GPU reset",
    "NVRM: Xid (PCI:0000:01:00): 79",
    "libGL error: failed to load driver: iris",
    "nothing interesting on this line at all",
    "std::bad_alloc " + "x" * 600,
])


def _rust_binary() -> Path | None:
    override = os.environ.get("GMP_ANALYZE_BIN")
    if override:
        return Path(override) if Path(override).exists() else None
    for profile in ("debug", "release"):
        candidate = _REPO / "target" / profile / "examples" / "analyze"
        if candidate.exists():
            return candidate
    return None


class BothImplementationsAgree(unittest.TestCase):
    def setUp(self):
        self.binary = _rust_binary()
        if self.binary is None:
            if os.environ.get("GMP_REQUIRE_RUST_HELPER") == "1":
                self.fail("GMP_REQUIRE_RUST_HELPER=1 but the analyze example is "
                          "not built - run `cargo build -p gmp-core --example analyze`")
            self.skipTest("build it with `cargo build -p gmp-core --example analyze`")

    def _rust(self, text: str, appid: str = "") -> list[dict]:
        proc = subprocess.run(
            [str(self.binary), appid], input=text, capture_output=True,
            text=True, timeout=60, check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def _python(self, text: str, appid: str = "") -> list[dict]:
        return [f.__dict__ for f in logrules.analyze_text(text, appid=appid)]

    def test_the_two_agree_on_the_whole_corpus(self):
        """Every field of every finding, in order.

        Order is part of the answer: the list is sorted most-severe-first and a
        user reads the top of it, so two implementations that agree on the set
        but not the order do not agree.
        """
        self.assertEqual(self._python(CORPUS), self._rust(CORPUS))

    def test_the_two_agree_with_an_appid_substituted(self):
        self.assertEqual(self._python(CORPUS, "123456"), self._rust(CORPUS, "123456"))

    def test_the_corpus_actually_trips_most_of_the_rule_base(self):
        """A corpus that matched nothing would make the tests above pass
        vacuously - which is the failure this project has shipped before."""
        matched = {f["rule_id"] for f in self._python(CORPUS)}
        self.assertGreaterEqual(len(matched), len(logrules.RULES) - 1,
                                f"corpus misses: "
                                f"{{r.id for r in logrules.RULES}} - {matched}")

    def test_the_two_agree_that_nothing_matched(self):
        self.assertEqual(self._python("all quiet"), self._rust("all quiet"))

    def test_the_two_agree_on_repeats_and_truncation(self):
        text = "std::bad_alloc " + "y" * 900 + "\nstd::bad_alloc again"
        py, rs = self._python(text), self._rust(text)
        self.assertEqual(py, rs)
        self.assertEqual(py[0]["count"], 2)
        self.assertEqual(len(py[0]["sample"]), 300)


class TheRuleTablesMatch(unittest.TestCase):
    """The constants are the specification, so they are compared directly.

    Read out of the Rust source rather than from a running binary: the table is
    `const` data, and a mismatch should fail without anything needing to be
    built. The generator that produced the Rust table read the Python one, so
    this starts true; the point is that it stays true.
    """

    def _rust_rules(self) -> list[dict]:
        import re
        src = (_REPO / "crates/gmp-core/src/logrules.rs").read_text()
        block = src.split("pub const RULES: &[Rule] = &[", 1)[1].split("\n];", 1)[0]
        out = []
        for chunk in block.split("Rule {")[1:]:
            row = {}
            for field in ("id", "pattern", "label", "category", "cause", "fix", "severity"):
                m = re.search(rf'^\s*{field}: r(#*)"(.*?)"\1,\s*$', chunk, re.M | re.S)
                row[field] = m.group(2) if m else None
            row["live"] = bool(re.search(r"^\s*live: true,", chunk, re.M))
            m = re.search(r'^\s*fix_cmd: Some\(r(#*)"(.*?)"\1\),\s*$', chunk, re.M | re.S)
            row["fix_cmd"] = m.group(2) if m else None
            out.append(row)
        return out

    def test_every_rule_matches_field_for_field(self):
        rust = self._rust_rules()
        self.assertEqual(len(rust), len(logrules.RULES),
                         "the two rule tables are different lengths")
        for py, rs in zip(logrules.RULES, rust, strict=True):
            with self.subTest(rule=py.id):
                self.assertEqual(py.id, rs["id"])
                self.assertEqual(py.pattern, rs["pattern"], "the regex differs")
                self.assertEqual(py.label, rs["label"])
                self.assertEqual(py.category, rs["category"])
                self.assertEqual(py.cause, rs["cause"])
                self.assertEqual(py.fix, rs["fix"])
                self.assertEqual(py.severity, rs["severity"])
                self.assertEqual(py.live, rs["live"])
                self.assertEqual(py.fix_cmd, rs["fix_cmd"])

    def test_the_order_matches_too(self):
        """Ties in the sort fall back to table order, so the tables agreeing as
        sets is not enough."""
        self.assertEqual([r.id for r in logrules.RULES],
                         [r["id"] for r in self._rust_rules()])


if __name__ == "__main__":
    unittest.main()
