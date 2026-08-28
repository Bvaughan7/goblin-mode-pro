#!/usr/bin/env python3
"""Minimal pytest-ish harness so the suite runs without pytest installed.

CachyOS ships no pip and `python-pytest` isn't a dependency, so this provides
just enough of pytest's `tmp_path` + `monkeypatch` fixtures to run tests/.

    python3 scripts/run_tests.py

Prefer real pytest when available:  sudo pacman -S python-pytest && pytest
"""
import importlib
import inspect
import sys
import tempfile
import traceback
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT))

TEST_MODULES = [
    "tests.test_config",
    "tests.test_mangohud",
    "tests.test_payload",
    "tests.test_compositor", "tests.test_diagnostics", "tests.test_fpswatch", "tests.test_gpu", "tests.test_gamedetect", "tests.test_preflight", "tests.test_logrules", "tests.test_report",
    "tests.test_observer_state",
    "tests.test_incidents",
    "tests.test_runner",
]


class MonkeyPatch:
    _SENTINEL = object()

    def __init__(self):
        self._undo = []

    def setattr(self, target, name=_SENTINEL, value=_SENTINEL):
        if isinstance(target, str) and value is self._SENTINEL:
            value, name = name, self._SENTINEL
        if isinstance(target, str):
            parts = target.split(".")
            name = parts[-1]
            obj, idx = None, 0
            for i in range(len(parts) - 1, 0, -1):
                try:
                    obj = importlib.import_module(".".join(parts[:i]))
                    idx = i
                    break
                except ModuleNotFoundError:
                    continue
            for p in parts[idx:-1]:
                obj = getattr(obj, p)
            target = obj
        had = hasattr(target, name)
        self._undo.append((target, name, getattr(target, name, None), had))
        setattr(target, name, value)

    def undo(self):
        for target, name, old, had in reversed(self._undo):
            setattr(target, name, old) if had else delattr(target, name)
        self._undo.clear()


def run_module(mod_name):
    mod = importlib.import_module(mod_name)
    fns = [
        (n, f)
        for n, f in inspect.getmembers(mod, inspect.isfunction)
        if n.startswith("test_") and f.__module__ == mod.__name__
    ]
    passed = failed = 0
    for name, fn in sorted(fns):
        mp = MonkeyPatch()
        params = inspect.signature(fn).parameters
        kwargs, tmp = {}, None
        if "tmp_path" in params:
            tmp = tempfile.TemporaryDirectory()
            kwargs["tmp_path"] = Path(tmp.name)
        if "monkeypatch" in params:
            kwargs["monkeypatch"] = mp
        try:
            fn(**kwargs)
            print(f"  PASS {mod_name}::{name}")
            passed += 1
        except Exception:
            print(f"  FAIL {mod_name}::{name}")
            traceback.print_exc()
            failed += 1
        finally:
            mp.undo()
            if tmp:
                tmp.cleanup()
    return passed, failed


def main():
    total_p = total_f = 0
    for m in TEST_MODULES:
        print(f"\n== {m} ==")
        p, f = run_module(m)
        total_p += p
        total_f += f
    print(f"\n{total_p} passed, {total_f} failed")
    return 1 if total_f else 0


if __name__ == "__main__":
    raise SystemExit(main())
