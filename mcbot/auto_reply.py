"""自动回复引擎：检测私聊消息，按规则回复或执行命令。

规则模型：
- trigger:    触发关键词（私聊内容包含即命中，大小写不敏感；为空则任何私聊都命中）
- reply:      回复文本（可含 {name} 占位=发送者，{msg}=原消息；可为空）
- command:    收到后由该账号执行的命令（可为空；自动补前导 /）
- owner_only: True 时只有主人发的私聊才会触发

权限控制：
- masters: 主人名单（任意账号名）。只有名单内的人发的私聊才能触发
  owner_only=True 的规则；普通规则对所有人生效。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional


@dataclass
class ReplyRule:
    """一条自动回复规则。"""

    trigger: str = ""          # 触发关键词（空=任意私聊）
    reply: str = ""            # 回复文本（空=不回复）
    command: str = ""          # 执行命令（空=不执行）
    owner_only: bool = False   # 仅主人可触发

    def to_dict(self) -> Dict:
        return {
            "trigger": self.trigger,
            "reply": self.reply,
            "command": self.command,
            "owner_only": self.owner_only,
        }

    @staticmethod
    def from_dict(d: Dict) -> "ReplyRule":
        return ReplyRule(
            trigger=str(d.get("trigger", "")),
            reply=str(d.get("reply", "")),
            command=str(d.get("command", "")),
            owner_only=bool(d.get("owner_only", False)),
        )


# ---- 私聊消息解析 ----------------------------------------------------------

# 常见私聊格式（按优先级尝试）：
#   [xxx -> 我] 内容     /  [我 -> xxx] 内容
#   xxx 悄悄对你说: 内容  /  你悄悄对 xxx 说: 内容
#   xxx -> 我 : 内容     /  From xxx: 内容
_PATTERNS = [
    # [发送者 -> 接收者] 内容
    re.compile(r"^\[(.+?)\s*->\s*(.+?)\]\s*(.+)$", re.S),
    # xxx 悄悄对(你说|我) / 你悄悄对 xxx 说
    re.compile(r"^(.+?)\s*悄悄对(?:你说|我)\s*[:：]?\s*(.+)$", re.S),
    re.compile(r"^你悄悄对\s*(.+?)\s*说\s*[:：]?\s*(.+)$", re.S),
    # From xxx: 内容 / [From: xxx] 内容
    re.compile(r"^(?:\[?From[:\s]+(.+?)\]?)\s*[:：]\s*(.+)$", re.I | re.S),
    #tellraw/ess 常见: xxx 对你说: 内容
    re.compile(r"^(.+?)\s*对你说\s*[:：]\s*(.+)$", re.S),
]


def parse_private_chat(text: str, my_name: str) -> Optional[tuple]:
    """从聊天文本解析私聊。返回 (发送者, 内容)；不是私聊返回 None。

    接收者不是自己（我发出去的私聊）也返回 None。
    """
    t = text.strip()
    for pat in _PATTERNS:
        m = pat.match(t)
        if not m:
            continue
        if m.lastindex and m.lastindex >= 3:
            sender, receiver, content = (
                m.group(1).strip(), m.group(2).strip(), m.group(3).strip())
        else:
            # 两分组模式：From xxx / xxx对你说
            sender, content = m.group(1).strip(), m.group(2).strip()
            receiver = "我"
        # 判断接收者是否是自己（我发出去的私聊忽略）
        is_to_me = (
            receiver in ("我", "you", "You", "me")
            or my_name.lower() in receiver.lower()
        )
        if not is_to_me:
            return None  # 自己发出去的私聊，忽略
        # 去掉发送者名字里的颜色码残留与装饰符号
        sender = re.sub(r"[§&][0-9a-fk-or]", "", sender, flags=re.I).strip()
        return (sender, content)
    return None


class AutoReplyEngine:
    """规则引擎：输入私聊消息 → 输出需要执行的动作列表。

    action 为 (类型, 内容)：("chat", 文本) 或 ("command", 命令)。
    """

    def __init__(self) -> None:
        self.rules: List[ReplyRule] = []
        self.masters: List[str] = []          # 主人名单
        self.enabled: bool = False            # 总开关
        self._cooldowns: Dict[str, float] = {}  # 触发键 → 上次时间（防刷屏）
        self.cooldown: float = 2.0            # 同一规则对同一人冷却秒数

    # -- 匹配 --

    def match(self, sender: str, content: str) -> List[ReplyRule]:
        """返回命中规则。owner_only 规则要求发送者在主人名单内。"""
        if not self.enabled:
            return []
        hits = []
        is_master = sender in self.masters
        now = time.time()
        for i, rule in enumerate(self.rules):
            if rule.owner_only and not is_master:
                continue
            trig = rule.trigger.strip()
            if trig and trig.lower() not in content.lower():
                continue
            # 冷却：同一规则 + 同一发送者
            key = f"{i}:{sender}"
            if now - self._cooldowns.get(key, 0) < self.cooldown:
                continue
            self._cooldowns[key] = now
            hits.append(rule)
        return hits

    @staticmethod
    def render(template: str, sender: str, content: str) -> str:
        """占位符替换：{name}=发送者, {msg}=消息内容。"""
        return template.replace("{name}", sender).replace("{msg}", content)

    # -- 持久化 --

    def to_dict(self) -> Dict:
        return {
            "enabled": self.enabled,
            "masters": list(self.masters),
            "rules": [r.to_dict() for r in self.rules],
        }

    def load(self, cfg: Dict) -> None:
        self.enabled = bool(cfg.get("enabled", False))
        self.masters = [str(m) for m in (cfg.get("masters") or [])]
        self.rules = [ReplyRule.from_dict(d) for d in (cfg.get("rules") or [])]
