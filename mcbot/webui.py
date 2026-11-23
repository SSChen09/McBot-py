"""McBot Web 控制台：标准库 HTTP 服务，复用 McBotClient 与自动回复引擎。

启动：
    python -m mcbot.webui                # 单独运行，默认 0.0.0.0:6099
    python -m mcbot.webui --port 6099
    python -m mcbot.webui --host 127.0.0.1

桌面端同启时由 `mcbot.gui` 调用 ``start(host, port, manager=DesktopWebManager)``：
Web 面板与桌面窗口共用同一批机器人；单独运行时 ``main`` 使用自带
``BotManager``，只与桌面端共用 config.json。

说明：
- 浏览器页面由 mcbot/web/ 下的静态文件提供，不依赖第三方库；
- 控制台日志通过 /api/state?since=<seq> 增量拉取。
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import secrets
import socket
import threading
import time
from collections import deque
from dataclasses import dataclass
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, unquote, urlparse

from mcbot import webauth
from mcbot.auto_reply import AutoReplyEngine, parse_private_chat
from mcbot.client import McBotClient
from mcbot.keycmd import KeyCommandHandler
from mcbot.teleport import TeleportService

BASE_DIR = Path(__file__).resolve().parent.parent      # McBot-py/
WEB_DIR = Path(__file__).resolve().parent / "web"      # mcbot/web/
CONFIG_FILE = BASE_DIR / "config.json"
LOG_FILE = BASE_DIR / "logs" / "web.log"

# 登录会话：随机 token -> 密钥（密钥失效时会话自动失效）
SESSIONS: Dict[str, str] = {}
SESSIONS_LOCK = threading.Lock()
SESSION_COOKIE = "mcbot_session"
SESSION_MAX_AGE = 24 * 3600

DEFAULT_CONFIG: Dict[str, Any] = {
    "current_line": 1,
    "lines": {"1": "", "2": "", "3": "", "4": ""},
    "addr": "",
    "gap": 1,
    "auto_reconnect": True,
    "auto_login": True,
    "auto_respawn": True,
    "accounts": [],
    "auto_reply": {"enabled": False, "masters": [], "rules": []},
}


def parse_addr(addr: str) -> tuple:
    """把 "host:port" 解析成 (host, port)；缺省端口 25565。"""
    addr = (addr or "").strip()
    host, _, port = addr.partition(":")
    try:
        port_n = int(port) if port else 25565
    except ValueError:
        port_n = 25565
    return (host or addr, port_n)


def ping_target(addr: str) -> Dict[str, Any]:
    """服务器状态 ping（测速）；Web 面板与桌面端桥接共用。"""
    host, port = parse_addr(addr)
    if not host:
        return {"error": "请先填写服务器地址"}
    t0 = time.perf_counter()
    try:
        info = McBotClient.ping_server(host, port, timeout=3.0)
    except socket.gaierror:
        return {"error": f"域名解析失败：{host}"}
    except Exception as exc:
        return {"error": f"测速失败：{exc}"}
    ms = int((time.perf_counter() - t0) * 1000)
    version = info.get("version") or {}
    players = info.get("players") or {}
    return {
        "ok": True,
        "latency": ms,
        "version": version.get("name", "?"),
        "protocol": version.get("protocol"),
        "online": players.get("online"),
        "max": players.get("max"),
        "description": info.get("description", ""),
    }


# ---------------------------------------------------------------- 账号状态


@dataclass
class BotAccount:
    name: str
    password: str = ""
    status: str = "离线"          # 离线 / 连接中 / 在线 / 断开
    client: Optional[McBotClient] = None
    manual_stop: bool = False
    last_error: str = ""
    token: int = 0                # 连接代次，避免旧回调覆盖新状态


# ---------------------------------------------------------------- 管理器


class BotManager:
    """线程安全的多账号管理：连接、重连、聊天、自动回复、配置持久化。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._accounts: List[BotAccount] = []
        self._by_name: Dict[str, BotAccount] = {}
        self._logs: deque = deque(maxlen=1200)
        self._seq = 0
        self.config: Dict[str, Any] = dict(DEFAULT_CONFIG)
        self.auto_reply = AutoReplyEngine()
        self.key_commands = KeyCommandHandler()
        self.teleport = TeleportService()
        self._load_config()

    # ---- 配置 ----

    def _load_config(self) -> None:
        cfg = json.loads(json.dumps(DEFAULT_CONFIG))
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                cfg.update(data)
        except (OSError, ValueError):
            pass
        cfg["lines"] = {str(k): str(v) for k, v in (cfg.get("lines") or {}).items()}
        for i in range(1, 5):
            cfg["lines"].setdefault(str(i), "")
        cfg["gap"] = max(1, int(cfg.get("gap") or 1))
        with self._lock:
            self.config = cfg
            self.auto_reply.load(cfg.get("auto_reply") or {})
            for item in cfg.get("accounts") or []:
                name = str(item.get("name", "")).strip()
                if not name or name in self._by_name:
                    continue
                acc = BotAccount(name=name, password=str(item.get("password", "")))
                self._accounts.append(acc)
                self._by_name[name] = acc
        self._log("system", "Web 控制台已启动，配置已加载")

    def save_config(self) -> None:
        with self._lock:
            cfg = dict(self.config)
            cfg["accounts"] = [
                {"name": a.name, "password": a.password} for a in self._accounts
            ]
            cfg["auto_reply"] = self.auto_reply.to_dict()
            try:
                CONFIG_FILE.write_text(
                    json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
            except OSError as exc:
                self._log("system", f"[系统] 保存配置失败：{exc}")

    # ---- 日志 ----

    def _log(self, kind: str, text: str, account: str = "") -> None:
        with self._lock:
            self._seq += 1
            item = {
                "seq": self._seq,
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "kind": kind,
                "text": text,
                "account": account,
            }
            self._logs.append(item)
        try:
            LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"{item['ts']} {text}\n")
        except OSError:
            pass

    def logs_since(self, since: int, limit: int = 400) -> List[Dict[str, Any]]:
        with self._lock:
            items = [x for x in self._logs if x["seq"] > since]
        return items[-limit:]

    # ---- 状态快照 ----

    def snapshot(self, since: int = 0) -> Dict[str, Any]:
        with self._lock:
            accounts = []
            online = 0
            for a in self._accounts:
                c = a.client
                if a.status == "在线":
                    online += 1
                accounts.append({
                    "name": a.name,
                    "status": a.status,
                    "online": a.status == "在线",
                    "has_password": bool(a.password),
                    "error": a.last_error,
                    "hp": round(float(c.health), 1) if c and a.status == "在线" else None,
                    "food": int(c.food) if c and a.status == "在线" else None,
                    "x": round(c.x, 2) if c and a.status == "在线" else None,
                    "y": round(c.y, 2) if c and a.status == "在线" else None,
                    "z": round(c.z, 2) if c and a.status == "在线" else None,
                })
            cfg = {
                "addr": self.config.get("addr", ""),
                "gap": int(self.config.get("gap", 1)),
                "current_line": int(self.config.get("current_line", 1)),
                "lines": dict(self.config.get("lines") or {}),
                "auto_reconnect": bool(self.config.get("auto_reconnect", True)),
                "auto_login": bool(self.config.get("auto_login", True)),
                "auto_respawn": bool(self.config.get("auto_respawn", True)),
            }
            reply = self.auto_reply.to_dict()
        return {
            "ok": True,
            "stats": {"online": online, "total": len(accounts),
                      "addr": cfg["addr"], "seq": self._seq},
            "accounts": accounts,
            "config": cfg,
            "auto_reply": reply,
            "logs": self.logs_since(since),
        }

    # ---- 账号 ----

    def add_account(self, name: str, password: str) -> Dict[str, Any]:
        name = (name or "").strip()
        if not name:
            return {"error": "账号名不能为空"}
        with self._lock:
            if name in self._by_name:
                return {"error": f"账号 {name} 已存在"}
            acc = BotAccount(name=name, password=password or "")
            self._accounts.append(acc)
            self._by_name[name] = acc
        self.save_config()
        self._log("system", f"[系统] 已添加账号 {name}")
        return {"ok": True}

    def remove_accounts(self, names: List[str]) -> Dict[str, Any]:
        removed = 0
        with self._lock:
            targets = [self._by_name[n] for n in names if n in self._by_name]
        for acc in targets:
            self.disconnect([acc.name], quiet=True)
            with self._lock:
                acc.manual_stop = True
                if acc in self._accounts:
                    self._accounts.remove(acc)
                self._by_name.pop(acc.name, None)
            self._log("system", f"[系统] 已删除账号 {acc.name}")
            removed += 1
        if removed:
            self.save_config()
        return {"ok": True, "removed": removed}

    # ---- 连接 ----

    def _addr(self) -> tuple:
        return parse_addr(str(self.config.get("addr", "")))

    def connect_many(self, names: List[str]) -> Dict[str, Any]:
        if not str(self.config.get("addr", "")).strip():
            return {"error": "请先填写服务器地址"}
        with self._lock:
            valid = [n for n in names if n in self._by_name]
        if not valid:
            return {"error": "没有可上线的账号"}
        threading.Thread(target=self._connect_seq, args=(valid,), daemon=True).start()
        return {"ok": True, "queued": valid}

    def _connect_seq(self, names: List[str]) -> None:
        gap = max(1, int(self.config.get("gap", 1)))
        for i, name in enumerate(names):
            if i:
                time.sleep(gap)
            self._connect_one(name)

    def _connect_one(self, name: str) -> None:
        with self._lock:
            acc = self._by_name.get(name)
            if acc is None or acc.status in ("在线", "连接中"):
                return
            acc.manual_stop = False
            acc.status = "连接中"
            acc.last_error = ""
            acc.token += 1
            token = acc.token
        self._log("system", f"[{name}] 正在连接 {self.config['addr']} …", name)

        host, port = self._addr()
        client = McBotClient(
            host, port=port, username=name,
            on_chat=lambda text, n=name: self._on_chat(n, text),
            on_disconnect=lambda reason, n=name: self._on_disconnect(n, reason),
            on_teleport=lambda x, y, z, n=name: self.teleport.on_teleport(
                n, x, y, z),
            auto_respawn=bool(self.config.get("auto_respawn", True)),
        )
        try:
            client.connect()
        except Exception as exc:
            if token == acc.token:
                acc.status = "断开"
                acc.last_error = str(exc)
            self._log("system", f"[{name}] ❌ 连接失败：{exc}", name)
            self._maybe_reconnect(name, token)
            return

        with self._lock:
            if acc.manual_stop or token != acc.token:
                stale = True
            else:
                stale = False
                acc.client = client
                acc.status = "在线"
        if stale:
            client.disconnect()
            return
        self._log("system", f"[{name}] ✅ 已进入服务器", name)
        if bool(self.config.get("auto_login", True)) and acc.password:
            threading.Thread(
                target=self._auto_login, args=(name, client, acc.password),
                daemon=True).start()

    def _auto_login(self, name: str, client: McBotClient, password: str) -> None:
        time.sleep(3)
        if client.running:
            try:
                client.send_command(f"/login {password}")
                self._log("system", f"[{name}] 🔏 已发送 /login ******", name)
            except Exception as exc:
                self._log("system", f"[{name}] 自动登录失败：{exc}", name)

    def _on_disconnect(self, name: str, reason: str) -> None:
        self.teleport.cancel(name, "连接已断开")
        with self._lock:
            acc = self._by_name.get(name)
            if acc is None:
                return
            acc.client = None
            manual = acc.manual_stop
            acc.status = "离线" if manual else "断开"
            token = acc.token
        if not manual:
            self._log("system", f"[{name}] ⛔ 已断开：{reason}", name)
            self._maybe_reconnect(name, token)

    def _maybe_reconnect(self, name: str, token: int) -> None:
        with self._lock:
            acc = self._by_name.get(name)
            enabled = bool(self.config.get("auto_reconnect", True))
        if not acc or not enabled or acc.manual_stop:
            return
        self._log("system", f"[{name}] 🔄 5 秒后自动重连…", name)

        def retry() -> None:
            with self._lock:
                cur = self._by_name.get(name)
                if (cur is None or cur.manual_stop or token != cur.token
                        or cur.status == "在线"):
                    return
            threading.Thread(target=self._connect_one, args=(name,), daemon=True).start()

        threading.Timer(5.0, retry).start()

    def disconnect(self, names: List[str], quiet: bool = False) -> Dict[str, Any]:
        with self._lock:
            targets = [self._by_name[n] for n in names if n in self._by_name]
        for acc in targets:
            acc.manual_stop = True
            acc.token += 1
            self.teleport.cancel(acc.name, "账号已手动下线")
            client = acc.client
            acc.client = None
            acc.status = "离线"
            if client is not None:
                try:
                    client.disconnect()
                    client.join(timeout=1.5)
                except Exception:
                    pass
            if not quiet:
                self._log("system", f"[{acc.name}] ⛔ 手动下线")
        return {"ok": True}

    # ---- 聊天与命令 ----

    def _on_chat(self, name: str, text: str) -> None:
        self._log("chat", text, name)
        # 接受/拒绝传送邀请的系统消息也要经过传送流程（线程安全）
        self.teleport.on_chat(name, text)
        # 自己发出的私聊（“你悄悄对 xxx 说”）不算接收
        if text.lstrip().startswith("你悄悄对"):
            return
        parsed = parse_private_chat(text, name)
        if not parsed:
            return
        sender, content = parsed
        self._log("system", f"[{name}] 📩 私聊来自 {sender}：{content}", name)
        if self._handle_key_command(name, sender, content):
            return
        with self._lock:
            acc = self._by_name.get(name)
            client = acc.client if acc else None
        if client is None or not client.running:
            return
        if self.teleport.handle(
                name, sender, content, self.auto_reply.masters,
                send_command=lambda command, n=name: self._send_command_to(
                    n, command),
                reply=lambda message, log_text="": self._reply_private(
                    name, sender, message, log_text),
                log=lambda message: self._log(
                    "system", f"[{name}] {message}", name)):
            return
        engine = self.auto_reply
        if not engine.enabled:
            return
        for rule in engine.match(sender, content):
            reply = engine.render(rule.reply, sender, content)
            command = engine.render(rule.command, sender, content)
            try:
                if reply:
                    client.send_chat(reply)
                    self._log("system", f"[{name}] 💬 自动回复 {sender}：{reply}", name)
                if command:
                    client.send_command(command)
                    self._log("system", f"[{name}] ⚙️ 自动执行：{command}", name)
            except Exception as exc:
                self._log("system", f"[{name}] 自动回复失败：{exc}", name)

    # ---- 游戏内密钥命令（仅主人名单） ----

    def _reply_private(self, name: str, sender: str, message: str,
                       log_text: str = "") -> None:
        """以私聊方式回复（/tell），避免把密钥发到公屏。"""
        with self._lock:
            acc = self._by_name.get(name)
            client = acc.client if acc else None
        if client is None or not client.running:
            return
        try:
            client.send_command(f"tell {sender} {message}")
            self._log(
                "system",
                f"[{name}] 🔏 私聊回复 {sender}：{log_text or message}", name)
        except Exception as exc:
            self._log("system", f"[{name}] 私聊回复失败：{exc}", name)

    def _handle_key_command(self, name: str, sender: str, content: str) -> bool:
        """处理 get / xy 命令。返回是否已消费该消息。"""
        return self.key_commands.handle(
            sender,
            content,
            self.auto_reply.masters,
            reply=lambda text, log_text="": self._reply_private(
                name, sender, text, log_text),
            log=lambda text: self._log("system", f"[{name}] {text}", name),
        )

    def _send_command_to(self, name: str, command: str) -> None:
        """按账号名取当前客户端发送命令；定时器线程也会调用，失败抛异常。"""
        with self._lock:
            acc = self._by_name.get(name)
            client = acc.client if acc else None
        if client is None or not client.running:
            raise RuntimeError("账号未在线")
        client.send_command(command)

    def send_command(self, text: str, names: List[str]) -> Dict[str, Any]:
        text = (text or "").strip()
        if not text:
            return {"error": "内容不能为空"}
        with self._lock:
            if names:
                targets = [self._by_name[n] for n in names if n in self._by_name]
            else:
                targets = list(self._accounts)
            clients = [(a.name, a.client) for a in targets
                       if a.client is not None and a.client.running]
        sent = 0
        for name, client in clients:
            try:
                if text.startswith("/"):
                    client.send_command(text)
                else:
                    client.send_chat(text)
                sent += 1
            except Exception as exc:
                self._log("system", f"[{name}] 发送失败：{exc}", name)
        if sent:
            self._log("system", f"[系统] 已发送给 {sent} 个账号：{text}")
            return {"ok": True, "sent": sent}
        return {"error": "没有在线的目标账号"}

    # ---- 测速 ----

    def ping(self, addr: str = "") -> Dict[str, Any]:
        return ping_target(addr or self.config.get("addr", ""))

    @staticmethod
    def _parse_addr(addr: str) -> tuple:
        return parse_addr(addr)

    # ---- 配置更新 ----

    def update_config(self, data: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            if "addr" in data:
                self.config["addr"] = str(data["addr"]).strip()
            if "gap" in data:
                try:
                    self.config["gap"] = max(1, min(300, int(data["gap"])))
                except (TypeError, ValueError):
                    pass
            for key in ("auto_reconnect", "auto_login", "auto_respawn"):
                if key in data:
                    self.config[key] = bool(data[key])
            if "lines" in data and isinstance(data["lines"], dict):
                for k, v in data["lines"].items():
                    self.config["lines"][str(k)] = str(v)
            if "current_line" in data:
                try:
                    line = int(data["current_line"])
                except (TypeError, ValueError):
                    line = 1
                self.config["current_line"] = max(1, min(4, line))
        self.save_config()
        return {"ok": True}

    def update_auto_reply(self, data: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            self.auto_reply.load(data or {})
        self.save_config()
        self._log("system", f"[系统] 自动回复已{'启用' if self.auto_reply.enabled else '停用'}")
        return {"ok": True}

    # ---- 生命周期 ----

    def shutdown(self) -> None:
        with self._lock:
            names = [a.name for a in self._accounts]
        self.disconnect(names, quiet=True)
        self.save_config()


# ---------------------------------------------------------------- HTTP


class Handler(BaseHTTPRequestHandler):
    manager: BotManager = None  # type: ignore[assignment]
    server_version = "McBotWeb/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:  # 安静：控制台只留业务日志
        pass

    # -- 响应 --

    def _send(self, code: int, body: bytes, ctype: str,
              headers: Optional[List[tuple]] = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for name, value in headers or []:
            self.send_header(name, value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (ConnectionError, BrokenPipeError):
            pass

    def _json(self, obj: Any, code: int = 200,
              headers: Optional[List[tuple]] = None) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._send(code, body, "application/json; charset=utf-8", headers)

    def _redirect(self, location: str, code: int = 302) -> None:
        self._send(code, b"", "text/plain; charset=utf-8",
                   [("Location", location)])

    def _body(self) -> Dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > 2_000_000:
            return {}
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    # -- 登录态 --

    def _cookie_token(self) -> str:
        raw = self.headers.get("Cookie") or ""
        if not raw:
            return ""
        try:
            jar = SimpleCookie()
            jar.load(raw)
        except Exception:
            return ""
        morsel = jar.get(SESSION_COOKIE)
        return morsel.value if morsel else ""

    def _session_key(self) -> str:
        """返回会话对应的有效密钥；无会话/密钥失效返回空串。"""
        token = self._cookie_token()
        if not token:
            return ""
        with SESSIONS_LOCK:
            key = SESSIONS.get(token, "")
        if key and webauth.validate(key):
            return key
        if token:
            with SESSIONS_LOCK:
                SESSIONS.pop(token, None)
        return ""

    def _require_auth(self) -> bool:
        if self._session_key():
            return True
        self._json({"error": "未登录或密钥已过期"}, 401)
        return False

    @staticmethod
    def _session_cookie(token: str, max_age: int = SESSION_MAX_AGE) -> tuple:
        return (
            "Set-Cookie",
            f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax; "
            f"Max-Age={max_age}",
        )

    # -- 路由 --

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        mgr = self.manager

        if path == "/api/health":
            self._json({"ok": True, "time": time.time()})
            return
        if path == "/api/auth":
            authed = bool(self._session_key())
            self._json({"authed": authed,
                        "expires_in": webauth.remaining() if authed else 0})
            return
        if path == "/api/state":
            if not self._require_auth():
                return
            try:
                since = int((query.get("since") or ["0"])[0])
            except ValueError:
                since = 0
            payload = mgr.snapshot(since)
            payload["expires_in"] = webauth.remaining()
            self._json(payload)
            return
        if path == "/api/ping":
            if not self._require_auth():
                return
            addr = (query.get("addr") or [""])[0]
            self._json(mgr.ping(addr))
            return
        if path == "/login":
            if self._session_key():
                self._redirect("/")
                return
            self._serve_static("/login.html")
            return
        if path in ("", "/"):
            if not self._session_key():
                self._redirect("/login")
                return
            self._serve_static("/index.html")
            return
        self._serve_static(path)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        mgr = self.manager
        data = self._body()

        if path == "/api/login":
            key = str(data.get("key", "")).strip()
            record = webauth.current()
            if not record or not webauth.validate(key):
                self._json({"error": "密钥无效或已过期"}, 401)
                return
            token = secrets.token_hex(16)
            with SESSIONS_LOCK:
                SESSIONS[token] = record["key"]
            self._json(
                {"ok": True, "expires_in": webauth.remaining()},
                headers=[self._session_cookie(token)])
            return

        if not self._require_auth():
            return

        if path == "/api/logout":
            token = self._cookie_token()
            with SESSIONS_LOCK:
                SESSIONS.pop(token, None)
            self._json({"ok": True},
                       headers=[self._session_cookie("", max_age=0)])
            return
        if path == "/api/renew":
            key = self._session_key()
            record = webauth.renew(key)
            if not record:
                self._json({"error": "密钥已失效，请重新登录"}, 401)
                return
            self._json({"ok": True, "expires_in": webauth.remaining(),
                        "expires_at": webauth.expiry_text(record)})
            return

        if path == "/api/accounts":
            self._json(mgr.add_account(
                str(data.get("name", "")), str(data.get("password", ""))))
        elif path == "/api/connect":
            names = [str(n) for n in (data.get("names") or [])]
            self._json(mgr.connect_many(names))
        elif path == "/api/disconnect":
            names = [str(n) for n in (data.get("names") or [])]
            self._json(mgr.disconnect(names))
        elif path == "/api/command":
            names = [str(n) for n in (data.get("names") or [])]
            self._json(mgr.send_command(str(data.get("text", "")), names))
        elif path == "/api/config":
            self._json(mgr.update_config(data))
        elif path == "/api/auto-reply":
            self._json(mgr.update_auto_reply(data))
        else:
            self._json({"error": "未知接口"}, 404)

    def do_DELETE(self) -> None:  # noqa: N802
        if not self._require_auth():
            return
        parsed = urlparse(self.path)
        if parsed.path != "/api/accounts":
            self._json({"error": "未知接口"}, 404)
            return
        query = parse_qs(parsed.query)
        names = [unquote(n) for n in (query.get("name") or [])]
        if not names:
            data = self._body()
            names = [str(n) for n in (data.get("names") or [])]
        self._json(self.manager.remove_accounts(names))

    # -- 静态文件 --

    def _serve_static(self, path: str) -> None:
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        if rel.startswith("api/"):
            self._json({"error": "未知接口"}, 404)
            return
        if rel == "index.html" and not self._session_key():
            self._redirect("/login")
            return
        target = (WEB_DIR / rel).resolve()
        try:
            target.relative_to(WEB_DIR.resolve())
        except ValueError:
            self._json({"error": "禁止访问"}, 403)
            return
        if not target.is_file():
            self._json({"error": "文件不存在"}, 404)
            return
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in (
                "application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        self._send(200, target.read_bytes(), ctype)


# ---------------------------------------------------------------- 入口


def create_server(host: str, port: int, manager: Any = None) -> ThreadingHTTPServer:
    """创建（未启动的）HTTP 服务；manager 为空时新建独立的 BotManager。"""
    Handler.manager = manager if manager is not None else BotManager()
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    return httpd


def start(host: str = "0.0.0.0", port: int = 6099,
          manager: Any = None) -> tuple:
    """在后台线程启动 Web 控制台，返回 (httpd, url)。

    桌面端同启时传入 DesktopWebManager，让浏览器操作与桌面端共享同一批
    机器人；独立运行（cli）时省略 manager 即使用自带 BotManager。
    """
    httpd = create_server(host, port, manager)
    threading.Thread(target=httpd.serve_forever, name="mcbot-web",
                     daemon=True).start()
    return httpd, _url(host, httpd.server_address[1])


def stop(httpd: ThreadingHTTPServer) -> None:
    """停止后台 Web 服务（不触碰账号/配置，由调用方决定如何收尾）。"""
    try:
        httpd.shutdown()
    finally:
        httpd.server_close()


def _url(host: str, port: int) -> str:
    shown = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    return f"http://{shown}:{port}/"


def main() -> None:
    parser = argparse.ArgumentParser(description="McBot Web 控制台")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址（默认 0.0.0.0）")
    parser.add_argument("--port", type=int, default=6099, help="监听端口（默认 6099）")
    args = parser.parse_args()

    manager = BotManager()
    try:
        httpd = create_server(args.host, args.port, manager)
    except OSError as exc:
        print(f"[错误] 无法监听 {args.host}:{args.port}：{exc}")
        raise SystemExit(1) from exc
    print(f"[McBot Web] {_url(args.host, args.port)}"
          f"  (监听 {args.host}:{args.port})", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[McBot Web] 正在关闭…")
    finally:
        manager.shutdown()
        httpd.server_close()


if __name__ == "__main__":
    main()
