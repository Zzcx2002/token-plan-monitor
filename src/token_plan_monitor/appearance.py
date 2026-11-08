"""Application appearance is independent of account and quota configuration."""
from __future__ import annotations

import winreg
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

THEME_MODES = ("light", "dark", "system")
THEME_LABELS = {"light": "浅色", "dark": "深色", "system": "跟随系统"}


@dataclass(frozen=True)
class AppearancePreferences:
    theme: str = "system"

    def __post_init__(self) -> None:
        if self.theme not in THEME_MODES:
            raise ValueError("Unknown appearance theme")

    def to_dict(self) -> dict[str, str]:
        return {"theme": self.theme}

    @classmethod
    def from_dict(cls, value: Any) -> AppearancePreferences:
        mode = value.get("theme") if isinstance(value, dict) else None
        return cls(mode if isinstance(mode, str) and mode in THEME_MODES else "system")


@dataclass(frozen=True)
class Theme:
    name: str
    colors: Mapping[str, str]
    shadow_opacity: float
    radius: int = 20


LIGHT = Theme("light", MappingProxyType({
    "surface": "#F4F5F7", "card": "#FFFFFF", "border": "#E2E5EB",
    "text": "#191B20", "muted": "#646975", "accent": "#007AFF",
    "accent_active": "#0062CC", "accent_start": "#55B2FF",
    "button_top": "#0073E6", "button_hover": "#006BDA", "button_bottom": "#0067D6",
    "button_pressed": "#0062CC", "button_disabled": "#B6CBE5",
    "button_text": "#FFFFFF", "success": "#248A3D",
    "warning": "#B85A00", "warning_start": "#F6B34E", "warning_surface": "#FFF3E2",
    "error": "#C92A39", "info_surface": "#E7F1FF", "trough": "#E5E8EE",
    "card_top": "#FFFFFF", "card_bottom": "#F0F3F8", "highlight": "#FFFFFF",
    "hover": "#EAEFF6", "pressed": "#DFE6EF", "focus": "#007AFF",
}), 0.14)

DARK = Theme("dark", MappingProxyType({
    "surface": "#18191D", "card": "#24262B", "border": "#383C44",
    "text": "#F2F3F5", "muted": "#ADB2BD", "accent": "#409CFF",
    "accent_active": "#2584E6", "accent_start": "#70BEFF",
    "button_top": "#60B0FF", "button_hover": "#78BCFF", "button_bottom": "#409CFF",
    "button_pressed": "#2584E6", "button_disabled": "#354A65",
    "button_text": "#101922", "success": "#68D391",
    "warning": "#FFB75E", "warning_start": "#FFD28C", "warning_surface": "#382C20",
    "error": "#FF8791", "info_surface": "#203249", "trough": "#353942",
    "card_top": "#2B2E35", "card_bottom": "#22252B", "highlight": "#454A55",
    "hover": "#30353E", "pressed": "#393F49", "focus": "#70BEFF",
}), 0.30)


def system_theme() -> str:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return "light" if winreg.QueryValueEx(key, "AppsUseLightTheme")[0] else "dark"
    except OSError:
        return "light"


def resolve_theme(mode: str) -> Theme:
    name = system_theme() if mode == "system" else mode
    return DARK if name == "dark" else LIGHT


# Logical pixels; widget code applies the current Tk DPI scale.
OUTER_PADDING = 16
SECTION_GAP = 10
ROW_PADDING = 8


def installed_fonts(families: tuple[str, ...]) -> tuple[str, str]:
    """Select local Chinese body and Latin numeric families without downloads."""
    available = {name.casefold(): name for name in families}
    def choose(candidates: tuple[str, ...]) -> str:
        return next((available[name.casefold()] for name in candidates
                     if name.casefold() in available), candidates[-1])
    latin = choose(("SF Pro Text", "SF Pro Display", "Segoe UI"))
    chinese = choose(("PingFang SC", "苹方-简", "Microsoft YaHei UI"))
    return chinese, latin
