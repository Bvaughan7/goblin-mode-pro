from goblinmode.config import GameProfile
from goblinmode.payload import PerformancePayload


class FakeHelper:
    def __init__(self):
        self.governor = "powersave"
        self.epp = "balance_performance"
        self.pl = (45_000_000, 107_000_000)
        self.reverts = 0
        self.reniced = []

    def available(self):
        return True

    def set_governor(self, g):
        self.governor = g
        return True

    def set_epp(self, e):
        self.epp = e
        return True

    def get_governor(self):
        return self.governor

    def get_power_limits(self):
        return self.pl

    def set_power_limits(self, pl1, pl2):
        self.pl = (pl1, pl2)
        return True

    def renice(self, pid, nice):
        self.reniced.append((pid, nice))
        return True

    def revert_all(self):
        self.reverts += 1
        self.governor = "powersave"
        self.pl = (45_000_000, 107_000_000)
        return True


def _payload(monkeypatch):
    monkeypatch.setattr("goblinmode.payload.mangohud.apply", lambda p: "/tmp/x.conf")
    monkeypatch.setattr("goblinmode.payload.mangohud.revert", lambda p: None)
    monkeypatch.setattr("goblinmode.payload.PerformancePayload._write_applied_state", lambda self: None)
    pp = PerformancePayload(helper=FakeHelper())
    # neutralise the real compositor
    pp.compositor.enable_tearing = lambda: True
    pp.compositor.restore_tearing = lambda: True
    pp.compositor.enable_adaptive_sync = lambda policy="automatic": True
    pp.compositor.restore_adaptive_sync = lambda: True
    return pp


def test_governor_refcounted_across_two_games(monkeypatch):
    pp = _payload(monkeypatch)
    a = GameProfile(exe="Wow.exe")
    b = GameProfile(exe="rs2client")

    pp.apply(a, 100)
    assert pp.helper.governor == "performance"
    pp.apply(b, 200)
    # second game exits - governor stays until the last one
    pp.revert(b)
    assert pp.helper.reverts == 0
    pp.revert(a)
    assert pp.helper.reverts == 1
    assert pp.helper.governor == "powersave"


def test_renice_uses_profile_nice_value(monkeypatch):
    pp = _payload(monkeypatch)
    p = GameProfile(exe="Wow.exe", nice_value=-8)
    pp.apply(p, 4242)
    assert pp.helper.reniced == [(4242, -8)]


def test_power_limit_applied_and_reverted(monkeypatch):
    pp = _payload(monkeypatch)
    p = GameProfile(exe="Wow.exe", power_limit_enabled=True, pl1_w=60, pl2_w=120, governor_boost=False)
    pp.apply(p, 1)
    assert pp.helper.pl == (60_000_000, 120_000_000)
    assert pp.status().power_limited is True
    pp.revert(p)
    assert pp.helper.reverts == 1


def test_highest_requested_power_limit_wins(monkeypatch):
    pp = _payload(monkeypatch)
    a = GameProfile(exe="a", power_limit_enabled=True, pl1_w=50, pl2_w=100)
    b = GameProfile(exe="b", power_limit_enabled=True, pl1_w=65, pl2_w=110)
    pp.apply(a, 1)
    pp.apply(b, 2)
    assert pp.helper.pl == (65_000_000, 110_000_000)


def test_forced_profile_skips_mangohud(monkeypatch):
    calls = []
    monkeypatch.setattr("goblinmode.payload.mangohud.apply", lambda p: calls.append(p) or "/x")
    monkeypatch.setattr("goblinmode.payload.PerformancePayload._write_applied_state", lambda self: None)
    pp = PerformancePayload(helper=FakeHelper())
    pp.compositor.enable_tearing = lambda: True
    pp.compositor.restore_tearing = lambda: True
    from goblinmode.payload import FORCED_EXE

    pp.apply(GameProfile(exe=FORCED_EXE, renice_enabled=False), None)
    assert calls == []
