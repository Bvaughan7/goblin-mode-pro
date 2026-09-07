"""Every field of a record reaches the dict that record is sent as.

Three dataclasses write their own `as_dict` instead of using `asdict`, because
each shapes the output - rounding, renaming, dropping an empty section. A
hand-written projection is also where a field can quietly fail to appear, and
that is not a cosmetic bug: it is the one this codebase already shipped.

`TweakStatus.scx_scheduler` existed for as long as sched_ext support did and
never reached the dict, so the CLI had a branch rendering it that could never
run, the session fingerprint recorded every tweak but that one, and the
dashboard's "active tweaks" row never mentioned the biggest lever in the tool.
Nothing failed; three readers were simply blind.

So this is written as the rule rather than as the instance. A field that is
deliberately internal has to say so here, which is a sentence someone must
write on purpose rather than an omission anyone can make by accident.
"""

from __future__ import annotations

import dataclasses
import unittest

from tests._support import _SRC  # noqa: F401

from goblinmode.diagnostics import Sample
from goblinmode.incidents import Incident
from goblinmode.payload import TweakStatus

#: Fields that are deliberately not part of the record, and why.
INTERNAL: dict[type, dict[str, str]] = {
    Sample: {
        "t": "a monotonic timestamp - meaningless outside the process that "
             "took it, and the reader has its own clock",
    },
}


def _value_for(annotation: str):
    """Something truthy of the right shape, so a projection that drops empty
    sections still has to carry every field.

    The order of these tests is the whole of it: `tuple[int, int]` and
    `list[float]` both mention a scalar type, and answering that scalar would
    hand a container-shaped field a number.
    """
    text = annotation if isinstance(annotation, str) else str(annotation)
    if "tuple" in text:
        return (1, 2)
    if "list" in text:
        if "dict" in text:
            return [{"x": 1}]
        if "float" in text:
            return [1.5]
        if "int" in text:
            return [1]
        return ["x"]
    if "dict" in text:
        return {"x": 1}
    if "bool" in text:
        return True
    if "float" in text:
        return 1.5
    if "int" in text:
        return 1
    return "x"


def _populated(cls):
    """An instance with every field set."""
    return cls(**{f.name: _value_for(f.type) for f in dataclasses.fields(cls)})


class EveryFieldReachesTheRecord(unittest.TestCase):
    def test_a_fully_populated_record_carries_all_of_its_fields(self):
        for cls in (Sample, Incident, TweakStatus):
            with self.subTest(record=cls.__name__):
                fields = {f.name for f in dataclasses.fields(cls)}
                internal = set(INTERNAL.get(cls, {}))
                reached = set(_populated(cls).as_dict())
                missing = sorted(fields - internal - reached)
                self.assertEqual(
                    missing, [],
                    f"{cls.__name__} has fields nothing downstream can see: "
                    f"{missing}. Either put them in `as_dict` or name them in "
                    f"INTERNAL with the reason.")

    def test_nothing_is_excused_that_does_not_exist(self):
        """The other direction: a field renamed or removed leaves a stale
        excuse behind, and a stale excuse would hide the next real one."""
        for cls, excuses in INTERNAL.items():
            fields = {f.name for f in dataclasses.fields(cls)}
            with self.subTest(record=cls.__name__):
                self.assertEqual(sorted(set(excuses) - fields), [])

    def test_the_record_invents_nothing(self):
        """A key in the dict that is not a field is a name only one side
        knows, which is how a reader ends up looking for something that will
        never be there."""
        for cls in (Sample, Incident, TweakStatus):
            with self.subTest(record=cls.__name__):
                fields = {f.name for f in dataclasses.fields(cls)}
                extra = sorted(set(_populated(cls).as_dict()) - fields)
                self.assertEqual(extra, [])


if __name__ == "__main__":
    unittest.main()
