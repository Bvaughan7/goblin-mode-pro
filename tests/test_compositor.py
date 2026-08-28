from goblinmode import compositor


_KSCREEN_OUT = """\
Output: 1 eDP-1 abc
\tenabled
\tVrr: incapable
Output: 2 DP-1 def
\tenabled
\tVrr: Never
Output: 3 HDMI-A-1 ghi
\tVrr: Automatic
"""


def test_vrr_outputs_skips_incapable(monkeypatch):
    class CP:
        returncode = 0
        stdout = _KSCREEN_OUT

    monkeypatch.setattr(compositor, "_run", lambda cmd, timeout=6: CP())
    outs = compositor._vrr_outputs()
    assert "eDP-1" not in outs
    assert outs == {"DP-1": "never", "HDMI-A-1": "automatic"}


def test_enable_adaptive_sync_saves_and_restores(monkeypatch):
    calls = []

    monkeypatch.setattr(compositor.Compositor, "adaptive_sync_supported", property(lambda self: True))
    monkeypatch.setattr(compositor, "_vrr_outputs", lambda: {"DP-1": "never"})
    monkeypatch.setattr(compositor, "_set_vrr", lambda name, policy: calls.append((name, policy)) or True)

    c = compositor.Compositor()
    assert c.enable_adaptive_sync() is True
    assert ("DP-1", "automatic") in calls
    assert c.restore_adaptive_sync() is True
    assert ("DP-1", "never") in calls


def test_tearing_unsupported_off_kde(monkeypatch):
    monkeypatch.setattr(compositor, "_session_type", lambda: "wayland")
    monkeypatch.setattr(compositor, "_desktop", lambda: "GNOME")
    c = compositor.Compositor()
    assert c.tearing_supported is False
    assert c.enable_tearing() is False
