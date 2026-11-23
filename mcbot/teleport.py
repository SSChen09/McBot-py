"""私聊传送点命令：sethome <名称> / goto <名称>。

sethome 流程：
  1. 私聊回复，确认开始保存；
  2. /tpa <发送者>，等待服务器把机器人传送到发送者身边；
  3. 到达后 /sethome <名称>，并检查服务器是否返回错误
     （序号只作为本地 home.json 里的编号，不能直接当作家名称：
     LiteTeleport 等插件会拒绝纯数字名称“无效的家名称”）；
  4. 确认没有报错后 /home home 回到出生点。

goto 流程：
  1. /home <名称>，等待机器人到达该传送点；
  2. /tpahere <发送者>，邀请发送者传送到机器人身边；
  3. 收到对方接受传送的提示后 /home home 回到出生点。

仅主人名单内的玩家可以触发；两条流程都以 /home home 收尾。
桌面端与 Web 端共用本模块，避免两边行为不一致。
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, Optional, Tuple

from .keycmd import is_master

BASE_DIR = Path(__file__).resolve().parent.parent
HOME_FILE = BASE_DIR / "home.json"

SET_HOME = "sethome"
GOTO = "goto"

MAX_NAME_LENGTH = 32
_COOLDOWN_SECONDS = 2.0

# 名称 → 序号的持久化使用模块级锁，桌面端与 Web 端同进程时也不会重复分配。
_STORE_LOCK = threading.RLock()

_COMMAND_RE = re.compile(r"^(sethome|goto)\s+(.+)$", re.IGNORECASE | re.DOTALL)

# 服务器在“别人请求传送到你这里”时给出的提示，不能当成对方接受了邀请。
_TPA_PROMPT_MARKERS = (
    "输入/tpaccept",
    "若想接受",
    "请求传送到你这里",
    "请求传送到你这",
    "想传送到你这里",
)


def parse_command(content: str) -> Optional[Tuple[str, str]]:
    """解析私聊内容，返回 ('sethome'|'goto', 名称)；不匹配返回 None。"""
    text = (content or "").strip()
    if text.startswith("/"):
        text = text[1:].lstrip()
    match = _COMMAND_RE.match(text)
    if not match:
        return None
    kind = match.group(1).lower()
    name = " ".join(match.group(2).split())
    return kind, name


def _is_self_echo(text: str, account: str) -> bool:
    """判断是否为机器人自己发出的聊天回显（不能用来判断对方接受）。"""
    stripped = text.lstrip()
    if stripped.startswith("你悄悄对"):
        return True
    low = stripped.lower()
    prefixes = ["[我 ->", "[我->"]
    if account:
        prefixes.append(f"[{account.lower()} ->")
        prefixes.append(f"[{account.lower()}->")
    return any(low.startswith(prefix) for prefix in prefixes)


def _mentions_teleport(text: str, low: str) -> bool:
    return "传送" in text or "teleport" in low or "tpa" in low


def _mentions_accept(text: str, low: str) -> bool:
    return "接受" in text or "accept" in low


def _mentions_deny(text: str, low: str) -> bool:
    return ("拒绝" in text or "denied" in low or "declined" in low
            or "refused" in low)


def _looks_accepted(text: str, sender: str, account: str) -> bool:
    if _is_self_echo(text, account):
        return False
    stripped = text.strip()
    low = stripped.lower()
    if not (_mentions_teleport(stripped, low) and _mentions_accept(stripped, low)):
        return False
    # 别人向机器人发起的 tpa 提示（含“接受”字样），不是对 goto 邀请的回应。
    if any(marker in stripped for marker in _TPA_PROMPT_MARKERS):
        return False
    # 机器人自己作为接受方时，服务器回显“已接受传送请求。”。
    if re.match(r"^(已|成功)?接受了?传送请求", stripped):
        return False
    sender = (sender or "").strip()
    if "你的" in stripped or "您的" in stripped or "你发送的" in stripped:
        return True
    if sender and sender.lower() in low:
        return True
    if "your" in low:
        return True
    return False


def _looks_denied(text: str, sender: str, account: str) -> bool:
    if _is_self_echo(text, account):
        return False
    stripped = text.strip()
    low = stripped.lower()
    if not _mentions_deny(stripped, low):
        return False
    if not _mentions_teleport(stripped, low) and "请求" not in stripped:
        return False
    sender = (sender or "").strip()
    if sender and sender.lower() in low:
        return True
    return ("你的" in stripped or "您的" in stripped or "你发送的" in stripped
            or "your" in low or "你" in stripped)


def _looks_home_set_ok(text: str, account: str) -> bool:
    """服务器对 /sethome 的成功回执。"""
    if _is_self_echo(text, account):
        return False
    stripped = text.strip()
    low = stripped.lower()
    if any(marker in stripped for marker in
           ("已设置", "设置成功", "成功设置", "已创建", "家已保存")):
        return True
    return "home" in low and ("set" in low or "created" in low)


def _looks_home_set_failed(text: str, account: str) -> bool:
    """服务器对 /sethome 的失败回执（无效名称、已存在、花费不足等）。"""
    if _is_self_echo(text, account):
        return False
    stripped = text.strip()
    low = stripped.lower()
    if "无效的家名称" in stripped or "家名称已存在" in stripped:
        return True
    return "错误" in stripped or "error" in low or "invalid" in low


def _looks_home_missing(text: str) -> bool:
    """服务器提示“家不存在”，说明本地记录与服务器不一致。"""
    low = text.lower()
    has_missing = ("不存在" in text or "does not exist" in low
                   or "doesn't exist" in low or "dont exist" in low)
    return has_missing and ("家" in text or "home" in low)


class HomeStore:
    """按账号保存“名称 → 序号”映射，持久化在独立于 config.json 的 home.json。

    文件结构：
        {"version": 1, "accounts": {"账号名": {"名称": 序号, ...}, ...}}
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path is not None else HOME_FILE
        self._accounts: Dict[str, Dict[str, int]] = {}
        self.reload()

    def reload(self) -> None:
        """从磁盘重新读取，便于桌面端与 Web 端共享同一份文件。"""
        with _STORE_LOCK:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return
            accounts = data.get("accounts") if isinstance(data, dict) else None
            if not isinstance(accounts, dict):
                return
            clean: Dict[str, Dict[str, int]] = {}
            for account, homes in accounts.items():
                acct = str(account).strip()
                if not acct or not isinstance(homes, dict):
                    continue
                clean_homes: Dict[str, int] = {}
                for name, slot in homes.items():
                    try:
                        slot_i = int(slot)
                    except (TypeError, ValueError):
                        continue
                    display = str(name).strip()
                    if display and slot_i > 0:
                        clean_homes[display] = slot_i
                clean[acct] = clean_homes
            self._accounts = clean

    def assign(self, account: str, name: str) -> int:
        """返回该账号下名称对应的序号；不存在时分配下一个正整数并写入磁盘。"""
        key = " ".join(str(name).split())
        acct = (str(account).strip() or "-")
        with _STORE_LOCK:
            self.reload()
            homes = self._homes_for(acct)
            for existing, slot in homes.items():
                if existing.casefold() == key.casefold():
                    return slot
            slot = max(homes.values(), default=0) + 1
            homes[key] = slot
            self._save()
            return slot

    def slot_for(self, account: str, name: str) -> Optional[int]:
        key = " ".join(str(name).split()).casefold()
        acct = (str(account).strip() or "-")
        with _STORE_LOCK:
            self.reload()
            for existing, homes in self._accounts.items():
                if existing.casefold() != acct.casefold():
                    continue
                for home_name, slot in homes.items():
                    if home_name.casefold() == key:
                        return slot
        return None

    def snapshot(self, account: Optional[str] = None) -> Dict[str, Dict[str, int]]:
        with _STORE_LOCK:
            self.reload()
            if account is None:
                return {acct: dict(homes) for acct, homes in self._accounts.items()}
            acct = (str(account).strip() or "-")
            for existing, homes in self._accounts.items():
                if existing.casefold() == acct.casefold():
                    return dict(homes)
            return {}

    def _homes_for(self, account: str) -> Dict[str, int]:
        """取账号条目；账号名大小写不同视为同一账号。"""
        for existing, homes in self._accounts.items():
            if existing.casefold() == account.casefold():
                return homes
        homes: Dict[str, int] = {}
        self._accounts[account] = homes
        return homes

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "accounts": {
                acct: dict(sorted(homes.items(), key=lambda kv: kv[1]))
                for acct, homes in sorted(self._accounts.items())
            },
        }
        fd, tmp_name = tempfile.mkstemp(
            prefix=".home-", suffix=".tmp", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, self.path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise


@dataclass
class _Job:
    kind: str
    sender: str
    name: str
    slot: int
    stage: str
    token: int
    send_command: Callable[[str], None]
    reply: Callable[[str, str], None]
    log: Callable[[str], None]
    timer: Optional[threading.Timer] = None


class TeleportService:
    """按账号维护一条传送流程的状态机，可被多个线程安全调用。"""

    def __init__(
        self,
        store: Optional[HomeStore] = None,
        *,
        timeout_tpa: float = 60.0,
        timeout_home: float = 30.0,
        timeout_accept: float = 90.0,
        timeout_saving: float = 3.0,
        return_delay: float = 1.0,
        cooldown: float = _COOLDOWN_SECONDS,
    ) -> None:
        self.store = store if store is not None else HomeStore()
        self.timeout_tpa = timeout_tpa
        self.timeout_home = timeout_home
        self.timeout_accept = timeout_accept
        self.timeout_saving = timeout_saving
        self.return_delay = return_delay
        self.cooldown = cooldown
        self._jobs: Dict[str, _Job] = {}
        self._last: Dict[str, float] = {}
        self._token = 0
        self._lock = threading.RLock()

    # ---- 私聊命令入口 ----

    def handle(
        self,
        account: str,
        sender: str,
        content: str,
        masters: Iterable[str],
        send_command: Callable[[str], None],
        reply: Optional[Callable[[str, str], None]] = None,
        log: Optional[Callable[[str], None]] = None,
    ) -> bool:
        """返回是否消费了这条私聊（与 KeyCommandHandler 约定一致）。"""
        parsed = parse_command(content)
        if parsed is None:
            return False

        def _reply(text: str, log_text: str = "") -> None:
            if reply:
                try:
                    reply(text, log_text or text)
                except Exception:
                    pass

        def _log(text: str) -> None:
            if log:
                try:
                    log(text)
                except Exception:
                    pass

        if not is_master(sender, masters):
            if not any(str(m).strip() for m in masters):
                _log("未配置主人名单，sethome/goto 已忽略")
            return False

        kind, name = parsed
        if not name:
            _reply("用法：sethome <名称> 或 goto <名称>", "用法提示已通过私聊发送")
            return True
        if len(name) > MAX_NAME_LENGTH:
            _reply(f"名称过长（最多 {MAX_NAME_LENGTH} 个字符）。",
                   "名称过长提示已通过私聊发送")
            return True
        if any(ch.isspace() for ch in name):
            _reply("名称不能包含空格，请换一个名称（例如 sethome 家）。",
                   "名称包含空格提示已通过私聊发送")
            return True

        sender = str(sender).strip()
        now = time.monotonic()
        if now - self._last.get(sender.lower(), 0.0) < self.cooldown:
            return True
        self._last[sender.lower()] = now

        with self._lock:
            if account in self._jobs:
                _reply("上一个传送流程还在进行中，请稍后再试。",
                       "传送流程占用提示已通过私聊发送")
                return True
            if kind == SET_HOME:
                try:
                    slot = self.store.assign(account, name)
                except OSError as exc:
                    _log(f"❌ 保存传送点名称失败：{exc}")
                    _reply("保存传送点失败，请查看日志。",
                           "保存传送点失败提示已通过私聊发送")
                    return True
                job = self._new_job(
                    account, SET_HOME, sender, name, slot, "wait_tpa",
                    send_command, reply, log)
                if not self._send(job, f"tpa {sender}"):
                    self._drop(account, job)
                    _reply("发送 /tpa 失败，请稍后再试。",
                           "tpa 发送失败提示已通过私聊发送")
                    return True
                _log(f"🧭 保存传送点「{name}」：序号 {slot}，已向 {sender} 发送 tpa")
                _reply(f"正在前往你的位置保存传送点「{name}」（序号 {slot}），"
                       f"请接受传送请求…",
                       f"已开始保存传送点「{name}」")
                self._arm(account, job, self.timeout_tpa, "wait_tpa")
                return True

            slot = self.store.slot_for(account, name)
            if slot is None:
                _log(f"goto 未找到传送点「{name}」")
                _reply(f"没有名为「{name}」的传送点，请先私聊 sethome {name} 保存。",
                       "未找到传送点提示已通过私聊发送")
                return True
            job = self._new_job(
                account, GOTO, sender, name, slot, "wait_home",
                send_command, reply, log)
            if not self._send(job, f"home {name}"):
                self._drop(account, job)
                _reply("发送 /home 失败，请稍后再试。",
                       "home 发送失败提示已通过私聊发送")
                return True
            _log(f"🧭 前往传送点「{name}」：序号 {slot}，已向 {sender} 发送 home {name}")
            _reply(f"正在前往传送点「{name}」（序号 {slot}）…",
                   f"已开始前往传送点「{name}」")
            self._arm(account, job, self.timeout_home, "wait_home")
            return True

    # ---- 客户端事件 ----

    def on_teleport(self, account: str, x: float, y: float, z: float) -> None:
        """客户端位置被服务器同步（/tpa 或 /home 生效）时调用。"""
        with self._lock:
            job = self._jobs.get(account)
            if job is None:
                return
            if job.kind == SET_HOME and job.stage == "wait_tpa":
                job.stage = "saving"
                self._cancel_timer(job)
                if not self._send(job, f"sethome {job.name}"):
                    self._drop(account, job)
                    job.reply("发送 /sethome 失败，本次保存已取消。",
                              "sethome 发送失败提示已通过私聊发送")
                    self._home_now(job)
                    return
                job.log(f"📍 已到达 {job.sender} 的位置，执行 sethome {job.name}")
                # 等服务器的回执：失败（如“无效的家名称”）时如实告知，
                # 成功或没有报错时才确认保存并返回出生点。
                self._arm(account, job, self.timeout_saving, "saving")
            elif job.kind == GOTO and job.stage == "wait_home":
                job.stage = "wait_accept"
                self._cancel_timer(job)
                if not self._send(job, f"tpahere {job.sender}"):
                    self._drop(account, job)
                    job.reply("发送 /tpahere 失败，机器人将返回出生点。",
                              "tpahere 发送失败提示已通过私聊发送")
                    self._home_now(job)
                    return
                job.log(f"📍 已到达传送点「{job.name}」，已向 {job.sender} 发送 tpahere")
                job.reply(
                    f"已到达传送点「{job.name}」（序号 {job.slot}），"
                    f"已向你发送传送邀请。",
                    f"已向 {job.sender} 发送传送邀请")
                self._arm(account, job, self.timeout_accept, "wait_accept")

    def on_chat(self, account: str, text: str) -> None:
        """所有聊天行都会经过这里，用于识别服务器回执与传送邀请的回应。"""
        with self._lock:
            job = self._jobs.get(account)
            if job is None:
                return
            if job.kind == SET_HOME and job.stage == "saving":
                if _looks_home_set_failed(text, account):
                    reason = " ".join(text.split())
                    self._drop(account, job)
                    job.log(f"❌ 保存传送点「{job.name}」失败：{reason}")
                    job.reply(f"保存传送点「{job.name}」失败：{reason}",
                              "保存失败提示已通过私聊发送")
                    self._home_now(job)
                    return
                if _looks_home_set_ok(text, account):
                    self._confirm_saved(account, job)
                return
            if job.kind != GOTO:
                return
            if job.stage == "wait_home":
                if _looks_home_missing(text):
                    self._drop(account, job)
                    job.log(f"❌ 服务器上不存在传送点「{job.name}」")
                    job.reply(f"服务器上没有名为「{job.name}」的传送点，"
                              f"请重新私聊 sethome {job.name} 保存。",
                              "服务器缺少传送点提示已通过私聊发送")
                    self._home_now(job)
                return
            if job.stage != "wait_accept":
                return
            if _looks_denied(text, job.sender, account):
                self._drop(account, job)
                job.log(f"🚫 {job.sender} 拒绝了传送邀请")
                job.reply("传送邀请被拒绝，机器人正在返回出生点。",
                          "拒绝结果已通过私聊发送")
                self._home_now(job)
                return
            if _looks_accepted(text, job.sender, account):
                job.stage = "return_home"
                self._cancel_timer(job)
                job.log(f"✅ {job.sender} 已接受传送邀请")
                job.reply("已收到你的回应，机器人正在返回出生点。",
                          "接受结果已通过私聊发送")
                self._arm(account, job, self.return_delay, "return_home")

    def cancel(self, account: str, reason: str = "") -> None:
        """账号断开或主动取消时清理流程（不发送任何游戏命令）。"""
        with self._lock:
            job = self._jobs.pop(account, None)
            if job is None:
                return
            self._cancel_timer(job)
            if reason:
                job.log(f"传送流程已取消：{reason}")

    def shutdown(self) -> None:
        """停止所有定时器（主要用于测试与进程退出）。"""
        with self._lock:
            for job in list(self._jobs.values()):
                self._cancel_timer(job)
            self._jobs.clear()

    # ---- 内部实现 ----

    def _new_job(
        self,
        account: str,
        kind: str,
        sender: str,
        name: str,
        slot: int,
        stage: str,
        send_command: Callable[[str], None],
        reply: Optional[Callable[[str, str], None]],
        log: Optional[Callable[[str], None]],
    ) -> _Job:
        self._token += 1
        job = _Job(
            kind=kind,
            sender=sender,
            name=name,
            slot=slot,
            stage=stage,
            token=self._token,
            send_command=send_command,
            reply=reply if reply is not None else (lambda text, log_text="": None),
            log=log if log is not None else (lambda text: None),
        )
        self._jobs[account] = job
        return job

    def _send(self, job: _Job, command: str) -> bool:
        try:
            job.send_command(command)
            return True
        except Exception as exc:
            job.log(f"❌ 发送 /{command} 失败：{exc}")
            return False

    def _home_now(self, job: _Job) -> None:
        if self._send(job, "home home"):
            job.log("🏠 已执行 home home 回到出生点")

    def _confirm_saved(self, account: str, job: _Job) -> None:
        """确认传送点已保存，并安排回出生点。"""
        job.stage = "return_home"
        self._cancel_timer(job)
        job.log(f"✅ 传送点「{job.name}」已保存（序号 {job.slot}）")
        job.reply(
            f"已到达你的位置，传送点「{job.name}」已保存（序号 {job.slot}），"
            f"正在返回出生点。",
            f"传送点「{job.name}」已保存为序号 {job.slot}")
        self._arm(account, job, self.return_delay, "return_home")

    def _arm(self, account: str, job: _Job, seconds: float, stage: str) -> None:
        self._cancel_timer(job)
        timer = threading.Timer(
            max(0.0, seconds), self._on_timeout, args=(account, job.token, stage))
        timer.daemon = True
        job.timer = timer
        timer.start()

    @staticmethod
    def _cancel_timer(job: _Job) -> None:
        if job.timer is not None:
            job.timer.cancel()
            job.timer = None

    def _drop(self, account: str, job: _Job) -> None:
        self._cancel_timer(job)
        if self._jobs.get(account) is job:
            self._jobs.pop(account, None)

    def _on_timeout(self, account: str, token: int, stage: str) -> None:
        with self._lock:
            job = self._jobs.get(account)
            if job is None or job.token != token or job.stage != stage:
                return
            self._cancel_timer(job)
            if stage == "return_home":
                self._drop(account, job)
                self._home_now(job)
                return
            if stage == "saving":
                self._confirm_saved(account, job)
                return
            if stage == "wait_tpa":
                self._drop(account, job)
                job.log(f"⌛ 等待 /tpa 生效超时（{job.name}）")
                job.reply(f"未等到传送确认，保存传送点「{job.name}」已取消。",
                          "保存传送点超时提示已通过私聊发送")
            elif stage == "wait_home":
                self._drop(account, job)
                job.log(f"⌛ 等待 /home 生效超时（{job.name}）")
                job.reply(f"未到达传送点「{job.name}」，本次传送已取消。",
                          "传送点到达超时提示已通过私聊发送")
                self._home_now(job)
            elif stage == "wait_accept":
                self._drop(account, job)
                job.log(f"⌛ 等待 {job.sender} 接受传送邀请超时")
                job.reply("等待回应超时，机器人将返回出生点。",
                          "等待接受超时提示已通过私聊发送")
                self._home_now(job)
