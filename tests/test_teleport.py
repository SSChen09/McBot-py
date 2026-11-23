"""私聊 sethome / goto 传送流程测试（不连接服务器）。"""
from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcbot.teleport import HomeStore, TeleportService  # noqa: E402


class Recorder:
    def __init__(self) -> None:
        self.commands = []
        self.replies = []
        self.logs = []

    def send(self, command: str) -> None:
        self.commands.append(command)

    def reply(self, text: str, log_text: str = "") -> None:
        self.replies.append(text)

    def log(self, text: str) -> None:
        self.logs.append(text)


class TeleportServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = HomeStore(Path(self._tmp.name) / "home.json")
        self.services = []

    def tearDown(self) -> None:
        for service in self.services:
            service.shutdown()
        self._tmp.cleanup()

    def make_service(self, **kwargs) -> TeleportService:
        defaults = dict(
            timeout_tpa=2.0, timeout_home=2.0, timeout_accept=2.0,
            timeout_saving=0.05, return_delay=0.05, cooldown=0.0)
        defaults.update(kwargs)
        service = TeleportService(self.store, **defaults)
        self.services.append(service)
        return service

    def wait_until(self, predicate, timeout: float = 2.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.01)
        return predicate()

    def test_sethome_then_goto_round_trip(self) -> None:
        recorder = Recorder()
        service = self.make_service()
        self.assertTrue(service.handle(
            "Bot", "Steve", "sethome 家", ["Steve"],
            recorder.send, recorder.reply, recorder.log))
        self.assertEqual(recorder.commands, ["tpa Steve"])
        self.assertEqual(self.store.slot_for("Bot", "家"), 1)

        service.on_teleport("Bot", 10.0, 64.0, 10.0)
        self.assertEqual(recorder.commands, ["tpa Steve", "sethome 家"])
        self.assertTrue(self.wait_until(
            lambda: "home home" in recorder.commands))
        self.assertTrue(any("已保存" in text for text in recorder.replies))

        goto = Recorder()
        self.assertTrue(service.handle(
            "Bot", "Steve", "goto 家", ["Steve"],
            goto.send, goto.reply, goto.log))
        self.assertEqual(goto.commands, ["home 家"])

        service.on_teleport("Bot", 10.0, 64.0, 10.0)
        self.assertEqual(goto.commands, ["home 家", "tpahere Steve"])
        service.on_chat("Bot", "Steve 已接受你的传送请求。")
        self.assertTrue(self.wait_until(
            lambda: goto.commands.count("home home") == 1))
        self.assertTrue(any("已收到你的回应" in text for text in goto.replies))

    def test_goto_unknown_name(self) -> None:
        recorder = Recorder()
        service = self.make_service()
        self.assertTrue(service.handle(
            "Bot", "Steve", "goto 不存在", ["Steve"],
            recorder.send, recorder.reply, recorder.log))
        self.assertEqual(recorder.commands, [])
        self.assertTrue(any("没有名为" in text for text in recorder.replies))

    def test_non_master_ignored(self) -> None:
        recorder = Recorder()
        service = self.make_service()
        self.assertFalse(service.handle(
            "Bot", "Alex", "sethome 家", ["Steve"],
            recorder.send, recorder.reply, recorder.log))
        self.assertEqual(recorder.commands, [])
        self.assertEqual(recorder.replies, [])

    def test_empty_masters_logged(self) -> None:
        recorder = Recorder()
        service = self.make_service()
        self.assertFalse(service.handle(
            "Bot", "Steve", "sethome 家", [],
            recorder.send, recorder.reply, recorder.log))
        self.assertTrue(any("未配置主人名单" in text for text in recorder.logs))

    def test_sethome_timeout(self) -> None:
        recorder = Recorder()
        service = self.make_service(timeout_tpa=0.05)
        service.handle(
            "Bot", "Steve", "sethome 家", ["Steve"],
            recorder.send, recorder.reply, recorder.log)
        self.assertTrue(self.wait_until(
            lambda: any("已取消" in text for text in recorder.replies)))
        self.assertEqual(recorder.commands, ["tpa Steve"])

    def test_goto_accept_timeout_returns_home(self) -> None:
        self.store.assign("Bot", "家")
        recorder = Recorder()
        service = self.make_service(timeout_accept=0.05)
        service.handle(
            "Bot", "Steve", "goto 家", ["Steve"],
            recorder.send, recorder.reply, recorder.log)
        service.on_teleport("Bot", 1.0, 2.0, 3.0)
        self.assertTrue(self.wait_until(
            lambda: "home home" in recorder.commands))
        self.assertTrue(any("超时" in text for text in recorder.replies))

    def test_tpa_prompt_is_not_acceptance(self) -> None:
        self.store.assign("Bot", "家")
        recorder = Recorder()
        service = self.make_service(timeout_accept=5.0)
        service.handle(
            "Bot", "Steve", "goto 家", ["Steve"],
            recorder.send, recorder.reply, recorder.log)
        service.on_teleport("Bot", 1.0, 2.0, 3.0)
        service.on_chat("Bot", "若想接受传送，输入/tpaccept。")
        service.on_chat("Bot", "Steve请求传送到你这里。")
        self.assertNotIn("home home", recorder.commands)

    def test_self_echo_is_not_acceptance(self) -> None:
        self.store.assign("Bot", "家")
        recorder = Recorder()
        service = self.make_service(timeout_accept=5.0)
        service.handle(
            "Bot", "Steve", "goto 家", ["Steve"],
            recorder.send, recorder.reply, recorder.log)
        service.on_teleport("Bot", 1.0, 2.0, 3.0)
        service.on_chat("Bot", "[我 -> Steve] 已收到你的回应，机器人正在返回出生点。")
        service.on_chat("Bot", "你悄悄对 Steve 说：已收到你的回应")
        self.assertNotIn("home home", recorder.commands)

    def test_unrelated_accept_message_is_ignored(self) -> None:
        self.store.assign("Bot", "家")
        recorder = Recorder()
        service = self.make_service(timeout_accept=5.0)
        service.handle(
            "Bot", "Steve", "goto 家", ["Steve"],
            recorder.send, recorder.reply, recorder.log)
        service.on_teleport("Bot", 1.0, 2.0, 3.0)
        service.on_chat("Bot", "Alex 接受了 Bob 的传送请求。")
        self.assertNotIn("home home", recorder.commands)

    def test_deny_returns_home(self) -> None:
        self.store.assign("Bot", "家")
        recorder = Recorder()
        service = self.make_service(timeout_accept=5.0)
        service.handle(
            "Bot", "Steve", "goto 家", ["Steve"],
            recorder.send, recorder.reply, recorder.log)
        service.on_teleport("Bot", 1.0, 2.0, 3.0)
        service.on_chat("Bot", "Steve 拒绝了你的传送请求。")
        self.assertTrue(self.wait_until(
            lambda: "home home" in recorder.commands))
        self.assertTrue(any("被拒绝" in text for text in recorder.replies))

    def test_name_lookup_is_case_insensitive_and_persisted(self) -> None:
        first = self.store.assign("Bot", "Home")
        second = self.store.assign("Bot", "home")
        self.assertEqual(first, second)
        reopened = HomeStore(self.store.path)
        self.assertEqual(reopened.slot_for("Bot", "HOME"), first)

    def test_accounts_have_separate_mappings(self) -> None:
        self.assertEqual(self.store.assign("Bot", "家"), 1)
        self.assertEqual(self.store.assign("Bot", "矿洞"), 2)
        self.assertEqual(self.store.assign("Alt", "家"), 1)
        self.assertEqual(self.store.assign("Alt", "矿洞"), 2)
        self.assertEqual(self.store.assign("Alt", "农场"), 3)

        reopened = HomeStore(self.store.path)
        self.assertEqual(reopened.snapshot("Bot"), {"家": 1, "矿洞": 2})
        self.assertEqual(reopened.snapshot("Alt"), {"家": 1, "矿洞": 2, "农场": 3})
        self.assertEqual(reopened.slot_for("Bot", "农场"), None)
        self.assertEqual(reopened.slot_for("ALT", "家"), 1)

    def test_sethome_invalid_name_is_reported(self) -> None:
        recorder = Recorder()
        service = self.make_service(timeout_saving=5.0)
        service.handle(
            "Bot", "Steve", "sethome 测试", ["Steve"],
            recorder.send, recorder.reply, recorder.log)
        service.on_teleport("Bot", 1.0, 2.0, 3.0)
        self.assertEqual(recorder.commands, ["tpa Steve", "sethome 测试"])
        service.on_chat("Bot", "错误：无效的家名称！")
        self.assertTrue(any("无效的家名称" in text for text in recorder.replies))
        self.assertFalse(any("已保存" in text for text in recorder.replies))
        self.assertTrue(self.wait_until(
            lambda: "home home" in recorder.commands))

    def test_sethome_success_message_confirms_early(self) -> None:
        recorder = Recorder()
        service = self.make_service(timeout_saving=5.0)
        service.handle(
            "Bot", "Steve", "sethome 测试", ["Steve"],
            recorder.send, recorder.reply, recorder.log)
        service.on_teleport("Bot", 1.0, 2.0, 3.0)
        service.on_chat("Bot", "成功设置家「测试」。")
        self.assertTrue(any("已保存" in text for text in recorder.replies))
        self.assertTrue(self.wait_until(
            lambda: "home home" in recorder.commands))

    def test_name_with_space_rejected(self) -> None:
        recorder = Recorder()
        service = self.make_service()
        self.assertTrue(service.handle(
            "Bot", "Steve", "sethome 我 的 家", ["Steve"],
            recorder.send, recorder.reply, recorder.log))
        self.assertEqual(recorder.commands, [])
        self.assertTrue(any("空格" in text for text in recorder.replies))

    def test_goto_server_missing_home_reports(self) -> None:
        self.store.assign("Bot", "家")
        recorder = Recorder()
        service = self.make_service(timeout_home=5.0)
        service.handle(
            "Bot", "Steve", "goto 家", ["Steve"],
            recorder.send, recorder.reply, recorder.log)
        service.on_chat("Bot", "错误：家「家」不存在！")
        self.assertTrue(any("服务器上没有名为" in text for text in recorder.replies))
        self.assertTrue(self.wait_until(
            lambda: "home home" in recorder.commands))

    def test_cancel_stops_pending_job(self) -> None:
        recorder = Recorder()
        service = self.make_service(timeout_tpa=5.0)
        service.handle(
            "Bot", "Steve", "sethome 家", ["Steve"],
            recorder.send, recorder.reply, recorder.log)
        service.cancel("Bot", "测试取消")
        service.on_teleport("Bot", 1.0, 2.0, 3.0)
        self.assertEqual(recorder.commands, ["tpa Steve"])
        self.assertTrue(any("测试取消" in text for text in recorder.logs))


if __name__ == "__main__":
    unittest.main()
