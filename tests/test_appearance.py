from unittest.mock import patch

import pytest

from token_plan_monitor.appearance import AppearancePreferences, LIGHT, DARK, resolve_theme, system_theme
from token_plan_monitor.storage import Storage


@pytest.mark.parametrize("value",[None,{},[],{"theme":"invalid"},{"theme":False},{"theme":[]},"dark"])
def test_invalid_or_missing_appearance_uses_system(value):
    assert AppearancePreferences.from_dict(value).theme=="system"


@pytest.mark.parametrize("mode",["light","dark","system"])
def test_appearance_roundtrip_is_independent_of_account_files(tmp_path,mode):
    storage=Storage(tmp_path)
    storage.config_path.write_text("existing account settings",encoding="utf-8")
    storage.widget_path.write_text("existing widget settings",encoding="utf-8")
    storage.snapshot_path.write_text("existing snapshot",encoding="utf-8")
    storage.save_appearance(AppearancePreferences(mode))
    assert storage.load_appearance().theme==mode
    assert storage.config_path.read_text()=="existing account settings"
    assert storage.widget_path.read_text()=="existing widget settings"
    assert storage.snapshot_path.read_text()=="existing snapshot"


def test_corrupt_appearance_recovers_without_rewriting_file(tmp_path):
    storage=Storage(tmp_path)
    storage.appearance_path.write_text("{broken",encoding="utf-8")
    assert storage.load_appearance().theme=="system"
    assert storage.appearance_path.read_text()=="{broken"


def test_system_selection_and_failed_registry_access():
    with patch("token_plan_monitor.appearance.system_theme",return_value="dark"):
        assert resolve_theme("system") is DARK
        assert resolve_theme("light") is LIGHT
    with patch("token_plan_monitor.appearance.winreg.OpenKey",side_effect=OSError):
        assert system_theme()=="light"


def luminance(colour):
    values=[v/255 for v in bytes.fromhex(colour[1:])]
    linear=[v/12.92 if v<=0.04045 else ((v+0.055)/1.055)**2.4 for v in values]
    return sum(v*w for v,w in zip(linear,(0.2126,0.7152,0.0722)))


@pytest.mark.parametrize("theme",[LIGHT,DARK])
def test_body_text_contrast(theme):
    for text in ("text","muted"):
        for background in ("surface","card"):
            first,second=sorted([luminance(theme.colors[text]),luminance(theme.colors[background])])
            assert (second+0.05)/(first+0.05)>=4.5


@pytest.mark.parametrize("theme",[LIGHT,DARK])
def test_primary_button_text_contrast(theme):
    for key in ("button_top","button_bottom","button_hover","button_pressed"):
        first,second=sorted([luminance(theme.colors["button_text"]),luminance(theme.colors[key])])
        assert (second+0.05)/(first+0.05)>=4.5


def test_font_selection_prefers_local_apple_fonts_and_handles_missing_families():
    from token_plan_monitor.appearance import installed_fonts
    assert installed_fonts(("Segoe UI", "Microsoft YaHei UI", "SF Pro Text", "PingFang SC")) == ("PingFang SC", "SF Pro Text")
    assert installed_fonts(()) == ("Microsoft YaHei UI", "Segoe UI")
    assert installed_fonts(("sf pro display", "苹方-简")) == ("苹方-简", "sf pro display")
