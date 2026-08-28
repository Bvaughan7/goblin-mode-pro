from goblinmode.config import Settings, GameProfile
from goblinmode.observer import Observer, _matches


class FakeProc:
    def __init__(self, pid, name, exe="", cmdline=None, rss=100_000_000):
        self.info = {"pid": pid, "name": name, "exe": exe, "cmdline": cmdline or [name]}
        self._rss = rss

    def memory_info(self):
        class M:
            rss = self._rss
        return M()


def test_matches_exact_and_substring():
    p_exact = GameProfile(exe="Wow.exe", match_mode="exact")
    assert _matches(p_exact, "Wow.exe", "/games/Wow.exe", ["Wow.exe", "-run"])
    assert not _matches(p_exact, "WowClassic.exe", "", [])

    p_sub = GameProfile(exe="runescape", match_mode="substring")
    assert _matches(p_sub, "rs2client", "", ["/opt/RuneScape/rs2client"])


def test_matches_exact_is_case_insensitive_and_windows_paths():
    """Real WoW: comm is 'WoW.exe', /proc/exe is the wine loader, cmdline has a
    Windows path."""
    p = GameProfile(exe="Wow.exe", match_mode="exact")
    assert _matches(
        p,
        "WoW.exe",
        "/opt/proton/files/lib/wine/x86_64-unix/wine64-preloader",
        [r"C:\Program Files (x86)\World of Warcraft\_retail_\WoW.exe", "-launcherlogin"],
    )
    # the voice-proxy sibling must NOT match
    assert not _matches(p, "WowVoiceProxy.e", "", ["./Utils/WowVoiceProxy.exe"])


def test_auto_detect_emits_auto_event(monkeypatch):
    from goblinmode.gamedetect import GameCandidate

    settings = Settings(profiles=[], auto_detect=True)
    events = []
    obs = Observer(settings, events.append)

    monkeypatch.setattr("goblinmode.observer.psutil.process_iter", lambda attrs: [])
    cand = GameCandidate(pid=999, exe="EldenRing.exe", display_name="Elden Ring",
                         score=8, source="steam")
    monkeypatch.setattr("goblinmode.observer.gamedetect.detect_games", lambda: [cand])
    obs.poll()
    assert len(events) == 1
    assert events[0].auto and events[0].profile is None
    assert events[0].candidate.display_name == "Elden Ring"

    # ignored -> the running game drops out, no new "running" event
    events.clear()
    settings.ignored_games = ["EldenRing.exe"]
    obs.update_settings(settings)
    obs.poll()
    assert not any(e.running for e in events)
    # and a fresh observer never surfaces it at all
    obs2 = Observer(settings, lambda e: events.append(e))
    events.clear()
    obs2.poll()
    assert events == []


def test_auto_detect_off_skips_the_sweep(monkeypatch):
    settings = Settings(profiles=[], auto_detect=False)
    events = []
    obs = Observer(settings, events.append)
    monkeypatch.setattr("goblinmode.observer.psutil.process_iter", lambda attrs: [])
    called = []
    monkeypatch.setattr("goblinmode.observer.gamedetect.detect_games",
                        lambda: called.append(1) or [])
    obs.poll()
    assert called == []


def test_find_pid_not_disqualified_by_wine_loader_exe(monkeypatch):
    settings = Settings(profiles=[GameProfile(exe="Wow.exe", match_mode="exact")])
    obs = Observer(settings, lambda e: None)
    procs = [
        FakeProc(
            29044,
            "WoW.exe",
            "/opt/proton/files/lib/wine/x86_64-unix/wine64-preloader",
            cmdline=[r"C:\Program Files (x86)\World of Warcraft\_retail_\WoW.exe"],
        )
    ]
    assert obs._find_pid(settings.profiles[0], procs) == 29044


def test_launch_and_exit_events(monkeypatch):
    settings = Settings(profiles=[GameProfile(exe="Wow.exe", match_mode="exact")])
    events = []
    obs = Observer(settings, events.append)

    procs = [FakeProc(1234, "WoW.exe", "/games/Wow.exe")]
    monkeypatch.setattr("goblinmode.observer.psutil.process_iter", lambda attrs: procs)
    obs.poll()
    assert len(events) == 1 and events[0].running and events[0].pid == 1234

    # still running -> no duplicate event
    obs.poll()
    assert len(events) == 1

    # gone -> exit event
    procs.clear()
    obs.poll()
    assert len(events) == 2 and events[1].running is False


def test_picks_fattest_process(monkeypatch):
    settings = Settings(profiles=[GameProfile(exe="Wow.exe", match_mode="exact")])
    obs = Observer(settings, lambda e: None)
    procs = [
        FakeProc(10, "Wow.exe", "/games/Wow.exe", rss=5_000_000),
        FakeProc(20, "Wow.exe", "/games/Wow.exe", rss=900_000_000),
    ]
    pid = obs._find_pid(settings.profiles[0], procs)
    assert pid == 20
