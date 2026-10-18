"""聊天组件解析与终端渲染。

两步流水线（对应 MCC 的 ChatParser + ConsoleIO 颜色处理）：
1. component_to_code(): JSON 聊天组件 → 带 § 颜色码的纯文本
2. code_to_ansi(): § 颜色码 → ANSI 转义序列，供现代终端着色
"""

from __future__ import annotations

import json
import re
from typing import Any, List

# § 颜色码 -> ANSI SGR 参数
_COLOR_MAP = {
    "0": "30",  # black
    "1": "34",  # dark_blue
    "2": "32",  # dark_green
    "3": "36",  # dark_aqua
    "4": "31",  # dark_red
    "5": "35",  # dark_purple
    "6": "33",  # gold
    "7": "37",  # gray
    "8": "90",  # dark_gray
    "9": "94",  # blue
    "a": "92",  # green
    "b": "96",  # aqua
    "c": "91",  # red
    "d": "95",  # light_purple
    "e": "93",  # yellow
    "f": "97",  # white
}

_NAMED_COLORS = {
    "black": "0", "dark_blue": "1", "dark_green": "2", "dark_aqua": "3",
    "dark_red": "4", "dark_purple": "5", "gold": "6", "gray": "7",
    "dark_gray": "8", "blue": "9", "green": "a", "aqua": "b",
    "red": "c", "light_purple": "d", "yellow": "e", "white": "f",
    "reset": "r",
}

_FORMAT_MAP = {
    "bold": "l",
    "strikethrough": "m",
    "underlined": "n",
    "italic": "o",
    "obfuscated": "k",
}

_ANSI_RESET = "\033[0m"


def component_to_code(component: Any) -> str:
    """递归展开聊天组件为带 § 码的字符串。"""
    if component is None:
        return ""
    if isinstance(component, str):
        # 可能是 JSON 文本，也可能是纯文本
        stripped = component.lstrip()
        if stripped.startswith(("{", "[")):
            try:
                return component_to_code(json.loads(component))
            except (json.JSONDecodeError, ValueError):
                return component
        return component
    if isinstance(component, list):
        return "".join(component_to_code(item) for item in component)
    if not isinstance(component, dict):
        return str(component)

    out: List[str] = []

    color = component.get("color")
    if isinstance(color, str):
        if color.startswith("#") and len(color) == 7:
            out.append(_hex_to_nearest_code(color))
        elif color in _NAMED_COLORS:
            out.append("§" + _NAMED_COLORS[color])

    for style, code in _FORMAT_MAP.items():
        if component.get(style) is True:
            out.append("§" + code)

    if "text" in component:
        out.append(str(component["text"]))
    elif "translate" in component:
        out.append(_render_translate(component))
    elif "keybind" in component:
        out.append(str(component["keybind"]))
    elif "selector" in component:
        out.append(str(component["selector"]))

    for extra in component.get("extra", []):
        out.append(component_to_code(extra))

    return "".join(out)


def _render_translate(component: dict) -> str:
    """处理 translate 字段；聊天类型模板按 vanilla 习惯拼接。"""
    key = str(component.get("translate", ""))
    args = [component_to_code(w).strip("§") for w in component.get("with", [])]
    if key in ("chat.type.text", "chat.type.text.narrate", "commands.message.display.incoming"):
        if len(args) >= 2:
            return f"<{args[0]}> {args[1]}"
        return key
    if key in ("chat.type.announcement",):
        if len(args) >= 2:
            return f"[{args[0]}] {args[1]}"
        return key
    # 模板自带 %s 占位符（部分服务器直接把格式串当 key）：按序代入参数
    if "%s" in key:
        out = key
        rest = []
        for a in args:
            if "%s" in out:
                out = out.replace("%s", a, 1)
            else:
                rest.append(a)
        if rest:
            out = out + " " + " ".join(rest)
        return out
    if args:
        return key + " " + " ".join(args)
    return key


def _hex_to_nearest_code(hex_color: str) -> str:
    """1.16+ 的 #RRGGBB 颜色就近映射到 16 种基础色。"""
    try:
        r = int(hex_color[1:3], 16)
        g = int(hex_color[3:5], 16)
        b = int(hex_color[5:7], 16)
    except ValueError:
        return "§f"
    palette = {
        "0": (0, 0, 0), "1": (0, 0, 170), "2": (0, 170, 0), "3": (0, 170, 170),
        "4": (170, 0, 0), "5": (170, 0, 170), "6": (255, 170, 0), "7": (170, 170, 170),
        "8": (85, 85, 85), "9": (85, 85, 255), "a": (85, 255, 85), "b": (85, 255, 255),
        "c": (255, 85, 85), "d": (255, 85, 255), "e": (255, 255, 85), "f": (255, 255, 255),
    }
    best = min(palette.items(), key=lambda kv: sum((a - b) ** 2 for a, b in zip(kv[1], (r, g, b))))
    return "§" + best[0]


def code_to_ansi(text: str, color_enabled: bool = True) -> str:
    """把 § 颜色/格式码转换为 ANSI 序列。"""
    if not color_enabled:
        return re.sub(r"§[0-9a-fk-or]", "", text)

    out: List[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "§" and i + 1 < n:
            code = text[i + 1].lower()
            if code in _COLOR_MAP:
                out.append(f"\033[{_COLOR_MAP[code]}m")
            elif code == "r":
                out.append(_ANSI_RESET)
            elif code in ("l", "m", "n", "o", "k"):
                out.append({
                    "l": "\033[1m", "m": "\033[9m",
                    "n": "\033[4m", "o": "\033[3m", "k": "\033[8m",
                }[code])
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out) + _ANSI_RESET


def render_component(component: Any, color_enabled: bool = True) -> str:
    """一步到位：聊天组件 JSON → 终端可显示的彩色文本。"""
    return code_to_ansi(component_to_code(component), color_enabled)
