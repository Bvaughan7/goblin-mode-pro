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
