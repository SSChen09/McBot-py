"""Web 控制台访问密钥。

规则：
- 同一时间只有一个有效密钥，有效期 10 分钟；
- 密钥只能由桌面端生成；游戏内私聊 get 可取回当前密钥；
- 游戏内私聊 xy <密钥> 或在 Web 面板点“续约”可再延长 10 分钟；
- 密钥到期后需要重新在桌面端生成。

密钥保存在项目根目录的 web_auth.json，桌面端与 Web 服务两个进程共用。
"""
from __future__ import annotations

import json
import os
import secrets
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

BASE_DIR = Path(__file__).resolve().parent.parent      # McBot-py/
AUTH_FILE = BASE_DIR / "web_auth.json"

KEY_TTL = 10 * 60          # 秒
_KEY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # 去掉 I/O/0/1
_GROUPS = 3
_GROUP_LEN = 4

_lock = threading.RLock()


def _normalize(key: str) -> str:
    """忽略大小写、空格和连字符后比较。"""
    return "".join(ch for ch in str(key or "").upper() if ch.isalnum())


def _make_key() -> str:
    groups = []
    for _ in range(_GROUPS):
        groups.append("".join(secrets.choice(_KEY_ALPHABET) for _ in range(_GROUP_LEN)))
    return "-".join(groups)


def _write(record: Dict[str, Any]) -> None:
    payload = json.dumps(record, ensure_ascii=False, indent=2).encode("utf-8")
    AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(AUTH_FILE.parent), prefix=".web_auth.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, AUTH_FILE)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def read() -> Optional[Dict[str, Any]]:
    """读取密钥记录（不判断是否过期）。"""
    with _lock:
        if not AUTH_FILE.is_file():
            return None
        try:
            data = json.loads(AUTH_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or not data.get("key"):
            return None
        try:
            data["expires_at"] = float(data.get("expires_at", 0))
            data["created_at"] = float(data.get("created_at", 0))
        except (TypeError, ValueError):
            return None
        return data


def remaining(record: Optional[Dict[str, Any]] = None) -> int:
    """剩余有效秒数（向上取整，最小 0）。"""
    record = record or read()
    if not record:
        return 0
    left = float(record.get("expires_at", 0)) - time.time()
    return max(0, int(left + 0.999))


def current() -> Optional[Dict[str, Any]]:
    """返回仍有效的密钥记录；没有或已过期返回 None。"""
    record = read()
    if not record or remaining(record) <= 0:
        return None
    return record


def generate() -> Dict[str, Any]:
    """生成新密钥，旧密钥立即失效。"""
    now = time.time()
    record = {
        "key": _make_key(),
        "created_at": now,
        "expires_at": now + KEY_TTL,
        "ttl": KEY_TTL,
    }
    with _lock:
        _write(record)
    return record


def revoke() -> None:
    """撤销当前密钥。"""
    with _lock:
        try:
            AUTH_FILE.unlink()
        except FileNotFoundError:
            pass


def validate(key: str) -> bool:
    """校验密钥是否有效（密钥匹配且未过期）。"""
    record = current()
    if not record:
        return False
    return secrets.compare_digest(_normalize(record["key"]), _normalize(key))


def renew(key: str) -> Optional[Dict[str, Any]]:
    """把有效密钥再延长 10 分钟；密钥无效或已过期返回 None。"""
    with _lock:
        record = current()
        if not record:
            return None
        if not secrets.compare_digest(_normalize(record["key"]), _normalize(key)):
            return None
        record["expires_at"] = time.time() + KEY_TTL
        record["ttl"] = KEY_TTL
        _write(record)
        return record


def mask(key: str) -> str:
    """用于日志的脱敏形式：只保留前 4 位。"""
    key = str(key or "")
    return (key[:4] + "…") if len(key) > 5 else "…"


def expiry_text(record: Optional[Dict[str, Any]] = None) -> str:
    """本地时间 HH:MM:SS。"""
    record = record or read()
    if not record:
        return "—"
    return time.strftime("%H:%M:%S", time.localtime(float(record.get("expires_at", 0))))
