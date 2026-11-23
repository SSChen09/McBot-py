"""协议客户端：连接服务器、登录、配置阶段、游戏循环与聊天收发。

对应 MCC 的 Protocol18Handler，按语义包名 + versions 调色板组织，
只实现"连接服务器 + 聊天栏显示"所需的关键子集：
- Status ping（版本探测）
- 离线模式登录（Login Start / Set Compression / Login Success）
- 配置阶段自动应答（KeepAlive/Ping/RegistryData/KnownPacks/ResourcePack/Cookie）
- 游戏循环（KeepAlive、传送确认、区块批次应答、系统/玩家聊天渲染、断开）
- 聊天与命令发送（1.19+ 未签名格式；1.20.6+ 离线命令极简格式）
"""

from __future__ import annotations

import hashlib
import json
import struct
import threading
import time
import uuid as uuid_mod
from typing import Any, Callable, Dict, Optional

from .chat import render_component
from .datatypes import (
    PacketBuffer,
    write_bool,
    write_float,
    write_long,
    write_string,
    write_ushort,
    write_varint,
)
from .network import ConnectionClosed, MCConnection
from .versions import VersionInfo, get_version, supported_versions

# 登录阶段包 ID（固定，1.16+ 一致；来源 Protocol18.cs 行4383-4387）
LOGIN_OUT_LOGIN_START = 0x00
LOGIN_OUT_ENCRYPTION_RESPONSE = 0x01
LOGIN_OUT_PLUGIN_RESPONSE = 0x02
LOGIN_OUT_ACKNOWLEDGED = 0x03
LOGIN_OUT_COOKIE_RESPONSE = 0x04

LOGIN_IN_DISCONNECT = 0x00
LOGIN_IN_ENCRYPTION_REQUEST = 0x01
LOGIN_IN_LOGIN_SUCCESS = 0x02
LOGIN_IN_SET_COMPRESSION = 0x03
LOGIN_IN_PLUGIN_REQUEST = 0x04
LOGIN_IN_COOKIE_REQUEST = 0x05

LATEST_PROTOCOL = 769  # 1.21.4，ping 时作为客户端版本上报


def _parse_component(text: str) -> Any:
    """安全解析聊天组件：空串或非法 JSON 按纯文本处理，避免 json.loads 抛错。"""
    stripped = text.strip()
    if not stripped:
        return ""
    if stripped[0] in "{[":
        try:
            return json.loads(stripped)
        except ValueError:
            pass
    return text


def offline_uuid(username: str) -> uuid_mod.UUID:
    """离线模式 UUID：MD5("OfflinePlayer:" + name)，与原版一致。"""
    digest = hashlib.md5(("OfflinePlayer:" + username).encode("utf-8")).digest()
    return uuid_mod.UUID(bytes=digest)


def _read_nbt(buf: PacketBuffer) -> Any:
    """读取一个完整 NBT 标签（对应 MCC DataTypes.ReadNextNbtTag），返回 Python 值。

    1.20.2+ 网络 NBT 根 compound 无名字，直接以 tagType 字节开始，0 = End。
    """
    tag_type = buf.read_byte()
    if tag_type == 0:
        return None
    return _read_nbt_payload(buf, tag_type)


def _read_nbt_payload(buf: PacketBuffer, tag_type: int) -> Any:
    if tag_type == 1:      # Byte（有符号）
        b = buf.read_byte()
        return b - 256 if b >= 128 else b
    if tag_type == 2:      # Short
        return buf.read_short()
    if tag_type == 3:      # Int
        return buf.read_int()
    if tag_type == 4:      # Long
        return buf.read_long()
    if tag_type == 5:      # Float
        return buf.read_float()
    if tag_type == 6:      # Double
        return buf.read_double()
    if tag_type == 7:      # Byte Array
        return list(buf.read_bytes(buf.read_int()))
    if tag_type == 8:      # String
        return buf.read_bytes(buf.read_ushort()).decode("utf-8", errors="replace")
    if tag_type == 9:      # List
        elem_type = buf.read_byte()
        count = max(buf.read_int(), 0)
        if elem_type == 0:  # TAG_End 列表按规范 count 必为 0
            return []
        return [_read_nbt_payload(buf, elem_type) for _ in range(count)]
    if tag_type == 10:     # Compound
        out: Dict[str, Any] = {}
        while True:
            field_type = buf.read_byte()
            if field_type == 0:
                break
            name = buf.read_bytes(buf.read_ushort()).decode("utf-8", errors="replace")
            out[name] = _read_nbt_payload(buf, field_type)
        return out
    if tag_type == 11:     # Int Array
        return [buf.read_int() for _ in range(buf.read_int())]
    if tag_type == 12:     # Long Array
        return [buf.read_long() for _ in range(buf.read_int())]
    raise ValueError(f"未知 NBT 标签类型: {tag_type}")


_STYLE_KEYS = ("bold", "italic", "underlined", "strikethrough", "obfuscated")


def _nbt_to_component(value: Any) -> Any:
    """NBT 聊天组件 → JSON 等价结构，供 chat.render_component 渲染。"""
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for key, val in value.items():
            if key in _STYLE_KEYS and isinstance(val, (bool, int)):
                out[key] = bool(val)  # NBT 中样式是 Byte，JSON 中是 bool
            elif key in ("extra", "with") and isinstance(val, list):
                out[key] = [_nbt_to_component(v) for v in val]
            elif isinstance(val, (dict, list)):
                out[key] = _nbt_to_component(val)
            else:
                out[key] = val
        return out
    if isinstance(value, list):
        return [_nbt_to_component(v) for v in value]
    return value


def _read_component(buf: PacketBuffer) -> Any:
    """读取一个聊天组件，自动探测编码格式。

    - 网络 NBT（新版服务器）：根 TAG_Compound(0x0A)，其后首字段类型字节 ≤12；
    - JSON 字符串（常规 ≤1.21）：varint 长度前缀，长度 10 时首字节同为 0x0A，
      但下一字节是 JSON 文本（`"`/`{`/`[` 等 >12），据此区分。
    - TAG_End(0x00) 与空字符串都只占 1 字节且都表示空组件，读法一致。
    """
    if (
        buf.remaining() >= 2
        and buf.data[buf.offset] == 0x0A
        and buf.data[buf.offset + 1] <= 12
    ):
        return _nbt_to_component(_read_nbt(buf))
    return _parse_component(buf.read_string())


class McBotClient:
    """Minecraft 离线模式控制台客户端。"""

    def __init__(
        self,
        host: str,
        port: int = 25565,
        username: str = "McBot",
        timeout: float = 10.0,
        color_enabled: bool = False,
        on_chat: Optional[Callable[[str], None]] = None,
        on_disconnect: Optional[Callable[[str], None]] = None,
        auto_respawn: bool = True,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.uuid = offline_uuid(username)
        self.timeout = timeout
        self.color_enabled = color_enabled
        self.on_chat = on_chat
        self.on_disconnect = on_disconnect
        self.auto_respawn = auto_respawn

        self.conn: Optional[MCConnection] = None
        self.version: Optional[VersionInfo] = None
        self.protocol = 0
        self.phase = "login"  # login / config / play
        self.running = False

        # 玩家位置（用于相对传送与 player_position 回包）
        self.x = 0.0
        self.y = 0.0
        self.z = 0.0

        # 区块批次自适应（MCC 行105-107 初始值）
        self._batch_start_nanos = 0
        self._agg_nanos_per_chunk = 2_000_000.0
        self._old_samples_weight = 1

        # LastSeen 简化：记录服务器会话中最后见到的消息索引（offset），
        # 发送聊天时用它作为 acknowledgement offset
        self._message_count = 0

        # 玩家状态（UpdateHealth 包更新，GUI 定时轮询显示）
        self.health = 20.0
        self.food = 20.0
        self.saturation = 5.0

        # 死亡自动复活（MCC AutoRespawn）：health<=0 时延迟 1 秒发 ClientStatus(0)
        self._respawn_pending = False
        self._respawn_timer: Optional[threading.Timer] = None
        self._respawn_delay = 1.0  # 秒（MCC respawnTicks = ClientTicksPerSecond）

        self._recv_thread: Optional[threading.Thread] = None
        self._closing = False  # 主动断开标志（屏蔽关闭竞态产生的底层错误）

    # --- 状态查询 ping -------------------------------------------------------

    @staticmethod
    def ping_server(host: str, port: int = 25565, timeout: float = 5.0) -> Dict[str, Any]:
        """Status 握手：handshake(state=1) → status request → 解析 JSON。"""
        conn = MCConnection(host, port, timeout)
        try:
            handshake = (
                write_varint(LATEST_PROTOCOL)
                + write_string(host)
                + write_ushort(port)
                + write_varint(1)
            )
            conn.send_packet(0x00, handshake)
            conn.send_packet(0x00)  # Status Request（空包）
            packet_id, buf = conn.read_packet()
            if packet_id != 0x00:
                raise ConnectionClosed(f"意外的 status 包 ID: {packet_id:#x}")
            raw = json.loads(buf.read_string())
            # 用服务器返回的协议号选择调色板（供后续 connect 使用）
            return {
                "version": raw.get("version", {}),
                "players": raw.get("players", {}),
                "description": raw.get("description", {}),
                "raw": raw,
            }
        finally:
            conn.close()

    # --- 连接与登录 ----------------------------------------------------------

    def connect(self) -> None:
        """建立连接并完成登录 + 配置阶段，随后在后台线程运行游戏循环。

        先用 status ping 探测服务器协议版本，选择对应调色板（MCC GetVersion 等价）。
        """
        status = self.ping_server(self.host, self.port, self.timeout)
        server_protocol = int(status["version"].get("protocol", LATEST_PROTOCOL))
        version = get_version(server_protocol)
        if version is None:
            # 服务器版本不在调色板中：选不超过它的最大支持版本（MCC 回退策略）
            candidates = [p for p in supported_versions() if p <= server_protocol]
            protocol = max(candidates) if candidates else LATEST_PROTOCOL
            version = get_version(protocol)
        else:
            protocol = server_protocol
        self.protocol = protocol
        self.version = version

        self.conn = MCConnection(self.host, self.port, self.timeout)

        # 1. 握手（nextState=2 登录）
        handshake = (
            write_varint(self.protocol)
            + write_string(self.host)
            + write_ushort(self.port)
            + write_varint(2)
        )
        self.conn.send_packet(0x00, handshake)

        # 2. Login Start（1.20.2+ = username + uuid）
        self.conn.send_packet(
            LOGIN_OUT_LOGIN_START,
            write_string(self.username) + self.uuid.bytes,
        )

        # 3. 登录循环：直到 Login Success
        self._login_loop()

        # 4. 游戏循环（后台线程），配置阶段内的包也在这里处理
        self.running = True
        self._recv_thread = threading.Thread(target=self._play_loop, daemon=True)
        self._recv_thread.start()

    def _login_loop(self) -> None:
        assert self.conn is not None
        while True:
            packet_id, buf = self.conn.read_packet()
            if packet_id == LOGIN_IN_DISCONNECT:
                reason = render_component(_read_component(buf), self.color_enabled)
                raise ConnectionClosed(f"登录被拒绝: {reason}")
            elif packet_id == LOGIN_IN_ENCRYPTION_REQUEST:
                raise ConnectionClosed("服务器要求加密（online-mode），本客户端仅支持离线模式")
            elif packet_id == LOGIN_IN_LOGIN_SUCCESS:
                # 1.20.2+：回 Login Acknowledged 并进入配置阶段
                self.conn.send_packet(LOGIN_OUT_ACKNOWLEDGED)
                self.phase = "config"
                self._send_client_information()
                return
            elif packet_id == LOGIN_IN_SET_COMPRESSION:
                self.conn.enable_compression(buf.read_varint())
            elif packet_id == LOGIN_IN_PLUGIN_REQUEST:
                # 不支持任何插件通道：回 success=false
                msg_id = buf.read_varint()
                payload = write_varint(msg_id) + write_bool(False)
                self.conn.send_packet(LOGIN_OUT_PLUGIN_RESPONSE, payload)
            elif packet_id == LOGIN_IN_COOKIE_REQUEST:
                name = buf.read_string()
                payload = write_string(name) + write_bool(False)
                self.conn.send_packet(LOGIN_OUT_COOKIE_RESPONSE, payload)
            else:
                # 未知包忽略
                pass

    def _send_client_information(self) -> None:
        """ClientInformation / ClientSettings（MCC 行5388-5431 字段布局）。"""
        assert self.conn is not None and self.version is not None
        payload = (
            write_string("zh_CN")          # locale
            + struct.pack(">B", 8)          # view distance
            + write_varint(0)               # chat mode: 全部
            + write_bool(True)              # chat colors
            + struct.pack(">B", 0x7F)       # skin parts（全部显示）
            + write_varint(1)               # main hand: 右手
            + write_bool(False)             # text filtering（1.18.1+ 恒 0，此处 false 兼容 765）
            + write_bool(True)              # allow server listings（1.18.1+）
        )
        if self.protocol >= 769:
            payload += write_varint(0)      # particle status（1.21.2+）
        if self.phase == "play":
            packet_id = self.version.out_id("play", "client_settings")
        else:
            packet_id = self.version.out_id("config", "client_information")
        self.conn.send_packet(packet_id, payload)

    # --- 游戏循环 ------------------------------------------------------------

    def _play_loop(self) -> None:
        self.version = self.version or get_version(LATEST_PROTOCOL)
        try:
            while self.running:
                packet_id, buf = self.conn.read_packet()
                try:
                    if self.phase == "play":
                        self._handle_play_packet(packet_id, buf)
                    else:
                        self._handle_config_packet(packet_id, buf)
                except ConnectionClosed:
                    raise
                except Exception as exc:
                    # 帧是独立读取的，单包解析失败不影响后续包，记录后继续
                    self._emit_chat(f"[警告] 包解析错误 (id={packet_id:#x}): {exc}")
        except ConnectionClosed as exc:
            self._fire_disconnect(str(exc))
        except Exception as exc:  # 协议错误/网络错误等
            self._fire_disconnect(f"协议错误: {exc}")
        finally:
            self.running = False
            self._cancel_respawn()
            if self.conn:
                self.conn.close()

    def _handle_config_packet(self, packet_id: int, buf: PacketBuffer) -> None:
        assert self.conn is not None and self.version is not None
        v = self.version
        if packet_id == v.in_id("config", "disconnect"):
            reason = render_component(_read_component(buf), self.color_enabled)
            raise ConnectionClosed(f"配置阶段断开: {reason}")
        elif packet_id == v.in_id("config", "finish_configuration"):
            self.conn.send_packet(v.out_id("config", "finish_configuration"))
            self.phase = "play"
            # 进入 Play 后补发 ClientSettings（服务器可能依赖它完成加入）
            self._send_client_information()
        elif packet_id == v.in_id("config", "keep_alive"):
            self.conn.send_packet(v.out_id("config", "keep_alive"), buf.read_bytes(8))
        elif packet_id == v.in_id("config", "ping"):
            self.conn.send_packet(v.out_id("config", "pong"), buf.read_bytes(4))
        elif packet_id == v.in_id("config", "registry_data"):
            pass  # 注册表数据可整体忽略
        elif packet_id == v.in_id("config", "known_packs"):
            self._handle_known_packs(buf)
        elif packet_id == v.in_id("config", "resource_pack"):
            self._handle_resource_pack(buf, "config")
        elif packet_id == v.in_id("config", "cookie_request"):
            name = buf.read_string()
            payload = write_string(name) + write_bool(False)
            self.conn.send_packet(v.out_id("config", "cookie_response"), payload)
        # plugin_message 等未知包忽略

    def _handle_known_packs(self, buf: PacketBuffer) -> None:
        """Known Data Packs：只回传 namespace=='minecraft' 的条目（MCC 行652-668）。"""
        assert self.conn is not None and self.version is not None
        count = buf.read_varint()
        mine: list[tuple[str, str, str]] = []
        for _ in range(count):
            namespace = buf.read_string()
            pack_id = buf.read_string()
            version = buf.read_string()
            if namespace == "minecraft":
                mine.append((namespace, pack_id, version))
        payload = write_varint(len(mine))
        for namespace, pack_id, version in mine:
            payload += write_string(namespace) + write_string(pack_id) + write_string(version)
        self.conn.send_packet(self.version.out_id("config", "known_packs"), payload)

    def _handle_resource_pack(self, buf: PacketBuffer, phase: str) -> None:
        """资源包请求：全部版本 ≥1.20.4，先读 UUID，回 accepted + loaded。"""
        assert self.conn is not None and self.version is not None
        pack_uuid = buf.read_uuid()
        _url = buf.read_string()
        _hash = buf.read_string()
        buf.read_bool()  # forced
        if buf.read_bool():  # hasPromptMessage
            prompt = _read_component(buf)
            self._emit_chat(f"[资源包] {render_component(prompt, self.color_enabled)}")
        payload = pack_uuid.bytes + write_varint(3) + write_varint(0)
        self.conn.send_packet(self.version.out_id(phase, "resource_pack_response"), payload)

    def _handle_play_packet(self, packet_id: int, buf: PacketBuffer) -> None:
        assert self.conn is not None and self.version is not None
        v = self.version
        if packet_id == v.in_id("play", "join_game"):
            pass  # 加入游戏，无需解析包体
        elif packet_id == v.in_id("play", "keep_alive"):
            self.conn.send_packet(v.out_id("play", "keep_alive"), buf.read_bytes(8))
        elif packet_id == v.in_id("play", "ping"):
            self.conn.send_packet(v.out_id("play", "pong"), buf.read_bytes(4))
        elif packet_id == v.in_id("play", "player_position_look"):
            self._handle_teleport(buf)
        elif packet_id == v.in_id("play", "chunk_batch_started"):
            self._batch_start_nanos = time.perf_counter_ns()
        elif packet_id == v.in_id("play", "chunk_batch_finished"):
            self._handle_chunk_batch_finished(buf)
        elif packet_id == v.in_id("play", "start_configuration"):
            # 服务器切回配置阶段：确认后进入 config
            self.conn.send_packet(v.out_id("play", "acknowledge_configuration"))
            self.phase = "config"
        elif packet_id == v.in_id("play", "system_chat"):
            component = _read_component(buf)
            buf.read_bool()  # overlay（ActionBar 标志）
            self._emit_chat(render_component(component, self.color_enabled))
        elif packet_id == v.in_id("play", "disguised_chat"):
            self._handle_disguised_chat(buf)
        elif packet_id == v.in_id("play", "player_chat"):
            self._handle_player_chat(buf)
        elif packet_id == v.in_id("play", "disconnect"):
            reason = render_component(_read_component(buf), self.color_enabled)
            raise ConnectionClosed(f"被服务器断开: {reason}")
        elif packet_id == v.in_id("play", "resource_pack"):
            self._handle_resource_pack(buf, "play")
        elif packet_id == v.in_id("play", "remove_resource_pack"):
            pass  # 只有 UUID，无需处理
        elif packet_id == v.in_id("play", "cookie_request"):
            name = buf.read_string()
            payload = write_string(name) + write_bool(False)
            self.conn.send_packet(v.out_id("play", "cookie_response"), payload)
        elif packet_id == v.in_id("play", "update_health"):
            # float health + varint food + float saturation（MCC 行2938-2942）
            self.health = buf.read_float()
            self.food = float(buf.read_varint())
            self.saturation = buf.read_float()
            self._on_update_health()
        # plugin_message 等未知包忽略

    # --- 死亡自动复活（MCC AutoRespawn） ------------------------------------

    def _on_update_health(self) -> None:
        """UpdateHealth 后触发复活（MCC OnUpdateHealth 行4577-4596）。

        死亡（health<=0）且开启自动复活时，延迟 1 秒（respawnTicks）后发送
        ClientStatus(0) 复活包；存活（复活成功或被治疗）时取消挂起的复活。
        """
        if self.health <= 0:
            if self.auto_respawn and not self._respawn_pending:
                self._respawn_pending = True
                # 先启动定时器再发日志：聊天回调异常不能阻断复活
                timer = threading.Timer(self._respawn_delay, self.respawn)
                timer.daemon = True  # 断开时不阻塞进程退出
                self._respawn_timer = timer
                timer.start()
                self._emit_chat(f"[死亡] 生命值归零，{self._respawn_delay:.0f} 秒后自动复活")
        else:
            self._cancel_respawn()
            self._respawn_pending = False

    def _cancel_respawn(self) -> None:
        if self._respawn_timer is not None:
            self._respawn_timer.cancel()
            self._respawn_timer = None

    def respawn(self) -> bool:
        """发送复活包（ClientStatus action=0，MCC SendRespawnPacket 行5339-5358）。

        既可手动调用，也是自动复活定时器的回调；仅在 play 阶段发送。
        """
        self._respawn_timer = None
        if self._closing or not self.running:
            self._emit_chat("[复活] 跳过：客户端已断开")
            self._respawn_pending = False
            return False
        assert self.conn is not None and self.version is not None
        if self.phase != "play":
            self._emit_chat(f"[复活] 跳过：当前阶段 {self.phase}")
            self._respawn_pending = False
            return False
        try:
            packet_id = v_out(self, "client_status")
            self.conn.send_packet(packet_id, b"\x00")
        except Exception as exc:
            self._emit_chat(f"[警告] 复活包发送失败: {exc}")
            self._respawn_pending = False
            return False
        self._respawn_pending = False
        self._emit_chat(f"[复活] 已发送复活包 (id={packet_id:#x})")
        return True

    def _handle_teleport(self, buf: PacketBuffer) -> None:
        """Synchronize Player Position：回 TeleportConfirm + PlayerPosition。"""
        assert self.conn is not None and self.version is not None
        if self.protocol >= 769:
            # 1.21.2+：teleportId + 3×pos + 3×delta + yaw + pitch + locMask(int)
            teleport_id = buf.read_varint()
            pos = [buf.read_double() for _ in range(3)]
            delta = [buf.read_double() for _ in range(3)]
            buf.read_float()  # yaw
            buf.read_float()  # pitch
            mask = buf.read_int() & 0xFFFFFFFF
            for i in range(3):
                if mask & (1 << i):  # 相对坐标：当前位置 + delta
                    pos[i] = [self.x, self.y, self.z][i] + delta[i]
        else:
            # 1.21.1 及更早：3×pos + yaw + pitch + flags(byte) + teleportId
            pos = [buf.read_double() for _ in range(3)]
            buf.read_float()
            buf.read_float()
            flags = buf.read_ubyte()
            teleport_id = buf.read_varint()
            for i in range(3):
                if flags & (1 << i):  # 相对坐标：坐标字段本身是增量
                    pos[i] = [self.x, self.y, self.z][i] + pos[i]
        self.x, self.y, self.z = pos

        self.conn.send_packet(v_out(self, "teleport_confirm"), write_varint(teleport_id))
        payload = (
            struct.pack(">ddd", self.x, self.y, self.z)
            + write_bool(True)  # onGround
        )
        self.conn.send_packet(v_out(self, "player_position"), payload)

    def _handle_chunk_batch_finished(self, buf: PacketBuffer) -> None:
        """自适应批次应答速率（MCC 行1293-1347）。"""
        assert self.conn is not None and self.version is not None
        batch_size = buf.read_varint()
        elapsed = time.perf_counter_ns() - self._batch_start_nanos
        if batch_size > 0:
            nanos_per_chunk = elapsed / batch_size
            low = self._agg_nanos_per_chunk / 3.0
            high = self._agg_nanos_per_chunk * 3.0
            nanos_per_chunk = max(low, min(high, nanos_per_chunk))
            self._agg_nanos_per_chunk = (
                self._agg_nanos_per_chunk * self._old_samples_weight + nanos_per_chunk
            ) / (self._old_samples_weight + 1)
            self._old_samples_weight = min(49, self._old_samples_weight + 1)
        wanted = 7_000_000.0 / self._agg_nanos_per_chunk  # 目标每秒 700万纳秒
        self.conn.send_packet(
            self.version.out_id("play", "chunk_batch_received"), write_float(wanted)
        )

    # --- 聊天解析 ------------------------------------------------------------

    def _handle_disguised_chat(self, buf: PacketBuffer) -> None:
        component = _read_component(buf)
        self._read_chat_type_holder(buf)
        chat_name = _read_component(buf)
        if buf.read_bool():
            _read_component(buf)  # target name（忽略）
        text = render_component(chat_name, self.color_enabled)
        self._emit_chat(f"{text} > {render_component(component, self.color_enabled)}")

    def _handle_player_chat(self, buf: PacketBuffer) -> None:
        """Player Chat（1.19.3+），按 MCC 行1181-1290 顺序解析。"""
        buf.read_bytes(16)  # sender uuid
        idx = buf.read_varint()   # index：全局会话消息索引
        if idx >= 0:
            self._message_count = idx + 1  # 记录最后见到的消息索引+1（即 offset）
        if buf.read_bool():  # hasSignature
            buf.read_bytes(256)
        message = buf.read_string()
        buf.read_long()  # timestamp
        buf.read_long()  # salt
        # previous messages
        for _ in range(buf.read_varint()):
            if buf.read_varint() == -1:
                buf.read_bytes(256)
        # 未签名聊天内容（离线模式通常在这里）
        unsigned: Optional[str] = None
        if buf.read_bool():
            unsigned = buf.read_string()
        # 过滤类型
        filter_type = buf.read_varint()
        if filter_type == 1:  # PartiallyFiltered
            for _ in range(buf.read_varint()):
                buf.read_long()
        self._read_chat_type_holder(buf)
        chat_name = _read_component(buf)
        if buf.read_bool():
            _read_component(buf)  # target name（忽略）

        content = unsigned if unsigned is not None else message
        name_text = render_component(chat_name, self.color_enabled)
        body = render_component(_parse_component(content), self.color_enabled)
        self._emit_chat(f"<{name_text}> {body}")

    def _read_chat_type_holder(self, buf: PacketBuffer) -> None:
        """chatTypeHolder（ChatParser.cs 行115-148）：1.21+ 可能内联 decoration。"""
        encoded_id = buf.read_varint()
        if self.protocol >= 767 and encoded_id == 0:
            # 内联 decoration：读两遍（network + presentation）
            for _ in range(2):
                buf.read_string()  # translation key
                for _ in range(buf.read_varint()):
                    buf.read_varint()  # parameter 枚举
                _read_nbt(buf)  # style NBT

    # --- 发送 ----------------------------------------------------------------

    def send_chat(self, message: str) -> None:
        """发送聊天（1.19+ 未签名格式：string + timestamp + salt 0 + 无签名
        + messageCount + 3 字节 bitset）。"""
        assert self.conn is not None and self.version is not None
        if self.phase != "play":
            return
        payload = (
            write_string(message)
            + write_long(int(time.time() * 1000))
            + write_long(0)             # salt
            + write_bool(False)         # hasSignature
            + write_varint(max(0, self._message_count - 1))  # ack offset = 最后见到的消息索引
            + b"\x00\x00\x00"           # 3 字节 ack bitset（全 0，无 ack）
        )
        self.conn.send_packet(self.version.out_id("play", "chat_message"), payload)

    def send_command(self, command: str) -> None:
        """发送命令（不带前导 '/'）。

        1.20.6+ 离线模式：只发 command 字符串（MCC 行5047-5053）；
        1.20.4 (765)：走完整未签名格式。
        """
        assert self.conn is not None and self.version is not None
        if self.phase != "play":
            return
        if self.protocol >= 766:  # 1.20.6+ 离线：极简格式
            payload = write_string(command.lstrip("/"))
            packet_id = self.version.out_id("play", "chat_command")
        else:
            payload = (
                write_string(command.lstrip("/"))
                + write_long(int(time.time() * 1000))
                + write_long(0)
                + write_bool(False)      # 无签名
                + write_varint(0)        # 签名的命令消息数 = 0
                + write_varint(max(0, self._message_count - 1))  # ack offset
                + b"\x00\x00\x00"
            )
            packet_id = self.version.out_id("play", "chat_command")
        self.conn.send_packet(packet_id, payload)

    # --- 杂项 ----------------------------------------------------------------

    def _emit_chat(self, text: str) -> None:
        if self.on_chat:
            self.on_chat(text)

    def _fire_disconnect(self, reason: str) -> None:
        if self._closing:
            reason = "断开连接"  # 主动 close 时 recv 线程可能报 10038 等竞态错误
        if self.on_disconnect:
            self.on_disconnect(reason)

    def disconnect(self, reason: str = "断开连接") -> None:
        self._closing = True
        self.running = False
        self._cancel_respawn()
        if self.conn:
            self.conn.close()

    def join(self, timeout: Optional[float] = None) -> None:
        """等待游戏循环线程结束。"""
        if self._recv_thread:
            self._recv_thread.join(timeout)


def v_out(client: McBotClient, name: str) -> int:
    """Play 阶段出包 ID 的便捷获取。"""
    assert client.version is not None
    packet_id = client.version.out_id("play", name)
    assert packet_id is not None
    return packet_id
