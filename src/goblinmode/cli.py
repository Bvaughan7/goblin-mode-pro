"""``goblin-mode-pro-cli`` — a headless client for the daemon.

Talks to the running ``systemd --user`` daemon over the session bus, so it works
in a plain terminal or over SSH with no display.

    goblin-mode-pro-cli status
    goblin-mode-pro-cli boost / unboost
    goblin-mode-pro-cli health
    goblin-mode-pro-cli sessions [--game NAME]
    goblin-mode-pro-cli benchmark "Wow.exe"
    goblin-mode-pro-cli preflight [--fix]
    goblin-mode-pro-cli report [--issue]
    goblin-mode-pro-cli games
"""

from __future__ import annotations

import argparse
import json
import sys

from goblinmode.ipc.daemon_bridge import BridgeClient


def _connect() -> BridgeClient:
    b = BridgeClient()
    if not b.connect():
        sys.exit("goblin-mode-pro daemon is not running "
                 "(systemctl --user start goblin-mode-pro)")
    return b


def _p(*a) -> None:
    print(*a)


def cmd_status(b: BridgeClient, args) -> int:
    s = b.get_status()
    if args.json:
        _p(json.dumps(s, indent=2))
        return 0
    games = s.get("active_games") or []
    _p(f"master      : {'on' if s.get('master_enabled') else 'off'}")
    _p(f"active game  : {', '.join(games) or '—'}")
    _p(f"governor     : {s.get('governor', '?')}")
    t = s.get("tweaks") or {}
    on = [k for k in ("governor", "epp_boosted", "tearing", "adaptive_sync",
                      "power_limited", "focus_mode") if t.get(k)]
    _p(f"active tweaks : {', '.join(on) or 'none'}")
    _p(f"helper       : {'connected' if s.get('helper_available') else 'limited mode'}")
    caps = s.get("capabilities") or {}
    _p(f"machine      : {caps.get('cpu_model', '?')} · {', '.join(caps.get('gpu_vendors') or [])}"
       f" · kernel {caps.get('kernel_release', '?')}")
    return 0


def cmd_boost(b: BridgeClient, _args) -> int:
    b.force_boost(True)
    _p("forced performance mode ON")
    return 0


def cmd_unboost(b: BridgeClient, _args) -> int:
    b.force_boost(False)
    _p("forced performance mode OFF")
    return 0


def cmd_health(b: BridgeClient, _args) -> int:
    h = b.get_health()
    score = h.get("score")
    _p(f"system readiness: {score if score is not None else '?'} / 10")
    n = h.get("counts") or {}
    _p(f"  {n.get('ok', 0)} ok · {n.get('warn', 0)} warn · {n.get('fail', 0)} fail")
    for w in h.get("worst") or []:
        _p(f"  ✗ {w}")
    return 0


def cmd_sessions(b: BridgeClient, args) -> int:
    rows = (b.get_session_history(args.game) if args.game else b.get_sessions())[-args.limit:]
    if not rows:
        _p("no sessions recorded yet")
        return 0
    for s in rows:
        tag = " [benchmark]" if s.get("benchmark") else ""
        avg = s.get("fps_avg")
        low = s.get("fps_1low")
        _p(f"{s.get('started','')[:16]}  {s.get('game','?'):24}{tag}"
           + (f"  avg {avg:.0f}  1% {low:.0f}" if avg else "  (no fps log)"))
    return 0


def cmd_benchmark(b: BridgeClient, args) -> int:
    if b.arm_benchmark(args.game):
        _p(f"benchmark armed for {args.game} — launch it and play a few minutes.")
        _p("the report card lands in `goblin-mode-pro-cli sessions` on exit.")
        return 0
    _p("could not arm the benchmark")
    return 1


def cmd_preflight(b: BridgeClient, args) -> int:
    checks = b.run_preflight()
    for c in checks:
        mark = {"ok": "✓", "warn": "!", "fail": "✗", "info": "i"}.get(c["status"], "?")
        _p(f"{mark} {c['title']:34} {c['value']}")
    if args.fix:
        res = b.apply_preflight_fixes()
        _p("\napplied: " + (", ".join(res.get("applied") or []) or "nothing"))
        if res.get("failed"):
            _p("failed : " + ", ".join(res["failed"]))
    return 0


def cmd_report(b: BridgeClient, args) -> int:
    md = b.build_report("")
    _p(md)
    return 0


def cmd_games(b: BridgeClient, _args) -> int:
    for p in b.get_status().get("profiles") or []:
        if p.get("exe") == "__forced__":
            continue
        _p(f"{'●' if p.get('enabled') else '○'} {p.get('display_name','?'):28} "
           f"({p.get('exe')}, {p.get('match_mode')})")
    return 0


def cmd_setup(b: BridgeClient, _args) -> int:
    _p(b.export_setup())
    return 0


_COMMANDS = {
    "status": cmd_status, "boost": cmd_boost, "unboost": cmd_unboost,
    "health": cmd_health, "sessions": cmd_sessions, "benchmark": cmd_benchmark,
    "preflight": cmd_preflight, "report": cmd_report, "games": cmd_games,
    "setup": cmd_setup,
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="goblin-mode-pro-cli", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in _COMMANDS:
        sp = sub.add_parser(name)
        if name == "status":
            sp.add_argument("--json", action="store_true")
        if name == "sessions":
            sp.add_argument("--game", default="")
            sp.add_argument("--limit", type=int, default=15)
        if name == "benchmark":
            sp.add_argument("game")
        if name == "preflight":
            sp.add_argument("--fix", action="store_true")
        if name == "report":
            sp.add_argument("--issue", action="store_true")
    args = ap.parse_args(argv)
    return _COMMANDS[args.cmd](_connect(), args)


if __name__ == "__main__":
    raise SystemExit(main())
