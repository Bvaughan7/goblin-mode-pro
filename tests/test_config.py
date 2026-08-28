import json

from goblinmode import config


def test_default_settings_has_wow_and_runescape():
    s = config.default_settings()
    exes = {p.exe for p in s.profiles}
    assert "Wow.exe" in exes
    assert any("rs2client" in e for e in exes)


def test_nice_value_clamped():
    p = config.GameProfile(exe="x", nice_value=-50)
    assert p.nice_value == -10
    p2 = config.GameProfile(exe="x", nice_value=99)
    assert p2.nice_value == 19


def test_env_assignments_resolve_toggles():
    p = config.GameProfile(
        exe="Wow.exe",
        runner_vars={"nvapi": True, "fsync": True, "no_esync": False, "dxvk_async": False},
    )
    env = p.env_assignments()
    assert env["PROTON_ENABLE_NVAPI"] == "1"
    assert env["DXVK_ENABLE_NVAPI"] == "1"
    assert env["WINEFSYNC"] == "1"
    assert "PROTON_NO_ESYNC" not in env
    assert "DXVK_ASYNC" not in env


def test_roundtrip_save_load(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    monkeypatch.setattr(config, "CONFIG_FILE", cfg)
    monkeypatch.setattr(config, "ensure_user_dirs", lambda: None)

    s = config.default_settings()
    s.poll_interval = 9
    s.profiles[0].nice_value = -8
    config.save(s)

    raw = json.loads(cfg.read_text())
    assert raw["schema_version"] == config.SCHEMA_VERSION

    loaded = config.load()
    assert loaded.poll_interval == 9
    assert loaded.profile_for_exe("Wow.exe").nice_value == -8


def test_master_disabled_yields_no_enabled_profiles():
    s = config.default_settings()
    s.master_enabled = False
    assert s.enabled_profiles() == []
