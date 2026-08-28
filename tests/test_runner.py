from goblinmode import runner
from goblinmode.config import Settings, GameProfile


def _settings():
    return Settings(
        profiles=[
            GameProfile(
                exe="Wow.exe",
                match_mode="exact",
                runner_vars={"nvapi": True, "fsync": True, "no_esync": False, "dxvk_async": True},
            )
        ]
    )


def test_resolve_env_for_proton_style_argv():
    argv = [
        "/steam/SteamLinuxRuntime/_v2-entry-point", "--verb=waitforexitandrun",
        "--", "/proton/proton", "run", "/drive_c/Program Files/WoW/Wow.exe",
    ]
    env = runner.resolve_env_for_argv(argv, _settings())
    assert env["PROTON_ENABLE_NVAPI"] == "1"
    assert env["WINEFSYNC"] == "1"
    assert "DXVK_ASYNC" in env
    assert "PROTON_NO_ESYNC" not in env


def test_print_env_for_emits_export_lines():
    out = runner.print_env_for(["/x/Wow.exe"], _settings())
    lines = set(out.splitlines())
    assert "export PROTON_ENABLE_NVAPI=1" in lines
    assert "export WINEFSYNC=1" in lines


def test_no_match_returns_empty():
    assert runner.resolve_env_for_argv(["/usr/bin/glxgears"], _settings()) == {}


def test_write_wrapper(tmp_path, monkeypatch):
    wrapper = tmp_path / "bin" / "goblin-run"
    monkeypatch.setattr(runner, "RUNNER_WRAPPER", wrapper)
    monkeypatch.setattr(runner, "ensure_user_dirs", lambda: wrapper.parent.mkdir(parents=True, exist_ok=True))
    path = runner.write_wrapper()
    assert path.exists()
    assert path.stat().st_mode & 0o111  # executable
    assert "goblin-mode-pro-daemon --print-env-for" in path.read_text()
