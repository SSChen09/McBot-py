"""Web 控制台桥接：浏览器操作 → 桌面主窗口（同一进程、同一批机器人）。

桌面端与 Web 端同启时，Web 面板不另建账号/连接，而是把请求投递到 Qt 主
线程、直接操作 GUI 中已有的账号行。这样两端看到的状态完全一致，也不会
出现同一账号被两个管理器重复登录、互踢的情况。

线程模型：HTTP 请求运行在 ThreadingHTTPServer 的工作线程，所有触碰 Qt
控件/账号行的操作都通过 ``BlockingQueuedConnection`` 投递到 GUI 线程执
行并同步取回结果；测速是纯网络 I/O，留在工作线程，避免占用 GUI。
"""
from __future__ import annotations

import threading
from typing import Any, Callable, Dict, List

from PySide6.QtCore import QObject, Qt, Signal

from mcbot import webui


class _Invoker(QObject):
    """把可调用对象投递到创建它的线程（GUI 线程）执行。"""

    _call = Signal(object, object)

    def __init__(self) -> None:
        super().__init__()
        self._call.connect(self._run, Qt.ConnectionType.BlockingQueuedConnection)

    def _run(self, fn: Callable[[], Any], holder: Dict[str, Any]) -> None:
        try:
            holder["value"] = fn()
        except Exception as exc:  # noqa: BLE001 - 原样回传给 HTTP 线程
            holder["error"] = exc


class DesktopWebManager:
    """webui.Handler 使用的后端：全部状态来自 MainWindow。"""

    def __init__(self, window: Any) -> None:
        self._win = window
        self._invoker = _Invoker()
        # 必须在 GUI 线程构造：记下当前线程，避免同线程投递时自锁
        self._gui_tid = threading.get_ident()

    # ---- 线程投递 ----

    def _ui(self, fn: Callable[..., Any], *args: Any) -> Any:
        if threading.get_ident() == self._gui_tid:
            return fn(*args)  # 已在 GUI 线程，直接执行
        holder: Dict[str, Any] = {}
        self._invoker._call.emit(lambda: fn(*args), holder)
        if "error" in holder:
            raise holder["error"]
        return holder.get("value")

    # ---- 与 BotManager 同名接口 ----

    def snapshot(self, since: int = 0) -> Dict[str, Any]:
        return self._ui(self._win.web_snapshot, since)

    def ping(self, addr: str = "") -> Dict[str, Any]:
        if not addr:
            addr = self._ui(self._win.web_addr) or ""
        return webui.ping_target(addr)

    def add_account(self, name: str, password: str) -> Dict[str, Any]:
        return self._ui(self._win.web_add_account, name or "", password or "")

    def remove_accounts(self, names: List[str]) -> Dict[str, Any]:
        return self._ui(self._win.web_remove_accounts, list(names or []))

    def connect_many(self, names: List[str]) -> Dict[str, Any]:
        return self._ui(self._win.web_connect_many, list(names or []))

    def disconnect(self, names: List[str], quiet: bool = False) -> Dict[str, Any]:
        return self._ui(self._win.web_disconnect, list(names or []), quiet)

    def send_command(self, text: str, names: List[str]) -> Dict[str, Any]:
        return self._ui(self._win.web_send_command, text or "", list(names or []))

    def update_config(self, data: Dict[str, Any]) -> Dict[str, Any]:
        return self._ui(self._win.web_update_config, dict(data or {}))

    def update_auto_reply(self, data: Dict[str, Any]) -> Dict[str, Any]:
        return self._ui(self._win.web_update_auto_reply, dict(data or {}))

    def shutdown(self) -> None:
        """退出收尾由 MainWindow.closeEvent 负责（保存配置、断开账号）。"""
        return None
