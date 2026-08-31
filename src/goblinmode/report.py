"""One-click bug report.

Collects the context a support request needs - system info, the pre-flight
results, the latest incident, an analysis of the newest Wine/Proton log, and the
tweaks that were active - and renders it three ways:

* :func:`as_markdown` - a paste for a forum thread or an issue body
* :func:`as_llm_prompt` - wrapped in a diagnostic system prompt
* :func:`github_issue_url` - a pre-filled new-issue link

Home paths and the login name are redacted from anything that came from a log.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.parse
from datetime import datetime, UTC
from typing import Any

from goblinmode import __version__, preflight
from goblinmode.incidents import _system_info
from goblinmode.logrules import analyze_text, redact as _redact
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


def _redact_incident(incident: dict | None) -> dict | None:
    if not incident:
        return incident
    inc = dict(incident)
    if inc.get("logs_tail"):
        inc["logs_tail"] = [_redact(str(line)) for line in inc["logs_tail"]]
    if inc.get("detail"):
        inc["detail"] = _redact(str(inc["detail"]))
    return inc


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
            for f in analyze_text(text):
                d = dict(f.__dict__)
                d["sample"] = _redact(d.get("sample", ""))
                log_findings.append(d)
        except OSError:
            pass

    incident = _redact_incident(incident)

    # The read-only selftest: which privileged paths this machine actually has
    # and which the helper can reach. It is the single most useful thing in a
    # bug report, because "it doesn't work" is usually a capability the machine
    # never had. Read-only, so building a report never changes anything, and
    # best-effort - a report that fails because a probe did would be worse than
    # one missing this section.
    capability_selftest: dict | None = None
    try:
        from goblinmode import selftest
        capability_selftest = selftest.to_json(selftest.SelfTest().run(), apply=False)
    except Exception as exc:                        # noqa: BLE001
        capability_selftest = {"error": f"{type(exc).__name__}: {exc}"}

    return {
        "schema": "gmp.report.v1",
        "generated": datetime.now(UTC).isoformat(),
        "system": sysinfo,
        "game": game or (incident or {}).get("game", ""),
        "user_note": user_note,
        "preflight_summary": preflight.summary(pf),
        "preflight_flags": pf_flags,
        "log_file": log_name,
        "log_findings": log_findings,
        "incident": incident,
        "active_tweaks": active_tweaks or (incident or {}).get("active_tweaks", {}),
        "capability_selftest": capability_selftest,
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
            L.append(f"  - `{str(f['sample']).replace('`', '')[:200]}`")
    elif rep["log_file"]:
        L.append("- no known failure patterns matched")
    else:
        L.append("- no captured log (set the launch option / command prefix to `goblin-run %command%`)")

    if rep.get("incident"):
        inc = rep["incident"]
        L.append(f"\n### Last incident — {inc.get('kind','?')}")
        L.append(f"- {inc.get('detail','')}")
        gs = inc.get("gpu_state") or {}
        if gs:
            L.append(f"- GPU: {gs.get('vram_used_mb')}/{gs.get('vram_total_mb')} MB VRAM · "
                     f"PCIe Gen{gs.get('pcie_gen')}×{gs.get('pcie_width')} · pstate {gs.get('pstate')} · "
                     f"clock {gs.get('clock_gfx_mhz')}/{gs.get('clock_gfx_max_mhz')} MHz")

    st = rep.get("capability_selftest") or {}
    if st.get("results"):
        counts = st.get("summary", {})
        L.append("\n### Capabilities  ("
                 + " · ".join(f"{v} {k.lower()}" for k, v in sorted(counts.items()))
                 + ")")
        # Only the things that aren't fine: a FAIL is a broken privileged path
        # and a SKIP is a capability this machine doesn't have - between them
        # that is almost always the answer to "why didn't it work for me".
        notable = [r for r in st["results"]
                   if r.get("status") in ("FAIL", "SKIP")]
        for r in notable:
            L.append(f"- **{r['status']}** {r['title']} — {r['detail']}")
        if not notable:
            L.append("- every privileged path this machine has is reachable")

    tw = rep.get("active_tweaks") or {}
    if tw:
        on = [k for k in ("governor", "epp_boosted", "tearing", "adaptive_sync",
                          "power_limited", "focus_mode") if tw.get(k)]
        reniced = ", ".join((tw.get("reniced") or {}).keys()) or "none"
        L.append(f"\n### Active tweaks\n- {', '.join(on) or 'none'}  ·  reniced: {reniced}")
    return "\n".join(L) + "\n"


def as_llm_prompt(rep: dict) -> str:
    return _LLM_PROMPT + "\n\n```json\n" + json.dumps(rep, indent=2, default=str) + "\n```\n"


def build_setup_report(settings) -> str:
    """A full, shareable snapshot of the machine + every profile - for "help me"
    threads or reproducing a setup elsewhere. No incident, no log."""
    from goblinmode import capabilities, proton

    s = _system_info()
    s.update(_desktop())
    s["ram_gb"] = _ram_gb()
    s["mesa_gl"] = _mesa_version()
    caps = capabilities.detect()

    L = [f"# Goblin Mode Pro setup — {datetime.now(UTC).isoformat()[:19]}Z",
         "", "## System",
         f"- CPU: {s.get('cpu','?')}  ({caps.get('cpufreq_driver','?')})",
         f"- GPU: {s.get('gpu','?')}  ·  {', '.join(caps.get('gpu_vendors') or [])}",
         f"- Kernel: {caps.get('kernel_release','?')}  ({caps.get('kernel_flavor','?')})",
         f"- Distro: {caps.get('distro_id','?')}  ·  {s.get('desktop','?')} / {s.get('session_type','?')}",
         f"- RAM: {s.get('ram_gb','?')} GB  ·  GMP {__version__}",
         f"- Handheld: {caps.get('handheld') or 'no'}"]

    builds = proton.installed_builds()
    if builds:
        L += ["", "## Custom Proton / Wine builds"]
        L += [f"- {b['name']}  ({b['kind']})" for b in builds[:20]]

    caches = proton.shader_caches()
    if caches:
        L += ["", "## Shader caches"]
        L += [f"- {c['label']}: {c['bytes'] / (1024**2):.0f} MB" for c in caches]

    pf = preflight.run_all()
    flags = [r for r in pf if r["status"] in ("warn", "fail")]
    n = preflight.summary(pf)
    L += ["", f"## Pre-flight  ({n.get('ok',0)} ok · {n.get('warn',0)} warn · {n.get('fail',0)} fail)"]
    L += [f"- **{r['status'].upper()}** {r['title']} = `{r['value']}`" for r in flags] or ["- all clear"]

    L += ["", "## Game profiles"]
    for p in settings.profiles:
        if p.exe == "__forced__":
            continue
        d = p.__dict__
        on = [k for k in ("renice_enabled", "governor_boost", "tearing_enabled",
                          "adaptive_sync_enabled", "focus_mode", "fps_watchdog",
                          "gamescope_enabled", "power_limit_enabled") if d.get(k)]
        rv = [k for k, v in (d.get("runner_vars") or {}).items() if v]
        gt = [k for k, v in (d.get("gpu_tuning") or {}).items() if v]
        L.append(f"\n### {p.display_name}  (`{p.exe}`, match: {p.match_mode})")
        L.append(f"- on: {', '.join(on) or 'nothing'}")
        if p.core_pin != "off":
            L.append(f"- core pin: {p.core_pin}")
        if rv:
            L.append(f"- runner vars: {', '.join(rv)}")
        if gt:
            L.append(f"- gpu tuning: {', '.join(gt)}")
        if p.steam_app_id:
            L.append(f"- steam appid: {p.steam_app_id}")
        if p.notes:
            L.append(f"- notes: {_redact(p.notes)}")
    return "\n".join(L) + "\n"


def build_works_for_me(profile: dict, note: str = "") -> dict[str, Any]:
    """A small, anonymized 'this setup works' report - no incident, no log
    excerpt, just the system and which profile settings were active. Field
    allowlist reused from community.SHAREABLE - the exact set already judged
    safe to leave a machine (no undervolt/fan-control settings, etc)."""
    from goblinmode.community import SHAREABLE

    sysinfo = _system_info()
    sysinfo.update(_desktop())
    sysinfo["gmp_version"] = __version__
    return {
        "schema": "gmp.worksforme.v1",
        "generated": datetime.now(UTC).isoformat(),
        "system": sysinfo,
        "game": profile.get("display_name") or profile.get("exe", ""),
        "steam_app_id": profile.get("steam_app_id", ""),
        "note": note[:500],
        "profile": {k: v for k, v in profile.items() if k in SHAREABLE},
    }


def works_for_me_markdown(rep: dict) -> str:
    s = rep.get("system", {})
    lines = [f"## Works for me — {rep.get('game', '?')}", ""]
    if rep.get("note"):
        lines += [f"> {rep['note']}", ""]
    lines += [
        f"- **CPU** {s.get('cpu', '?')}",
        f"- **GPU** {s.get('gpu', '?')}",
        f"- **Kernel** {s.get('kernel', '?')}  ·  {s.get('distro', '?')}  ·  "
        f"{s.get('desktop', '?')} / {s.get('session_type', '?')}",
        f"- **GMP** {s.get('gmp_version', '?')}",
    ]
    if rep.get("steam_app_id"):
        lines.append(f"- **Steam AppID** {rep['steam_app_id']}")
    lines += ["", "### Profile settings", "```json",
             json.dumps(rep.get("profile", {}), indent=2), "```"]
    return "\n".join(lines) + "\n"


def works_for_me_issue_url(rep: dict, repo: str = "Bvaughan7/goblin-mode-pro") -> str:
    """A pre-filled 'works for me' issue link - the whole "upload" mechanism:
    no server, no account, no telemetry. GitHub issues tagged works-for-me
    are the de-facto community database, browsable without any of this
    project's own infrastructure."""
    body = works_for_me_markdown(rep)
    query = urllib.parse.urlencode({
        "title": f"[works for me] {rep.get('game') or 'game'}",
        "body": body,
        "labels": "works-for-me",
    })
    return f"https://github.com/{repo}/issues/new?{query}"


def github_issue_url(rep: dict, repo: str = "Bvaughan7/goblin-mode-pro") -> str:
    body = as_markdown(rep)
    if len(body) > 6000:
        body = body[:6000] + "\n\n*(truncated - full report is on the clipboard)*\n"
    query = urllib.parse.urlencode({
        "title": f"[{rep.get('game') or 'game'}] ",
        "body": body,
        "labels": "triage",
    })
    return f"https://github.com/{repo}/issues/new?{query}"
