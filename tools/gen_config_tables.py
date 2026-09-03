#!/usr/bin/env python3
"""Emit crates/gmp-core/src/config_tables.rs from src/goblinmode/config.py.

Generated rather than retyped, for the same reason the logrules and preflight
tables are: these are the values a profile is normalised against, and a typo in
one of them silently changes what a user's saved settings mean. Reading them
out of the Python at generation time means the two cannot drift.

Usage: python3 tools/gen_config_tables.py > crates/gmp-core/src/config_tables.rs
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from goblinmode import config as C


def rs_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def main() -> None:
    out: list[str] = []
    out.append("//! Tables a profile is normalised against.")
    out.append("//!")
    out.append("//! GENERATED from `src/goblinmode/config.py` by")
    out.append("//! `tools/gen_config_tables.py`. Do not edit by hand - a typo here")
    out.append("//! silently changes what a user's saved settings mean.")
    out.append("")
    out.append("/// A set of environment assignments: `(name, value)` pairs.")
    out.append("pub type EnvSet = &'static [(&'static str, &'static str)];")
    out.append("")
    out.append("/// Runner-variable toggle -> the env assignments it applies when on.")
    out.append("pub const RUNNER_VARS: &[(&str, EnvSet)] = &[")
    for k, env in C.RUNNER_VARS.items():
        pairs = ", ".join(f"({rs_str(a)}, {rs_str(b)})" for a, b in env.items())
        out.append(f"    ({rs_str(k)}, &[{pairs}]),")
    out.append("];")
    out.append("")
    out.append("/// Vendor GPU-driver tuning: (vendor, key, env assignments).")
    out.append("/// The label is not carried - it is GUI text and reaches no decision.")
    out.append("pub const GPU_TUNING_VARS: &[(&str, &str, EnvSet)] = &[")
    for vendor, keys in C.GPU_TUNING_VARS.items():
        for key, (_label, env) in keys.items():
            pairs = ", ".join(f"({rs_str(a)}, {rs_str(b)})" for a, b in env.items())
            out.append(f"    ({rs_str(vendor)}, {rs_str(key)}, &[{pairs}]),")
    out.append("];")
    out.append("")
    for name, seq in [
        ("MATCH_MODES", C.MATCH_MODES),
        ("CORE_PIN_MODES", C.CORE_PIN_MODES),
        ("SCX_MODES", C.SCX_MODES),
        ("GAMESCOPE_UPSCALERS", C.GAMESCOPE_UPSCALERS),
    ]:
        out.append(f"pub const {name}: &[&str] = &[{', '.join(rs_str(x) for x in seq)}];")
    out.append("")
    out.append("/// MangoHud toggle defaults, in the order the Python builds them.")
    out.append("pub const DEFAULT_MANGOHUD: &[(&str, bool)] = &[")
    for k, v in C._default_mangohud().items():
        out.append(f"    ({rs_str(k)}, {str(v).lower()}),")
    out.append("];")
    out.append("")
    out.append("pub const DEFAULT_RUNNER_VARS: &[(&str, bool)] = &[")
    for k, v in C._default_runner_vars().items():
        out.append(f"    ({rs_str(k)}, {str(v).lower()}),")
    out.append("];")
    out.append("")
    out.append("/// gamescope defaults. Mixed types, so each is spelled out.")
    out.append("pub fn default_gamescope() -> serde_json::Map<String, serde_json::Value> {")
    out.append("    let mut map = serde_json::Map::new();")
    for k, v in C._default_gamescope().items():
        if isinstance(v, bool):
            lit = str(v).lower()
        elif isinstance(v, str):
            lit = rs_str(v)
        else:
            lit = str(v)
        out.append(f"    map.insert({rs_str(k)}.to_string(), serde_json::json!({lit}));")
    out.append("    map")
    out.append("}")
    text = "\n".join(out) + "\n"
    # Formatted here rather than left to `cargo fmt`, so that regenerating is
    # idempotent and CI can diff the output against the committed file.
    if shutil.which("rustfmt"):
        text = subprocess.run(["rustfmt", "--edition", "2021", "--emit", "stdout"],
                              input=text, capture_output=True, text=True,
                              check=True).stdout
        # rustfmt prefixes its stdout output with a filename banner.
        text = text.split("\n", 1)[1] if text.startswith("stdout:") else text
    sys.stdout.write(text)


if __name__ == "__main__":
    main()
