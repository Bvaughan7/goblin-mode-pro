from goblinmode import mangohud
from goblinmode.config import GameProfile


def test_apply_preserves_user_lines(tmp_path, monkeypatch):
    conf = tmp_path / "MangoHud.conf"
    conf.write_text("# my config\nfont_size=24\nposition=top-left\n")
    monkeypatch.setattr(mangohud, "MANGOHUD_CONF", conf)
    monkeypatch.setattr(mangohud, "MANGOHUD_DIR", tmp_path)

    p = GameProfile(exe="Wow.exe", mangohud={"enabled": True, "fps": True, "cpu_temp": True})
    mangohud.apply(p)

    text = conf.read_text()
    assert "font_size=24" in text
    assert "position=top-left" in text
    assert "no_display=0" in text
    assert "fps" in text
    assert mangohud._GMP_BEGIN in text


def test_revert_removes_only_gmp_block(tmp_path, monkeypatch):
    conf = tmp_path / "MangoHud.conf"
    monkeypatch.setattr(mangohud, "MANGOHUD_CONF", conf)
    monkeypatch.setattr(mangohud, "MANGOHUD_DIR", tmp_path)
    conf.write_text("font_size=24\n")

    p = GameProfile(exe="Wow.exe", mangohud={"enabled": True, "fps": True})
    mangohud.apply(p)
    assert mangohud._GMP_BEGIN in conf.read_text()

    mangohud.revert(p)
    text = conf.read_text()
    assert "font_size=24" in text
    assert mangohud._GMP_BEGIN not in text
    assert "fps" not in text


def test_disabled_writes_no_display_1(tmp_path, monkeypatch):
    conf = tmp_path / "MangoHud.conf"
    monkeypatch.setattr(mangohud, "MANGOHUD_CONF", conf)
    monkeypatch.setattr(mangohud, "MANGOHUD_DIR", tmp_path)

    p = GameProfile(exe="Wow.exe", mangohud={"enabled": False})
    mangohud.apply(p)
    assert "no_display=1" in conf.read_text()
