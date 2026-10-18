"""Minecraft 协议基础数据类型的读写。

对应 MCC 中 DataTypes.cs 的核心能力：VarInt / String / UUID / Long 等。
读取端基于 PacketBuffer（从完整包体字节切片），发送端为独立的构建函数。
"""

from __future__ import annotations

import struct
import uuid as uuid_mod
from typing import Any, Callable, Optional


class PacketReadError(Exception):
    """包体读取越界/格式错误。"""


class PacketBuffer:
    """已完整收齐的包体，提供游标式顺序读取。"""

    __slots__ = ("data", "offset")

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.offset = 0

    # ---- 基础 ----

    def remaining(self) -> int:
        return len(self.data) - self.offset

    def read_bytes(self, n: int) -> bytes:
        if n < 0 or n > self.remaining():
            raise PacketReadError(
                f"需要 {n} 字节但只剩 {self.remaining()} 字节 (offset={self.offset})"
            )
        out = self.data[self.offset : self.offset + n]
        self.offset += n
        return out

    def read_byte(self) -> int:
        return self.read_bytes(1)[0]

    def read_bool(self) -> bool:
        return self.read_byte() != 0

    def read_ubyte(self) -> int:
        return self.read_byte()

    def read_short(self) -> int:
        return struct.unpack(">h", self.read_bytes(2))[0]

    def read_ushort(self) -> int:
        return struct.unpack(">H", self.read_bytes(2))[0]

    def read_int(self) -> int:
        return struct.unpack(">i", self.read_bytes(4))[0]

    def read_long(self) -> int:
        return struct.unpack(">q", self.read_bytes(8))[0]

    def read_ulong(self) -> int:
        return struct.unpack(">Q", self.read_bytes(8))[0]

    def read_float(self) -> float:
        return struct.unpack(">f", self.read_bytes(4))[0]

    def read_double(self) -> float:
        return struct.unpack(">d", self.read_bytes(8))[0]

    # ---- 变长类型 ----

    def read_varint(self) -> int:
        """读取一个 VarInt（补码形式，最多 5 字节）。"""
        value = 0
        for i in range(5):
            b = self.read_byte()
            value |= (b & 0x7F) << (7 * i)
            if not (b & 0x80):
                # 协议中 VarInt 是补码有符号数，负数需要符号扩展
                if value & (1 << 31):
                    value -= 1 << 32
                return value
        raise PacketReadError("VarInt 超过 5 字节")

    def read_varlong(self) -> int:
        value = 0
        for i in range(10):
            b = self.read_byte()
            value |= (b & 0x7F) << (7 * i)
            if not (b & 0x80):
                if value & (1 << 63):
                    value -= 1 << 64
                return value
        raise PacketReadError("VarLong 超过 10 字节")

    def read_string(self, max_len: int = 32767) -> str:
        length = self.read_varint()
        if length < 0 or length > max_len * 3 + 3:
            raise PacketReadError(f"非法字符串长度 {length}")
        return self.read_bytes(length).decode("utf-8", errors="replace")

    def read_uuid(self) -> uuid_mod.UUID:
        return uuid_mod.UUID(bytes=self.read_bytes(16))

    def read_chat(self) -> str:
        """读取一个聊天组件（JSON 字符串原文，交由 chat 模块解析）。"""
        return self.read_string()


# ---------------------------------------------------------------------------
# 发送端：字节构建
# ---------------------------------------------------------------------------


def write_varint(value: int) -> bytes:
    """编码一个 VarInt。"""
    out = bytearray()
    value &= 0xFFFFFFFF  # 统一为无符号 32 位
    while True:
        b = value & 0x7F
        value >>= 7
        if value:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def write_int(value: int) -> bytes:
    return struct.pack(">i", value)


def write_long(value: int) -> bytes:
    return struct.pack(">q", value)


def write_float(value: float) -> bytes:
    return struct.pack(">f", value)


def write_double(value: float) -> bytes:
    return struct.pack(">d", value)


def write_ushort(value: int) -> bytes:
    return struct.pack(">H", value)


def write_short(value: int) -> bytes:
    return struct.pack(">h", value)


def write_position(x: int, y: int, z: int) -> bytes:
    """Position 打包：x/z 各 26 位、y 12 位，合成 64 位 long（无符号打包，位模式一致）。"""
    value = ((x & 0x3FFFFFF) << 38) | ((z & 0x3FFFFFF) << 12) | (y & 0xFFF)
    return struct.pack(">Q", value & 0xFFFFFFFFFFFFFFFF)


def write_bool(value: bool) -> bytes:
    return b"\x01" if value else b"\x00"


def write_string(text: str) -> bytes:
    raw = text.encode("utf-8")
    return write_varint(len(raw)) + raw


def write_uuid(value: uuid_mod.UUID) -> bytes:
    return value.bytes


# ---------------------------------------------------------------------------
# 物品栈（Item Stack）
# ---------------------------------------------------------------------------


def read_item_stack(
    buf: PacketBuffer,
    protocol: int,
    skip_nbt: Optional[Callable[[PacketBuffer], Any]] = None,
) -> Optional[tuple]:
    """读取一个物品栈，返回 (item_id, count) 或 None（空槽）。

    - 1.20.5+ (766+)：委托 components.read_item_stack_modern，按协议注册表
      逐个跳过物品组件（对应 MCC StructuredComponentsHandler）。
    - 1.20.4 (765)：present(bool) → item_id(VarInt) → count(byte) → NBT。
      NBT 跳过通过 skip_nbt 回调注入（避免 datatypes → client 循环导入）。
    """
    if protocol >= 766:
        from .components import read_item_stack_modern  # 延迟导入避免循环

        return read_item_stack_modern(buf, protocol, skip_nbt)
    if not buf.read_bool():
        return None
    item_id = buf.read_varint()
    count = buf.read_byte()
    if skip_nbt is not None:
        skip_nbt(buf)  # 物品 NBT（网络格式，1.20.2+ 根无名）
    return item_id, count


def empty_item_bytes(protocol: int) -> bytes:
    """序列化一个空物品槽（ClickWindow 的 changed_slots/carried item 用）。"""
    if protocol >= 766:
        return write_varint(0)
    return b"\x00"  # present = false


def read_varint_from_socket(recv_one) -> int:
    """从 socket 逐字节读取 VarInt。recv_one: () -> int，返回单字节。"""
    value = 0
    for i in range(5):
        b = recv_one()
        value |= (b & 0x7F) << (7 * i)
        if not (b & 0x80):
            if value & (1 << 31):
                value -= 1 << 32
            return value
    raise PacketReadError("VarInt 超过 5 字节")
