from goblinmode import preflight


def test_run_all_returns_wellformed(monkeypatch):
    res = preflight.run_all()
    ids = {r["id"] for r in res}
    assert "max_map_count" in ids and "split_lock" in ids
    for r in res:
        assert r["status"] in ("ok", "warn", "fail", "info", "unknown")
        assert isinstance(r["title"], str) and isinstance(r["why"], str)


def test_max_map_count_check(monkeypatch):
    monkeypatch.setattr(preflight, "_read_int", lambda p: 65530 if "max_map_count" in p else 0)
    r = preflight._c_max_map_count()
    assert r.status == preflight.FAIL

    monkeypatch.setattr(preflight, "_read_int", lambda p: 2147483642)
    assert preflight._c_max_map_count().status == preflight.OK


def test_split_lock_check(monkeypatch):
    monkeypatch.setattr(preflight, "_read", lambda p: "1")
    assert preflight._c_split_lock().status == preflight.WARN
    monkeypatch.setattr(preflight, "_read", lambda p: "0")
    assert preflight._c_split_lock().status == preflight.OK
    monkeypatch.setattr(preflight, "_read", lambda p: None)
    assert preflight._c_split_lock().status == preflight.INFO


def test_sysctl_dropin_only_includes_failing_fixable(monkeypatch):
    fake = [
        {"id": "a", "status": "fail", "sysctl": ["vm.max_map_count", "2147483642"]},
        {"id": "b", "status": "ok", "sysctl": ["vm.swappiness", "10"]},
        {"id": "c", "status": "warn", "sysctl": None, "kernel_param": "x=y"},
    ]
    text = preflight.sysctl_dropin_text(fake)
    assert "vm.max_map_count = 2147483642" in text
    assert "vm.swappiness" not in text
    assert preflight.pending_sysctls(fake) == [("vm.max_map_count", "2147483642")]


def test_severity_downgrade(monkeypatch):
    # a check whose _run FAILs but severity is INFO reports INFO, not fail
    monkeypatch.setattr(preflight, "_read_int", lambda p: 200)  # swappiness high
    res = {r["id"]: r for r in preflight.run_all()}
    assert res["swappiness"]["status"] in ("info", "ok")
