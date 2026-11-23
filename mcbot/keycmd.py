"""游戏内私聊密钥命令（get / xy）的共享逻辑。

桌面端与 Web 服务都可能持有在线账号，两边收到的私聊都要能处理，
因此把解析、主人校验、冷却和回复文案集中在这里，避免行为不一致。
"""
from __future__ import annotations

import time
from typing import Callable, Dict, Iterable, Optional, Tuple

from . import webauth

GET = "get"
XY = "xy"
COOLDOWN_SECONDS = 2.0


def is_master(sender: str, masters: Iterable[str]) -> bool:
    """主人名单非空且大小写不敏感匹配时才允许游戏内命令。"""
    wanted = {str(m).strip().lower() for m in masters if str(m).strip()}
    return bool(wanted) and str(sender).strip().lower() in wanted


def parse_command(content: str) -> Optional[Tuple[str, str]]:
    """把私聊内容解析成 ('get', '') 或 ('xy', 密钥)；其它内容返回 None。"""
    text = (content or "").strip()
    low = text.lower()
    if low == GET or low.startswith(GET + " "):
        return GET, ""
    if low.startswith(XY + " "):
        return XY, text[3:].strip()
    return None


class KeyCommandHandler:
    """处理游戏内 get/xy，按发送者做冷却。"""

    def __init__(self, cooldown: float = COOLDOWN_SECONDS) -> None:
        self.cooldown = cooldown
        self._last: Dict[str, float] = {}

    def handle(
        self,
        sender: str,
        content: str,
        masters: Iterable[str],
        reply: Callable[[str, str], None],
        log: Optional[Callable[[str], None]] = None,
    ) -> bool:
        """返回是否消费了这条私聊。

        reply(text, log_text)：以私聊发送并记录日志。
        log(text)：仅本地日志（不会发到游戏）。
        """
        parsed = parse_command(content)
        if parsed is None:
            return False

        if not is_master(sender, masters):
            if not any(str(m).strip() for m in masters):
                if log:
                    log("未配置主人名单，游戏内 get/xy 已忽略")
                reply("未配置主人名单，无法通过游戏获取密钥。",
                      "未配置主人名单提示已通过私聊发送")
            return False

        now = time.monotonic()
        if now - self._last.get(sender.lower(), 0.0) < self.cooldown:
            return True
        self._last[sender.lower()] = now

        kind, key = parsed
        if kind == GET:
            record = webauth.current()
            if not record:
                reply("当前没有有效密钥，请先在桌面端生成（有效 10 分钟）。",
                      "无有效密钥提示已通过私聊发送")
                return True
            left = webauth.remaining(record)
            minutes, seconds = divmod(left, 60)
            reply(
                f"当前密钥：{record['key']}，剩余 {minutes} 分 {seconds} 秒。"
                f"私聊 xy {record['key']} 可续约 10 分钟。",
                "当前密钥已通过私聊发送",
            )
            return True

        record = webauth.renew(key)
        if record:
            reply(
                f"续约成功，密钥 {record['key']} 已延长 10 分钟"
                f"（至 {webauth.expiry_text(record)}）。",
                "续约结果已通过私聊发送",
            )
        else:
            reply("密钥无效或已过期，无法续约，请重新获取。",
                  "续约失败结果已通过私聊发送")
        return True
