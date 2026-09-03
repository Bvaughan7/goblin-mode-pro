"""Shared test helpers. Pure stdlib so the suite runs on the system Python."""

from __future__ import annotations

import sys
from pathlib import Path

# tests are run from the repo root; make `import goblinmode` work without install
_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def write_mangohud_csv(path: Path, fps: list[float],
                       cpu_temp: list[float] | None = None,
                       gpu_temp: list[float] | None = None) -> None:
    """Write a MangoHud-style CSV: two preamble lines, a header, then rows."""
    cols = ["fps", "frametime"]
    if cpu_temp is not None:
        cols.append("cpu_temp")
    if gpu_temp is not None:
        cols.append("gpu_temp")
    cols.append("elapsed")
    lines = ["os,cpu,gpu,ram,kernel,driver\n", "Linux,x,y,16,6.0,570\n", ",".join(cols) + "\n"]
    for i, f in enumerate(fps):
        row = [f"{f}", "8.0"]
        if cpu_temp is not None:
            row.append(f"{cpu_temp[i % len(cpu_temp)]}")
        if gpu_temp is not None:
            row.append(f"{gpu_temp[i % len(gpu_temp)]}")
        row.append(f"{i * 200_000_000}")  # elapsed in ns
        lines.append(",".join(row) + "\n")
    path.write_text("".join(lines))


def typed(value):
    """A comparable that keeps what ``==`` throws away.

    Parity tests diff two implementations' answers, and ordinary equality is
    blind to two things those answers can differ in. A number's TYPE can carry
    meaning - ``0`` and ``0.0`` render differently, and Python says
    ``0 == 0.0 == -0.0`` - and dict equality ignores insertion order, which
    several of these formats treat as significant because Python dicts do.

    Found the hard way: two mutants survived a mutation run not because the
    corpus was too small but because the comparison could not see them.
    """
    if isinstance(value, bool):
        return ("bool", value)
    if isinstance(value, int):
        return ("int", value)
    if isinstance(value, float):
        return ("float", repr(value))
    if isinstance(value, dict):
        return ("dict", [(k, typed(v)) for k, v in value.items()])
    if isinstance(value, list):
        return ("list", [typed(v) for v in value])
    return (type(value).__name__, value)
