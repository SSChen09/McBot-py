"""按协议版本组织的包 ID 调色板。

对应 MCC 的 PacketPaletteXXX 机制：同一个语义包在不同版本有不同的数字 ID，
这里为每个支持的协议版本维护一张查表，客户端只写语义名，ID 由查表得到。

当前支持：1.20.4 (765)、1.21/1.21.1 (767)、1.21.4 (769)。
新增版本的方式与 MCC 相同：加一张表即可。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass(frozen=True)
class VersionInfo:
    protocol: int
    name: str
    play_in: Dict[str, int] = field(default_factory=dict)
    play_out: Dict[str, int] = field(default_factory=dict)
    config_in: Dict[str, int] = field(default_factory=dict)
    config_out: Dict[str, int] = field(default_factory=dict)

    def in_id(self, phase: str, name: str) -> Optional[int]:
        table = self.play_in if phase == "play" else self.config_in
        return table.get(name)

    def out_id(self, phase: str, name: str) -> Optional[int]:
        table = self.play_out if phase == "play" else self.config_out
        return table.get(name)


_VERSIONS: Dict[int, VersionInfo] = {}


def _register(info: VersionInfo) -> None:
    _VERSIONS[info.protocol] = info


# --- 1.20.4 (协议 765) -----------------------------------------------------

_register(VersionInfo(
    protocol=765,
    name="1.20.4",
    play_in={
        "chunk_data": 0x25,
        "chunk_batch_finished": 0x0C,
        "chunk_batch_started": 0x0D,
        "plugin_message": 0x18,
        "disconnect": 0x1B,
        "disguised_chat": 0x1C,
        "keep_alive": 0x24,
        "join_game": 0x29,
        "ping": 0x33,
        "player_chat": 0x37,
        "player_position_look": 0x3E,
        "remove_resource_pack": 0x43,
        "resource_pack": 0x44,
        "start_configuration": 0x67,
        "system_chat": 0x69,
        "update_health": 0x5B,
        "close_window": 0x12,
        "window_items": 0x13,
        "window_property": 0x14,
        "set_slot": 0x15,
        "open_window": 0x31,
    },
    play_out={
        "teleport_confirm": 0x00,
        "chat_command": 0x04,
        "chat_message": 0x05,
        "resource_pack_status": 0x28,
        "chunk_batch_received": 0x07,
        "client_settings": 0x09,
        "client_status": 0x08,
        "acknowledge_configuration": 0x0B,
        "click_window": 0x0D,
        "close_window": 0x0E,
        "interact_entity": 0x12,
        "keep_alive": 0x15,
        "player_position": 0x17,
        "player_position_and_rotation": 0x18,
        "player_input": 0x1A,
        "player_digging": 0x21,
        "pong": 0x24,
        "use_item_on": 0x35,
    },
    config_in={
        "plugin_message": 0x00,
        "disconnect": 0x01,
        "finish_configuration": 0x02,
        "keep_alive": 0x03,
        "ping": 0x04,
        "registry_data": 0x05,
        "resource_pack": 0x07,
    },
    config_out={
        "client_information": 0x00,
        "plugin_message": 0x01,
        "finish_configuration": 0x02,
        "keep_alive": 0x03,
        "pong": 0x04,
        "resource_pack_response": 0x05,
    },
))

# --- 1.21 / 1.21.1 (协议 767) ----------------------------------------------

_register(VersionInfo(
    protocol=767,
    name="1.21.1",
    play_in={
        "chunk_data": 0x27,
        "chunk_batch_finished": 0x0C,
        "chunk_batch_started": 0x0D,
        "cookie_request": 0x16,
        "plugin_message": 0x19,
        "disconnect": 0x1D,
        "disguised_chat": 0x1E,
        "keep_alive": 0x26,
        "join_game": 0x2B,
        "ping": 0x35,
        "player_chat": 0x39,
        "player_position_look": 0x40,
        "remove_resource_pack": 0x45,
        "resource_pack": 0x46,
        "start_configuration": 0x69,
        "system_chat": 0x6C,
        "update_health": 0x5D,
        "close_window": 0x12,
        "window_items": 0x13,
        "window_property": 0x14,
        "set_slot": 0x15,
        "open_window": 0x33,
    },
    play_out={
        "teleport_confirm": 0x00,
        "chat_command": 0x04,
        "signed_chat_command": 0x05,
        "chat_message": 0x06,
        "cookie_response": 0x11,
        "click_window": 0x0E,
        "close_window": 0x0F,
        "resource_pack_status": 0x2B,
        "chunk_batch_received": 0x08,
        "client_settings": 0x0A,
        "client_status": 0x09,
        "acknowledge_configuration": 0x0C,
        "interact_entity": 0x13,
        "keep_alive": 0x18,
        "player_position": 0x1A,
        "player_position_and_rotation": 0x1B,
        "player_input": 0x1D,
        "player_digging": 0x24,
        "pong": 0x27,
        "use_item_on": 0x38,
    },
    config_in={
        "cookie_request": 0x00,
        "plugin_message": 0x01,
        "disconnect": 0x02,
        "finish_configuration": 0x03,
        "keep_alive": 0x04,
        "ping": 0x05,
        "registry_data": 0x07,
        "resource_pack": 0x09,
        "known_packs": 0x0E,
    },
    config_out={
        "client_information": 0x00,
        "plugin_message": 0x02,
        "finish_configuration": 0x03,
        "keep_alive": 0x04,
        "pong": 0x05,
        "resource_pack_response": 0x06,
        "known_packs": 0x07,
    },
))

# --- 1.21.4 (协议 769) -----------------------------------------------------

_register(VersionInfo(
    protocol=769,
    name="1.21.4",
    play_in={
        "chunk_data": 0x28,
        "chunk_batch_finished": 0x0C,
        "chunk_batch_started": 0x0D,
        "cookie_request": 0x16,
        "plugin_message": 0x19,
        "disconnect": 0x1D,
        "disguised_chat": 0x1E,
        "keep_alive": 0x27,
        "join_game": 0x2C,
        "ping": 0x37,
        "player_chat": 0x3B,
        "player_position_look": 0x42,
        "remove_resource_pack": 0x4A,
        "resource_pack": 0x4B,
        "start_configuration": 0x70,
        "system_chat": 0x73,
        "update_health": 0x62,
        "close_window": 0x12,
        "window_items": 0x13,
        "window_property": 0x14,
        "set_slot": 0x15,
        "open_window": 0x35,
        "set_cursor_item": 0x5A,
        "set_player_inventory": 0x66,
    },
    play_out={
        "teleport_confirm": 0x00,
        "chat_command": 0x05,
        "signed_chat_command": 0x06,
        "chat_message": 0x07,
        "cookie_response": 0x13,
        "click_window": 0x10,
        "close_window": 0x11,
        "resource_pack_status": 0x2F,
        "chunk_batch_received": 0x09,
        "client_settings": 0x0C,
        "client_status": 0x0A,
        "acknowledge_configuration": 0x0E,
        "interact_entity": 0x15,
        "keep_alive": 0x1A,
        "player_position": 0x1C,
        "player_position_and_rotation": 0x1D,
        "player_input": 0x20,
        "player_digging": 0x27,
        "pong": 0x2B,
        "use_item_on": 0x3C,
    },
    config_in={
        "cookie_request": 0x00,
        "plugin_message": 0x01,
        "disconnect": 0x02,
        "finish_configuration": 0x03,
        "keep_alive": 0x04,
        "ping": 0x05,
        "registry_data": 0x07,
        "resource_pack": 0x09,
        "known_packs": 0x0E,
    },
    config_out={
        "client_information": 0x00,
        "plugin_message": 0x02,
        "finish_configuration": 0x03,
        "keep_alive": 0x04,
        "pong": 0x05,
        "resource_pack_response": 0x06,
        "known_packs": 0x07,
    },
))


def supported_versions() -> Dict[int, VersionInfo]:
    return dict(_VERSIONS)


def get_version(protocol: int) -> Optional[VersionInfo]:
    return _VERSIONS.get(protocol)


def find_by_name(name: str) -> Optional[VersionInfo]:
    for info in _VERSIONS.values():
        if info.name == name or name in info.name.split("/"):
            return info
    return None
