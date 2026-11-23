"""网络层：TCP 连接、Minecraft 帧格式与 zlib 压缩。

帧格式（未压缩）：VarInt 包长度 + VarInt 包 ID + 包体
帧格式（已启用压缩）：VarInt 帧长度 + VarInt 未压缩数据长度
    未压缩长度为 0 表示本帧数据未压缩，否则数据部分为 zlib 流。
对应 MCC 的 Protocol18.ReadNextPacket / SendPacket 与 SocketWrapper。
"""

from __future__ import annotations

import socket
import zlib
from typing import Optional, Tuple

from .datatypes import PacketBuffer, PacketReadError, write_varint


class ConnectionClosed(Exception):
    """连接被对端关闭。"""


class MCConnection:
    """封装一个 Minecraft 协议连接。"""

    def __init__(self, host: str, port: int, timeout: float = 10.0) -> None:
        self.host = host
        self.port = port
        self._sock = socket.create_connection((host, port), timeout=timeout)
        self._sock.settimeout(timeout)
        self._recv_buf = bytearray()
        self.compression_threshold: int = -1  # -1 表示未启用压缩
        self._send_lock = __import__("threading").Lock()

    # ---- 底层读取 ----

    def _recv_exact(self, n: int) -> bytes:
        while len(self._recv_buf) < n:
            try:
                chunk = self._sock.recv(65536)
            except socket.timeout as exc:
                raise ConnectionError("读取超时") from exc
            except OSError as exc:
                raise ConnectionClosed(str(exc)) from exc
            if not chunk:
                raise ConnectionClosed("服务器关闭了连接")
            self._recv_buf.extend(chunk)
        out = bytes(self._recv_buf[:n])
        del self._recv_buf[:n]
        return out

    def _read_varint(self) -> int:
        value = 0
        for i in range(5):
            b = self._recv_exact(1)[0]
            value |= (b & 0x7F) << (7 * i)
            if not (b & 0x80):
                if value & (1 << 31):
                    value -= 1 << 32
                return value
        raise PacketReadError("帧长度 VarInt 超过 5 字节")

    # ---- 包收发 ----

    def read_packet(self) -> Tuple[int, PacketBuffer]:
        """读取并解压一个完整包，返回 (包 ID, 包体)。"""
        frame_length = self._read_varint()
        if frame_length < 0 or frame_length > 2_097_152:
            raise PacketReadError(f"非法帧长度 {frame_length}")
        frame = self._recv_exact(frame_length)

        if self.compression_threshold < 0:
            data = frame
        else:
            # 解析内层的"未压缩长度" VarInt
            uncompressed_length = 0
            shift = 0
            idx = 0
            while True:
                b = frame[idx]
                idx += 1
                uncompressed_length |= (b & 0x7F) << shift
                shift += 7
                if not (b & 0x80):
                    break
            payload = frame[idx:]
            if uncompressed_length == 0:
                data = payload
            else:
                data = zlib.decompress(payload)

        buf = PacketBuffer(data)
        packet_id = buf.read_varint()
        return packet_id, buf

    def send_packet(self, packet_id: int, payload: bytes = b"") -> None:
        """按当前压缩设置编码并发送一个包。"""
        data = write_varint(packet_id) + payload
        if self.compression_threshold < 0:
            frame = write_varint(len(data)) + data
        else:
            if len(data) >= self.compression_threshold:
                compressed = zlib.compress(data)
                body = write_varint(len(data)) + compressed
            else:
                body = write_varint(0) + data
            frame = write_varint(len(body)) + body
        with self._send_lock:
            try:
                self._sock.sendall(frame)
            except OSError as exc:
                raise ConnectionClosed(str(exc)) from exc

    # ---- 生命周期 ----

    def enable_compression(self, threshold: int) -> None:
        self.compression_threshold = threshold

    def set_timeout(self, timeout: Optional[float]) -> None:
        self._sock.settimeout(timeout)

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass

    @property
    def closed(self) -> bool:
        return self._sock.fileno() == -1
