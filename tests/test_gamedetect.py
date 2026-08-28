from goblinmode import gamedetect
from goblinmode.gamedetect import _blocked, _steam_appid_from_cmd, _lutris_name_from_cmd, _win_basename


class FakeProc:
    def __init__(self, pid, name, exe="", cmdline=None, ppid=1, rss=50 * 1024 * 1024):
        self.info = {"pid": pid, "name": name, "exe": exe,
                     "cmdline": cmdline or [name], "ppid": ppid}
        self._rss = rss

    def memory_info(self):
        class M:
            rss = self._rss
        return M()


def test_steam_appid_parsing():
    assert _steam_appid_from_cmd("reaper SteamLaunch AppId=1091500 -- proton run Cyberpunk2077.exe") == "1091500"
    assert _steam_appid_from_cmd("/usr/bin/vlc") is None


def test_lutris_name_parsing():
    cmd = "python3 /usr/share/lutris/bin/lutris-wrapper Battle.net 0 2 Agent.exe /usr/bin/umu-run ..."
    assert _lutris_name_from_cmd(cmd) == "Battle.net"


def test_blocklist_stems():
    assert _blocked("kwin_wayland_wrapper", "kwin_wayland")
    assert _blocked("xdg-desktop-portal-kde", "xdg-desktop-portal-kde")
    assert _blocked("baloorunner", "baloorunner")
    assert _blocked("firefox", "firefox")
    assert not _blocked("WoW.exe", "WoW.exe")
    assert not _blocked("rs2client", "rs2client")


def test_win_basename_handles_windows_paths():
    assert _win_basename(r"C:\Program Files\Game\game.exe") == "game.exe"
    assert _win_basename("/opt/game/bin/game") == "game"


def test_detect_steam_game(monkeypatch):
    procs = [
        FakeProc(100, "reaper", cmdline=["reaper", "SteamLaunch", "AppId=1091500", "--",
                                         "proton", "run", "Cyberpunk2077.exe"], ppid=1),
        FakeProc(101, "Cyberpunk2077.e", exe="/games/Cyberpunk2077.exe",
                 cmdline=["Z:\\games\\Cyberpunk2077.exe"], ppid=100, rss=3_000_000_000),
    ]
    monkeypatch.setattr(gamedetect.psutil, "process_iter", lambda attrs: procs)
    monkeypatch.setattr(gamedetect, "_steam_app_name", lambda a: "Cyberpunk 2077")
    monkeypatch.setattr(gamedetect, "_gpu_load", lambda pid: 2)
    monkeypatch.setattr(gamedetect, "_links_game_libs", lambda pid: False)
    monkeypatch.setattr(gamedetect.psutil, "Process", lambda pid: next(p for p in procs if p.info["pid"] == pid))

    games = gamedetect.detect_games()
    assert len(games) == 1
    g = games[0]
    assert g.source == "steam"
    assert g.app_id == "1091500"
    assert g.display_name == "Cyberpunk 2077"
    # picked the fat inner process, not the reaper wrapper
    assert g.pid == 101


def test_generic_process_needs_two_signals(monkeypatch):
    procs = [FakeProc(200, "some-native-game", exe="/opt/game/game", ppid=1, rss=100 * 1024 * 1024)]
    monkeypatch.setattr(gamedetect.psutil, "process_iter", lambda attrs: procs)
    monkeypatch.setattr(gamedetect.psutil, "Process", lambda pid: procs[0])

    # only GPU active (score 3) -> not enough
    monkeypatch.setattr(gamedetect, "_gpu_load", lambda pid: 2)
    monkeypatch.setattr(gamedetect, "_links_game_libs", lambda pid: False)
    assert gamedetect.detect_games() == []

    # GPU active + SDL/wine linked + fat rss -> score 6 -> detected
    procs[0]._rss = 900 * 1024 * 1024
    monkeypatch.setattr(gamedetect, "_links_game_libs", lambda pid: True)
    games = gamedetect.detect_games()
    assert len(games) == 1 and games[0].source == "generic"


def test_kde_process_never_detected(monkeypatch):
    procs = [FakeProc(1, "plasmashell", exe="/usr/bin/plasmashell", rss=800 * 1024 * 1024)]
    monkeypatch.setattr(gamedetect.psutil, "process_iter", lambda attrs: procs)
    monkeypatch.setattr(gamedetect, "_gpu_load", lambda pid: 2)
    monkeypatch.setattr(gamedetect, "_links_game_libs", lambda pid: True)
    assert gamedetect.detect_games() == []
