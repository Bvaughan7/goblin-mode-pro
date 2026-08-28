"""One-click bug report.

Gathers everything you'd otherwise paste by hand into a forum thread or a GitHub
issue - system info, the pre-flight check results, the latest incident, an
analysis of the newest Wine/Proton log, and the tweaks that were active - and
renders it three ways:

* :func:`as_markdown` - a clean paste for a forum / issue body
* :func:`as_llm_prompt` - wrapped in a diagnostic system prompt
* :func:`github_issue_url` - a pre-filled new-issue link
"""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
import urllib.parse
from datetime import datetime, timezone
from typing import Any

from goblinmode import __version__, logrules, preflight
from goblinmode.incidents import _system_info
from goblinmode.runner import latest_log_files

_LLM_PROMPT = (
    "You are a Linux gaming support engineer. Below is an automatically collected "
    "report from Goblin Mode Pro: system info, a pre-flight system check, the most "
    "recent in-game incident, an analysis of the Wine/Proton log, and the "
    "performance tweaks that were active. Diagnose the most likely problem and give "
    "concrete, distro-appropriate fix steps ordered by expected impact. Be concise."
)


def _mesa_version() -> str | None:
    if not shutil.which("glxinfo"):
        return None
    try:
        out = subprocess.run(["glxinfo", "-B"], capture_output=True, text=True, timeout=6).stdout
        for line in out.splitlines():
            if "OpenGL version string" in line:
                return line.split(":", 1)[1].strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def _desktop() -> dict[str, str]:
    import os

    return {
        "desktop": os.environ.get("XDG_CURRENT_DESKTOP", "?"),
        "session_type": os.environ.get("XDG_SESSION_TYPE", "?"),
    }


def _ram_gb() -> float | None:
    try:
        import psutil

        return round(psutil.virtual_memory().total / (1024 ** 3), 1)
    except Exception:  # noqa: BLE001
        return None


def build_report(
    *,
    incident: dict | None = None,
    game: str = "",
    active_tweaks: dict | None = None,
    user_note: str = "",
) -> dict[str, Any]:
    sysinfo = _system_info()
    sysinfo.update(_desktop())
    sysinfo["ram_gb"] = _ram_gb()
    sysinfo["mesa_gl"] = _mesa_version()
    sysinfo["gmp_version"] = __version__

    pf = preflight.run_all()
    pf_flags = [r for r in pf if r["status"] in ("warn", "fail")]

    log_findings: list[dict] = []
    logs = latest_log_files(limit=1)
    log_name = ""
    if logs:
        log_name = logs[0].name
        try:
            text = logs[0].read_text(errors="replace")[-200_000:]
            log_findings = [f.__dict__ for f in logrules.analyze_text(text)]
        except OSError:
            pass

    return {
        "schema": "gmp.report.v1",
        "generated": datetime.now(timezone.utc).isoformat(),
        "system": sysinfo,
        "game": game or (incident or {}).get("game", ""),
        "user_note": user_note,
        "preflight_summary": preflight.summary(pf),
        "preflight_flags": pf_flags,
        "log_file": log_name,
        "log_findings": log_findings,
        "incident": incident,
        "active_tweaks": active_tweaks or (incident or {}).get("active_tweaks", {}),
    }


# --------------------------------------------------------------------------
# renderers
# --------------------------------------------------------------------------
def as_markdown(rep: dict) -> str:
    s = rep["system"]
    L: list[str] = []
    L.append(f"## Goblin Mode Pro report — {rep['generated'][:19]}Z")
    if rep.get("user_note"):
        L.append(f"\n> {rep['user_note']}\n")
    L.append("\n### System")
    L.append(f"- **CPU** {s.get('cpu','?')}")
    L.append(f"- **GPU** {s.get('gpu','?')}  ·  driver {s.get('nvidia_driver', s.get('mesa_gl','?'))}")
    L.append(f"- **Kernel** {s.get('kernel','?')}  ·  {s.get('distro','?')}  ·  {s.get('desktop','?')} / {s.get('session_type','?')}")
    L.append(f"- **RAM** {s.get('ram_gb','?')} GB  ·  GMP {s.get('gmp_version','?')}")
    if rep.get("game"):
        L.append(f"- **Game** {rep['game']}")

    n = rep["preflight_summary"]
    L.append(f"\n### Pre-flight  ({n.get('ok',0)} ok · {n.get('warn',0)} warn · {n.get('fail',0)} fail)")
    if rep["preflight_flags"]:
        for r in rep["preflight_flags"]:
            L.append(f"- **{r['status'].upper()}** {r['title']} = `{r['value']}` — {r['detail'] or r['why']}")
    else:
        L.append("- all clear")

    L.append("\n### Wine/Proton log" + (f"  (`{rep['log_file']}`)" if rep["log_file"] else ""))
    if rep["log_findings"]:
        for f in rep["log_findings"]:
            L.append(f"- **{f['label']}** ×{f['count']} ({f['category']}) — {f['cause']}")
            L.append(f"  - fix: {f['fix']}")
            L.append(f"  - `{f['sample']}`")
    else:
        L.append("- no known failure patterns matched" if rep["log_file"] else "- no captured log (set the Steam launch option / Lutris command prefix)")

    if rep.get("incident"):
        inc = rep["incident"]
        L.append(f"\n### Last incident — {inc.get('kind','?')}")
        L.append(f"- {inc.get('detail','')}")
        gs = inc.get("gpu_state") or {}
        if gs:
            L.append(f"- GPU: {gs.get('vram_used_mb')}/{gs.get('vram_total_mb')} MB VRAM · "
                     f"PCIe Gen{gs.get('pcie_gen')}×{gs.get('pcie_width')} · pstate {gs.get('pstate')} · "
                     f"clock {gs.get('clock_gfx_mhz')}/{gs.get('clock_gfx_max_mhz')} MHz")

    tw = rep.get("active_tweaks") or {}
    if tw:
        on = [k for k in ("governor", "epp_boosted", "tearing", "adaptive_sync",
                          "power_limited") if tw.get(k)]
        L.append(f"\n### Active tweaks\n- {', '.join(on) or 'none'} · reniced {list((tw.get('reniced') or {}).keys())}")
    return "\n".join(L) + "\n"


def as_llm_prompt(rep: dict) -> str:
    return _LLM_PROMPT + "\n\n```json\n" + json.dumps(rep, indent=2, default=str) + "\n```\n"


def github_issue_url(rep: dict, repo: str = "your-org/goblin-mode-pro") -> str:
    body = as_markdown(rep)
    if len(body) > 6000:
        body = body[:6000] + "\n\n*(truncated — full report on the clipboard)*\n"
    q = urllib.parse.urlencode({
        "title": f"[{rep.get('game') or 'game'}] ",
        "body": body,
        "labels": "triage",
    })
    return f"https://github.com/{repo}/issues/new?{q}"
