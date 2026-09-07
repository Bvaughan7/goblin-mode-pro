"""The four readers of the tweaks object agree about what is applied.

`GetStatus` carries one `tweaks` object and four things read it: the session
fingerprint stored with every run, the GUI dashboard's "active tweaks" row,
the CLI's status line, and the bug report's "Active tweaks" section. Each
spells its answer differently - one stores tokens, one shows translated words,
two print raw key names - and none of them may DISAGREE with another about
whether a thing is on.

Three bugs this week came from that object. The kernel scheduler reached none
of the four, so a swapped scheduler was invisible everywhere. A build was
adopted as a game by the one path that did not consult the blocklist. And the
governor read as tuned in two of the four on every machine there is, because
they tested a field holding a NAME for truthiness.

So this compares them on the concepts they share, over a corpus of the shapes
the daemon really sends. What a reader chooses to mention is its own business;
what it says about something it does mention is not.
"""

from __future__ import annotations

import itertools
import unittest

from tests._support import _SRC  # noqa: F401

from goblinmode import cli, report
from goblinmode.daemon import tweaks_fingerprint
from goblinmode.gui.labels import active_tweak_labels

#: concept -> how each reader spells it. `None` means that reader does not
#: report the concept at all, which is a choice rather than a disagreement.
SPELLINGS = {
    "governor": {
        "fingerprint": lambda out: "governor" in out,
        "dashboard": lambda out: "governor" in out,
        "cli": lambda line: "governor" in line,
        "report": lambda line: "governor" in line,
    },
    "tearing": {
        "fingerprint": lambda out: "tearing" in out,
        "dashboard": lambda out: "tearing" in out,
        "cli": lambda line: "tearing" in line,
        "report": lambda line: "tearing" in line,
    },
    "vrr": {
        "fingerprint": lambda out: "vrr" in out,
        "dashboard": lambda out: "VRR" in out,
        "cli": lambda line: "adaptive_sync" in line,
        "report": lambda line: "adaptive_sync" in line,
    },
    "power limit": {
        "fingerprint": lambda out: any(t.startswith("pl:") for t in out),
        "dashboard": lambda out: "power-limit" in out,
        "cli": lambda line: "power_limited" in line,
        "report": lambda line: "power_limited" in line,
    },
    "scheduler": {
        "fingerprint": lambda out: any(t.startswith("scx:") for t in out),
        "dashboard": lambda out: any(t.startswith("scx_") for t in out),
        "cli": lambda line: "scx_" in line,
        "report": lambda line: "scx_" in line,
    },
}


def _corpus():
    """The shapes a daemon really sends. Governors are NAMES."""
    for governor, epp, tearing, vrr, power, scx in itertools.product(
            ("powersave", "performance", "schedutil", None, ""),
            (False, True),
            (False, True),
            (False, True),
            (False, True),
            (None, "lavd")):
        tweaks = {
            "governor": governor,
            "epp_boosted": epp,
            "tearing": tearing,
            "adaptive_sync": vrr,
            "power_limited": power,
            "scx_scheduler": scx,
        }
        if power:
            tweaks["power_limits_w"] = [45, 60]
        yield tweaks


def _answers(tweaks: dict) -> dict:
    line = next(x for x in cli.status_lines({"tweaks": tweaks})
                if x.startswith("active tweaks"))
    markdown = report.as_markdown({"active_tweaks": tweaks}).splitlines()
    section = markdown[markdown.index("### Active tweaks") + 1]
    return {
        "fingerprint": tweaks_fingerprint(tweaks),
        "dashboard": active_tweak_labels(tweaks),
        "cli": line,
        "report": section,
    }


class TheFourReadersAgree(unittest.TestCase):
    def test_no_two_readers_disagree_about_a_concept_they_share(self):
        for tweaks in _corpus():
            answers = _answers(tweaks)
            for concept, readers in SPELLINGS.items():
                said = {name: test(answers[name])
                        for name, test in readers.items() if test is not None}
                with self.subTest(concept=concept, tweaks=tweaks):
                    self.assertEqual(
                        len(set(said.values())), 1,
                        f"the readers disagree about {concept}: {said}")

    def test_the_corpus_actually_turns_each_concept_on_and_off(self):
        """A corpus where a concept is never on would let every reader agree
        by saying no - which is how two of these bugs survived."""
        seen: dict[str, set] = {c: set() for c in SPELLINGS}
        for tweaks in _corpus():
            answers = _answers(tweaks)
            for concept, readers in SPELLINGS.items():
                seen[concept].add(readers["fingerprint"](answers["fingerprint"]))
        for concept, values in seen.items():
            with self.subTest(concept=concept):
                self.assertEqual(values, {True, False})


if __name__ == "__main__":
    unittest.main()
