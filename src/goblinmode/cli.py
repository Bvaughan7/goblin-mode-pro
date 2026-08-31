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
    goblin-mode-pro-cli gamescope-session [--game NAME] [-- COMMAND...]
    goblin-mode-pro-cli compare GAME
    goblin-mode-pro-cli works-for-me GAME [--note TEXT]
    goblin-mode-pro-cli selftest [--apply] [--json]
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # keeps `import gi` (via daemon_bridge) out of the import path
    from goblinmode.ipc.daemon_bridge import BridgeClient


def _connect() -> BridgeClient:
    from goblinmode.ipc.daemon_bridge import BridgeClient

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


def cmd_compare(b: BridgeClient, args) -> int:
    from goblinmode.benchmarkcard import diff_sessions

    history = b.get_session_history(args.game)
    if len(history) < 2:
        _p(f"need at least two recorded sessions for {args.game!r} to compare "
           f"(found {len(history)})")
        return 1
    b_sess, a_sess = history[-1], history[-2]
    _p(f"{a_sess.get('started', '')[:16]}  ->  {b_sess.get('started', '')[:16]}")
    for row in diff_sessions(a_sess, b_sess):
        line = f"  {row['label']:<22} {row['a']!s:>10}  ->  {row['b']!s:>10}"
        if row["delta_pct"] is not None:
            arrow = "^" if row["better"] == "b" else "v" if row["better"] == "a" else "-"
            line += f"   {arrow} {row['delta_pct']:+.1f}%"
        _p(line)
    return 0


def cmd_gamescope_session(b: BridgeClient, args) -> int:
    """Launch a standalone gamescope session (Steam Big Picture by default,
    or a specific game's profile) - gamescope becomes the top-level
    compositor for it, replacing this process (os.execvp), rather than
    nesting inside a single already-launching game the way the per-game
    launch wrapper's embedded gamescope does."""
    import os
    import shutil

    from goblinmode import runner
    from goblinmode.config import GameProfile

    if not shutil.which("gamescope"):
        _p("gamescope is not installed")
        return 1

    profile = None
    if args.game:
        needle = args.game.lower()
        for p in b.get_status().get("profiles") or []:
            if needle in {(p.get("exe") or "").lower(), (p.get("display_name") or "").lower()}:
                fields = {k: v for k, v in p.items() if k in GameProfile.__dataclass_fields__}
                profile = GameProfile(**fields)
                break
        if profile is None:
            _p(f"no profile matches {args.game!r} — run 'goblin-mode-pro-cli games' to list them")
            return 1

    argv = runner.gamescope_session_argv(profile, args.command or None)
    _p("launching:", " ".join(argv))
    os.execvp(argv[0], argv)  # noqa: S606 - fixed binary name, not user input


def cmd_works_for_me(b: BridgeClient, args) -> int:
    result = b.build_works_for_me(args.game, args.note or "")
    _p(result["markdown"])
    _p(f"Open this to post it: {result['url']}")
    return 0


def cmd_selftest(_b, args) -> int:
    """Probe every privileged path on this machine and report what worked.

    Needs no daemon - it talks to the helper directly, so it still works when
    the daemon is the thing that's broken.
    """
    from goblinmode import selftest

    results, code = selftest.run(apply=args.apply)
    if args.json:
        _p(json.dumps(selftest.to_json(results, args.apply), indent=2))
    else:
        _p(selftest.render(results, args.apply, color=sys.stdout.isatty()))
    return code


#: commands that talk to the helper or the local system directly and must work
#: with no daemon running - `selftest` exists precisely for when things are
#: broken, so requiring the daemon would defeat it.
_NO_DAEMON = {"selftest"}

_COMMANDS = {
    "status": cmd_status, "boost": cmd_boost, "unboost": cmd_unboost,
    "health": cmd_health, "sessions": cmd_sessions, "benchmark": cmd_benchmark,
    "preflight": cmd_preflight, "report": cmd_report, "games": cmd_games,
    "setup": cmd_setup, "gamescope-session": cmd_gamescope_session,
    "compare": cmd_compare, "works-for-me": cmd_works_for_me,
    "selftest": cmd_selftest,
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
        if name == "gamescope-session":
            sp.add_argument("--game", default="", help="a game's exe or display name; "
                             "default launches Steam Big Picture")
            sp.add_argument("command", nargs="*", default=[],
                             help="command to run instead of Steam, after --")
        if name == "compare":
            sp.add_argument("game", help="exe name, as shown by 'games'")
        if name == "works-for-me":
            sp.add_argument("game", help="exe name, as shown by 'games'")
            sp.add_argument("--note", default="", help="a short note, e.g. what you changed")
        if name == "selftest":
            sp.add_argument("--apply", action="store_true",
                             help="round-trip each capability (apply, read back, "
                                  "revert) instead of only probing - the only "
                                  "mode that proves a write path")
            sp.add_argument("--json", action="store_true",
                             help="machine-readable output, for pasting into an issue")
    args = ap.parse_args(argv)
    bridge = None if args.cmd in _NO_DAEMON else _connect()
    return _COMMANDS[args.cmd](bridge, args)


if __name__ == "__main__":
    raise SystemExit(main())
