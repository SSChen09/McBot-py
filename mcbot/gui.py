"""MC 多开机器人 —— Qt6 图形界面（PySide6），真实连接逻辑。

界面：顶栏状态、左侧账号列表（勾选/状态点/分类标签）、右侧操作参数 /
命令发送 / 运行选项 / 控制台输出，底部信息栏。

连接：每个账号对应一个 McBotClient（离线模式），上线所选按上号间隔
逐个连接；聊天实时进入控制台；掉线可自动重连；密码非空时连接成功后
自动发送 /login。

启动：默认同时启动桌面窗口与 Web 控制台（`web_bridge.DesktopWebManager`
把浏览器操作投递到本进程的账号，见 `webui.start`）；`--no-web` 可只开桌面端。
"""
import argparse
import base64
import json
import os
import platform
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

import psutil
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QLinearGradient,
    QPainter,
    QPalette,
    QPixmap,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStyle,
    QStyleOptionButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mcbot.client import McBotClient
from mcbot import webauth, webui
from mcbot.auto_reply import AutoReplyEngine, ReplyRule, parse_private_chat
from mcbot.keycmd import KeyCommandHandler
from mcbot.teleport import TeleportService
from mcbot.web_bridge import DesktopWebManager

from mcbot.theme import (
    AVATAR_COLORS,
    C_BG,
    C_BLUE,
    C_CYAN,
    C_DIM,
    C_GREEN,
    C_INPUT_BG,
    C_MUTED,
    C_PANEL,
    C_PANEL2,
    C_RED_FG,
    C_TEXT,
    C_YELLOW,
    STYLE,
)

# 状态 → 圆点颜色 / 文字颜色
STATUS_DOT = {
    "离线": "#6e7681",
    "连接中": C_YELLOW,
    "在线": C_GREEN,
    "断开": C_RED_FG,
}

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_FILE = LOG_DIR / "gui.log"
CHAT_DIR = LOG_DIR / "chats"   # 每个账号独立聊天记录目录
CONFIG_FILE = Path(__file__).resolve().parent.parent / "config.json"

LOG_MAX_BYTES = 16 * 1024 * 1024  # 日志文件上限：超过则舍弃最早的记录


def _rotate_log(path: Path) -> None:
    """日志超过上限时截去开头（最早的记录），保留末尾约一半继续写入。"""
    try:
        if path.stat().st_size <= LOG_MAX_BYTES:
            return
        data = path.read_bytes()
        keep = data[-(LOG_MAX_BYTES // 2):]
        nl = keep.find(b"\n")
        if nl >= 0:            # 丢弃第一个不完整行
            keep = keep[nl + 1:]
        path.write_bytes(keep)
    except OSError:
        pass


def make_dot(color: str, size: int = 10) -> QLabel:
    dot = QLabel()
    dot.setObjectName("dot")
    dot.setFixedSize(size, size)
    dot.setStyleSheet(f"QLabel#dot {{ background: {color}; border-radius: {size // 2}px; }}")
    return dot


def make_avatar(name: str, size: int = 36) -> QLabel:
    """按账号名生成首字母圆形头像，颜色由名字稳定散列。"""
    text = name[:1].upper() if name else "?"
    avatar = QLabel(text)
    avatar.setObjectName("avatar")
    avatar.setFixedSize(size, size)
    avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
    color = AVATAR_COLORS[sum(ord(c) for c in name) % len(AVATAR_COLORS)]
    avatar.setStyleSheet(
        f"QLabel#avatar {{ background: {color}; border-radius: {size // 2}px;"
        f" color: white; font-size: 15px; font-weight: 700; }}")
    return avatar


def make_vsep(height: int = 14) -> QFrame:
    """状态栏里的细竖线分隔符。"""
    line = QFrame()
    line.setObjectName("vsep")
    line.setFixedSize(1, height)
    return line


def make_app_icon(size: int = 64) -> QIcon:
    """用渐变圆角方块 + MC 字样生成窗口图标，避免额外图片资源。"""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    gradient = QLinearGradient(0, 0, size, size)
    gradient.setColorAt(0.0, QColor(C_BLUE))
    gradient.setColorAt(1.0, QColor("#8b5cf6"))
    painter.setBrush(gradient)
    painter.setPen(Qt.PenStyle.NoPen)
    radius = size * 0.28
    painter.drawRoundedRect(0, 0, size, size, radius, radius)
    painter.setPen(QColor("white"))
    font = QFont("Microsoft YaHei UI")
    font.setPixelSize(int(size * 0.34))
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "MC")
    painter.end()
    return QIcon(pixmap)


# 白色对勾 PNG（24x24，抗锯齿圆角线）：QSS 的 image 不支持 data URI，
# 且 PySide6 常缺 SVG 插件，故在子类 paintEvent 中直接绘制。
_CHECK_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAABgAAAAYCAYAAADgdz34AAAACXBIWXMAAA7EAAAOxAGVKw4b"
    "AAAA8ElEQVRIie3SsS5EQRQG4N0tiI5ELRFvwHoIjWdQaRQSQaEQJFtItlFItBqNZ5CoVquQ"
    "KDQ6Ue/qyKe5iXHcvZfsUMj+yVQz5/vn5k6jMc6/CtpY+i38yEe2cuPrPqeXE1/BayjYzYUv"
    "YhDwC7Ry4HN4Cvg1JquG2sVq1uDTuAv4PWaqhraTw91hJZjAVcCfMV/3yTdh6CSWoInzcO4F"
    "y5V4Mbzja07TH4aDsP+G1Vo8ud1xSckZWlgr2dv4Fh5KOiXQY3HbNN0f4aHksKQkzaVR3zr2"
    "h+A9TI2EJyV7AX/AbBY8KdlEH7dYyIqP8yd5B2n/7zDNteDzAAAAAElFTkSuQmCC"
)


class CheckableBox(QCheckBox):
    """选中态在蓝底上叠加白色对勾（普通复选框观感）的复选框。"""

    _check_pixmap: Optional[QPixmap] = None

    @classmethod
    def _pixmap(cls) -> QPixmap:
        if cls._check_pixmap is None or cls._check_pixmap.isNull():
            pm = QPixmap()
            pm.loadFromData(base64.b64decode(_CHECK_PNG_B64), "PNG")
            cls._check_pixmap = pm
        return cls._check_pixmap

    def paintEvent(self, event) -> None:  # noqa: N802（Qt 命名约定）
        super().paintEvent(event)
        if not self.isChecked():
            return
        opt = QStyleOptionButton()
        self.initStyleOption(opt)
        rect = self.style().subElementRect(
            QStyle.SubElement.SE_CheckBoxIndicator, opt, self)
        if rect.isValid():
            painter = QPainter(self)
            painter.drawPixmap(rect, self._pixmap())


class Bridge(QObject):
    """后台线程 → 主线程的信号桥（Qt 信号跨线程自动走队列连接）。"""

    log = Signal(str, str, str)  # (kind: system|chat, text, 账号名—聊天用)
    status = Signal(str, str)    # (账号, 状态)
    disconnected = Signal(str)   # 账号意外断开（主线程决定是否重连）
    private = Signal(str, str)   # (账号, 聊天原文) → 私聊检测/自动回复
    teleported = Signal(str, float, float, float)  # (账号, x, y, z) → 传送点流程
    chat = Signal(str, str)      # (账号, 聊天原文) → 传送点流程读取服务器回执


STATUS_TEXT = {
    "离线": "离线",
    "连接中": "正在连接…",
    "在线": "在线",
    "断开": "连接已断开",
}


class AccountRow(QFrame):
    """单个账号行：勾选框 + 头像 + 名称/状态 + 密码标签。"""

    def __init__(self, name: str, tag: str = "空", password: str = "",
                 on_change=None) -> None:
        super().__init__()
        self._name = name
        self.password = password
        self.status = "离线"
        self.client: Optional[McBotClient] = None
        self.manual_stop = False   # 主动下线标志（抑制自动重连）
        self._on_change = on_change
        self.setObjectName("row")
        # 固定行高，避免单个账号被垂直拉伸占满整个列表
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Fixed)
        if tag == "空":
            tag = "已设密码" if password else "无密码"

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 9, 12, 9)
        lay.setSpacing(10)

        self.check = CheckableBox()
        self.check.setChecked(True)
        self.check.stateChanged.connect(self._on_check)
        lay.addWidget(self.check)

        self.avatar = make_avatar(name)
        lay.addWidget(self.avatar)

        info = QVBoxLayout()
        info.setContentsMargins(0, 0, 0, 0)
        info.setSpacing(2)
        self.name_label = QLabel(name)
        self.name_label.setObjectName("name")
        info.addWidget(self.name_label)

        sub = QHBoxLayout()
        sub.setContentsMargins(0, 0, 0, 0)
        sub.setSpacing(6)
        self.dot = make_dot(STATUS_DOT["离线"], 7)
        sub.addWidget(self.dot)
        self.sub_label = QLabel(STATUS_TEXT["离线"])
        self.sub_label.setObjectName("rowSub")
        sub.addWidget(self.sub_label)
        sub.addStretch(1)
        info.addLayout(sub)
        lay.addLayout(info, 1)

        self.tag_label = QLabel(tag)
        self.tag_label.setObjectName("tag-gray" if tag == "无密码" else "tag")
        lay.addWidget(self.tag_label)

        self._refresh_style()

    @property
    def account_name(self) -> str:
        return self._name

    @property
    def online(self) -> bool:
        return self.status == "在线"

    def is_checked(self) -> bool:
        return self.check.isChecked()

    def set_checked(self, checked: bool) -> None:
        self.check.setChecked(checked)

    def set_status(self, status: str) -> None:
        self.status = status
        color = STATUS_DOT.get(status, "#6e7681")
        self.dot.setStyleSheet(
            f"QLabel#dot {{ background: {color}; border-radius: 3px; }}")
        self.sub_label.setText(STATUS_TEXT.get(status, status))
        self.sub_label.setStyleSheet(
            f"color: {color};" if status != "离线" else "")
        self.setToolTip(f"{self._name}：{status}")

    def _on_check(self) -> None:
        self._refresh_style()
        if self._on_change:
            self._on_change()

    def _refresh_style(self) -> None:
        self.setObjectName("row-checked" if self.check.isChecked() else "row")
        self.style().unpolish(self)
        self.style().polish(self)


class MainWindow(QWidget):
    """MC 多开机器人主窗口。"""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("root")
        self.setWindowTitle("MC 多开机器人")
        self.setWindowIcon(make_app_icon())
        self.resize(1400, 900)
        self.setMinimumSize(1120, 720)

        self.rows: List[AccountRow] = []
        self.row_index: Dict[str, AccountRow] = {}
        self.line_addresses: Dict[int, str] = {}   # 线路号(1-4) → 地址
        self.current_line = 2
        self._log_lines: List[tuple] = []          # (seq, kind, text, 时间戳, 账号)
        self._log_seq = 0                          # 单调递增日志序号（Web 增量拉取）
        self._console_mode = "console"             # console=全部 | monitor=聊天

        # 自动回复引擎（私聊检测 + 规则执行 + 主人权限）
        self.auto_reply = AutoReplyEngine()
        # 游戏内 get/xy 命令（与 Web 端共用同一套逻辑）
        self.key_commands = KeyCommandHandler()
        # 私聊 sethome/goto 传送点流程（与 Web 端共用同一套逻辑）
        self.teleport = TeleportService()

        # 状态栏：游戏状态 / 延迟 / 系统信息
        self._sys_version = platform.platform()
        self._latency_ms: Optional[int] = None   # 服务器延迟（状态 ping 测得）
        self._latency_busy = False
        self._latency_err = ""                    # 最近一次测速失败原因
        self._status_tick = 0
        self._net_prev: Optional[tuple] = None    # (monotonic 时间, rx, tx)
        self._net_down = 0.0                      # 下行 B/s
        self._net_up = 0.0                        # 上行 B/s

        # 后台线程信号桥
        self.bridge = Bridge()
        self.bridge.log.connect(self._on_log)
        self.bridge.status.connect(self._on_status)
        self.bridge.disconnected.connect(self._on_disconnected)
        self.bridge.private.connect(self._on_private)
        self.bridge.chat.connect(self.teleport.on_chat, Qt.QueuedConnection)
        self.bridge.teleported.connect(
            self.teleport.on_teleport, Qt.QueuedConnection)

        self._build_ui()
        self.setStyleSheet(STYLE)
        self._refresh_counters()
        self.load_config()

        # 状态栏定时刷新：游戏状态 / 系统信息 / 服务器延迟
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(500)
        self._status_timer.timeout.connect(self._refresh_status)
        self._status_timer.start()
        self._refresh_status()

    # ---- UI 搭建 ----
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 10)
        root.setSpacing(12)

        root.addWidget(self._build_header())

        # 左右两栏用可拖动分割条，自适应窗口大小
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(8)
        splitter.addWidget(self._build_account_panel())
        # 右栏：内容可滚动，底部命令条固定
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(10)
        rl.addWidget(self._build_right_scroll(), 1)
        rl.addWidget(self._build_cmdbar())
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 42)
        splitter.setStretchFactor(1, 58)
        splitter.setSizes([580, 780])
        root.addWidget(splitter, 1)

        root.addWidget(self._build_statusbar())

    def _build_right_scroll(self) -> QScrollArea:
        """右栏放进滚动区：窗口较矮时整栏滚动，避免卡片互相挤压。"""
        view = QScrollArea()
        view.setObjectName("rightScroll")
        view.setWidgetResizable(True)
        view.setFrameShape(QFrame.Shape.NoFrame)
        view.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        view.setWidget(self._build_right_panel())
        return view

    def _build_header(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("header")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 10, 14, 10)
        lay.setSpacing(12)

        brand = QLabel("MC")
        brand.setObjectName("brand")
        brand.setFixedSize(40, 40)
        brand.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(brand)

        titles = QVBoxLayout()
        titles.setContentsMargins(0, 0, 0, 0)
        titles.setSpacing(1)
        title = QLabel("MC 多开机器人")
        title.setObjectName("title")
        titles.addWidget(title)
        self.header_addr = QLabel("未连接服务器 · 离线模式")
        self.header_addr.setObjectName("subtitle")
        titles.addWidget(self.header_addr)
        lay.addLayout(titles)
        lay.addStretch(1)

        self.status_chip = QFrame()
        self.status_chip.setObjectName("chip")
        chip_lay = QHBoxLayout(self.status_chip)
        chip_lay.setContentsMargins(8, 3, 10, 3)
        chip_lay.setSpacing(6)
        self.header_dot = make_dot("#4c5a6d", 7)
        self.pill_online = QLabel("在线 0/0")
        self.pill_online.setObjectName("pillOnline")
        chip_lay.addWidget(self.header_dot)
        chip_lay.addWidget(self.pill_online)
        lay.addWidget(self.status_chip)

        lay.addSpacing(4)
        btn_stop = QPushButton("■ 停止")
        btn_stop.setObjectName("danger")
        btn_stop.setToolTip("断开所有在线账号")
        btn_stop.clicked.connect(self.stop_all)
        lay.addWidget(btn_stop)

        btn_refresh = QPushButton("↻ 刷新")
        btn_refresh.setToolTip("刷新账号、勾选与在线统计")
        btn_refresh.clicked.connect(self.refresh_status)
        lay.addWidget(btn_refresh)

        btn_web_key = QPushButton("Web 密钥")
        btn_web_key.setObjectName("ghost")
        btn_web_key.setToolTip("生成或查看 Web 控制台的登录密钥（有效 10 分钟）")
        btn_web_key.clicked.connect(self.show_web_key)
        lay.addWidget(btn_web_key)

        btn_save_cfg = QPushButton("保存配置")
        btn_save_cfg.setToolTip("把当前账号、线路与选项写入 config.json")
        btn_save_cfg.clicked.connect(self.save_config)
        lay.addWidget(btn_save_cfg)
        return bar

    def _build_account_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("panel")
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(8)
        title = QLabel("账号列表")
        title.setObjectName("cardTitle")
        head.addWidget(title)
        hint = QLabel("勾选后批量操作")
        hint.setObjectName("cardHint")
        head.addWidget(hint)
        head.addStretch(1)
        self.selected_label = QLabel("已选 0 / 0")
        self.selected_label.setObjectName("countChip")
        head.addWidget(self.selected_label)
        lay.addLayout(head)

        self.list_lay = QVBoxLayout()
        self.list_lay.setSpacing(6)
        self.list_lay.setContentsMargins(0, 0, 4, 0)
        self.empty_state = self._build_empty_state()
        self.list_lay.addWidget(self.empty_state)
        self.list_lay.addStretch(1)   # 行统一靠顶排列
        list_host = QWidget()
        list_host.setLayout(self.list_lay)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(list_host)
        lay.addWidget(scroll, 1)

        lay.addLayout(self._build_action_grid())
        return panel

    def _build_empty_state(self) -> QFrame:
        box = QFrame()
        box.setObjectName("empty")
        box.setMinimumHeight(230)
        v = QVBoxLayout(box)
        v.setContentsMargins(20, 24, 20, 24)
        v.setSpacing(6)
        v.addStretch(1)
        icon = QLabel("🎮")
        icon.setObjectName("emptyIcon")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(icon)
        title = QLabel("还没有账号")
        title.setObjectName("emptyTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(title)
        hint = QLabel("在右侧「账号与运行」里填写账号名并添加")
        hint.setObjectName("emptyHint")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(hint)
        v.addStretch(1)
        return box

    def _build_action_grid(self) -> QGridLayout:
        """两行四列的批量操作按钮，等宽铺满面板底部。"""
        grid = QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(6)
        specs = [
            ("全选", "ghost", self.select_all),
            ("反选", "ghost", self.invert_select),
            ("清空", "ghost", self.clear_select),
            ("▶ 上线所选", "primary", self.online_selected),
            ("下线所选", None, self.offline_selected),
            ("重置重连", None, self.reset_reconnect),
            ("删除所选", "danger", self.delete_selected),
            ("日志文件夹", "ghost", self.open_log_dir),
        ]
        for i, (text, obj, slot) in enumerate(specs):
            btn = QPushButton(text)
            if obj:
                btn.setObjectName(obj)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding,
                              QSizePolicy.Policy.Fixed)
            btn.clicked.connect(slot)
            grid.addWidget(btn, i // 4, i % 4)
        for col in range(4):
            grid.setColumnStretch(col, 1)
        return grid

    def _build_right_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("panel")
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        # --- 服务器线路 ---
        line_card = QFrame()
        line_card.setObjectName("card")
        lc = QVBoxLayout(line_card)
        lc.setContentsMargins(14, 10, 14, 10)
        lc.setSpacing(8)

        lh = QHBoxLayout()
        lh.setSpacing(8)
        lh.addWidget(self._card_title("服务器线路"))
        lh.addWidget(self._card_hint("4 条线路可保存，测速后再上线"))
        lh.addStretch(1)
        self.line_hint = QLabel("")
        self.line_hint.setObjectName("lineHint")
        lh.addWidget(self.line_hint)
        lc.addLayout(lh)

        line_row = QHBoxLayout()
        line_row.setSpacing(6)
        line_row.addWidget(self._form_label("线路"))
        self.line_buttons: List[QPushButton] = []
        for i in range(1, 5):
            btn = QPushButton(str(i))
            btn.setObjectName("line-btn")
            btn.setCheckable(True)
            btn.setFixedSize(30, 30)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, idx=i: self._select_line(idx))
            self.line_buttons.append(btn)
            line_row.addWidget(btn)
        self.input_addr = QLineEdit()
        self.input_addr.setPlaceholderText("服务器地址，如 mc.example.com:25565")
        self.input_addr.setMinimumWidth(140)
        line_row.addWidget(self.input_addr, 1)
        btn_ping = QPushButton("测速")
        btn_ping.setObjectName("primary")
        btn_ping.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_ping.clicked.connect(self.ping_line)
        line_row.addWidget(btn_ping)
        btn_save = QPushButton("保存线路")
        btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_save.clicked.connect(self.save_line)
        line_row.addWidget(btn_save)
        lc.addLayout(line_row)
        self.line_buttons[self.current_line - 1].setChecked(True)
        lay.addWidget(line_card)

        # --- 账号与运行 ---
        acct_card = QFrame()
        acct_card.setObjectName("card")
        ac = QVBoxLayout(acct_card)
        ac.setContentsMargins(14, 10, 14, 10)
        ac.setSpacing(8)

        ah = QHBoxLayout()
        ah.setSpacing(8)
        ah.addWidget(self._card_title("账号与运行"))
        ah.addWidget(self._card_hint("点选立即生效"))
        ah.addStretch(1)
        ac.addLayout(ah)

        add_row = QHBoxLayout()
        add_row.setSpacing(8)
        add_row.addWidget(self._form_label("账号"))
        self.input_name = QLineEdit()
        self.input_name.setPlaceholderText("账号名")
        self.input_name.setMinimumWidth(130)
        add_row.addWidget(self.input_name, 1)
        add_row.addWidget(self._form_label("密码"))
        self.input_pwd = QLineEdit()
        self.input_pwd.setPlaceholderText("密码（可留空）")
        self.input_pwd.setEchoMode(QLineEdit.Password)
        self.input_pwd.setMinimumWidth(130)
        add_row.addWidget(self.input_pwd, 1)
        btn_add = QPushButton("＋ 添加")
        btn_add.setObjectName("primary")
        btn_add.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_add.clicked.connect(self.add_account)
        add_row.addWidget(btn_add)
        ac.addLayout(add_row)

        ac.addWidget(self._divider())

        opt_row = QHBoxLayout()
        opt_row.setSpacing(8)
        opt_row.addWidget(self._form_label("运行选项"))
        self.cb_reconnect = CheckableBox("自动重连")
        self.cb_reconnect.setChecked(True)
        self.cb_reconnect.setToolTip("账号意外断开后，5 秒后自动重新连接")
        opt_row.addWidget(self.cb_reconnect)
        self.cb_login = CheckableBox("自动 /login")
        self.cb_login.setChecked(True)
        self.cb_login.setToolTip("进入服务器 3 秒后，用该账号密码自动发送 /login")
        opt_row.addWidget(self.cb_login)
        self.cb_respawn = CheckableBox("自动重生")
        self.cb_respawn.setChecked(True)
        self.cb_respawn.setToolTip("死亡后自动发送重生请求")
        self.cb_respawn.toggled.connect(self._on_respawn_toggle)
        opt_row.addWidget(self.cb_respawn)
        opt_row.addStretch(1)
        ac.addLayout(opt_row)

        gap_row = QHBoxLayout()
        gap_row.setSpacing(8)
        gap_row.addWidget(self._form_label("上号间隔"))
        self.spin_gap = QSpinBox()
        self.spin_gap.setRange(1, 300)
        self.spin_gap.setSuffix(" 秒")
        self.spin_gap.setFixedWidth(96)
        gap_row.addWidget(self.spin_gap)
        btn_gap = QPushButton("应用间隔")
        btn_gap.setObjectName("ghost")
        btn_gap.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_gap.clicked.connect(
            lambda: self.bridge.log.emit(
                "system", f"[系统] 上号间隔已设为 {self.spin_gap.value()} 秒", ""))
        gap_row.addWidget(btn_gap)
        hint = QLabel("依次连接，避免同时挤服")
        hint.setObjectName("cardHint")
        gap_row.addWidget(hint)
        gap_row.addStretch(1)
        ac.addLayout(gap_row)
        lay.addWidget(acct_card)

        # --- 自动回复 + 控制台（可拖动分割） ---
        vsplit = QSplitter(Qt.Orientation.Vertical)
        vsplit.setChildrenCollapsible(False)
        vsplit.setHandleWidth(8)
        vsplit.addWidget(self._build_auto_reply())
        vsplit.addWidget(self._build_console())
        vsplit.setStretchFactor(0, 3)
        vsplit.setStretchFactor(1, 2)
        vsplit.setSizes([480, 320])
        lay.addWidget(vsplit, 1)
        return panel

    def _build_cmdbar(self) -> QFrame:
        """命令发送条：固定在右栏底部，不随内容滚动。"""
        cmd_bar = QFrame()
        cmd_bar.setObjectName("cmdbar")
        cmd_row = QHBoxLayout(cmd_bar)
        cmd_row.setContentsMargins(10, 8, 10, 8)
        cmd_row.setSpacing(8)
        self.combo_cmd = QComboBox()
        self.combo_cmd.addItems(["发送到所选"])
        self.combo_cmd.setMinimumWidth(150)
        cmd_row.addWidget(self.combo_cmd)
        self.input_cmd = QLineEdit()
        self.input_cmd.setPlaceholderText("输入聊天内容或 /命令，回车发送给勾选的在线账号")
        cmd_row.addWidget(self.input_cmd, 1)
        btn_send = QPushButton("发送")
        btn_send.setObjectName("primary")
        btn_send.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_send.clicked.connect(self.send_command)
        cmd_row.addWidget(btn_send)
        self.input_cmd.returnPressed.connect(self.send_command)
        return cmd_bar

    @staticmethod
    def _card_title(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("cardTitle")
        return label

    @staticmethod
    def _card_hint(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("cardHint")
        return label

    @staticmethod
    def _form_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("formLabel")
        return label

    @staticmethod
    def _divider() -> QFrame:
        line = QFrame()
        line.setObjectName("divider")
        line.setFixedHeight(1)
        return line

    def _build_console(self) -> QFrame:
        box = QFrame()
        box.setObjectName("panel")
        v = QVBoxLayout(box)
        v.setContentsMargins(14, 10, 14, 10)
        v.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(self._card_title("控制台"))
        self.console_count = QLabel("0 条")
        self.console_count.setObjectName("countChip")
        head.addWidget(self.console_count)
        head.addStretch(1)
        self.btn_mode_all = QPushButton("全部日志")
        self.btn_mode_all.setObjectName("mode-checked")
        self.btn_mode_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_mode_chat = QPushButton("聊天监控")
        self.btn_mode_chat.setObjectName("mode")
        self.btn_mode_chat.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_mode_all.clicked.connect(
            lambda: self._switch_mode("console", self.btn_mode_all, self.btn_mode_chat))
        self.btn_mode_chat.clicked.connect(
            lambda: self._switch_mode("monitor", self.btn_mode_chat, self.btn_mode_all))
        head.addWidget(self.btn_mode_all)
        head.addWidget(self.btn_mode_chat)
        v.addLayout(head)

        self.console = QPlainTextEdit()
        self.console.setObjectName("console")
        self.console.setReadOnly(True)
        self.console.setMaximumBlockCount(2000)
        self.console.setMinimumHeight(90)
        v.addWidget(self.console)
        return box

    def _build_auto_reply(self) -> QFrame:
        """自动回复面板：总开关 + 主人名单 + 规则表（增删）。"""
        box = QFrame()
        box.setObjectName("panel")
        v = QVBoxLayout(box)
        v.setContentsMargins(14, 10, 14, 10)
        v.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(self._card_title("自动回复"))
        head.addWidget(self._card_hint("检测私聊 → 回复 / 执行命令 / 传送点"))
        head.addStretch(1)
        self.cb_auto_reply = CheckableBox("启用")
        self.cb_auto_reply.setChecked(False)
        self.cb_auto_reply.toggled.connect(self._on_auto_reply_toggle)
        head.addWidget(self.cb_auto_reply)
        v.addLayout(head)

        masters_row = QHBoxLayout()
        masters_row.setSpacing(8)
        masters_row.addWidget(self._form_label("主人名单"))
        self.input_masters = QLineEdit()
        self.input_masters.setPlaceholderText(
            "逗号分隔，如 Steve, Alex；仅名单内玩家可触发「仅主人」规则与 sethome/goto")
        self.input_masters.editingFinished.connect(self._sync_auto_reply)
        masters_row.addWidget(self.input_masters, 1)
        v.addLayout(masters_row)

        # 规则表：触发词 / 回复 / 执行命令 / 仅主人
        # 列宽交互式（可拖动表头分隔线调整），初始按比例分配
        self.table_rules = QTableWidget(0, 4)
        self.table_rules.setHorizontalHeaderLabels(
            ["触发词", "回复内容", "执行命令", "仅主人"])
        tips = [
            "留空表示任意私聊都触发",
            "支持 {name} / {msg} 占位符",
            "不带 / 也可以，发送时自动作为命令",
            "仅「主人名单」内的玩家可触发",
        ]
        for col, tip in enumerate(tips):
            self.table_rules.horizontalHeaderItem(col).setToolTip(tip)
        header = self.table_rules.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setMinimumSectionSize(80)
        header.setStretchLastSection(False)
        self.table_rules.verticalHeader().setVisible(False)
        self.table_rules.verticalHeader().setDefaultSectionSize(30)
        self.table_rules.setAlternatingRowColors(True)
        self.table_rules.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        self.table_rules.setMinimumHeight(108)
        self.table_rules.itemChanged.connect(lambda _: self._sync_auto_reply())
        v.addWidget(self.table_rules, 1)

        btns = QHBoxLayout()
        btns.setSpacing(8)
        btn_add = QPushButton("＋ 添加规则")
        btn_add.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_add.clicked.connect(self._add_rule_row)
        btns.addWidget(btn_add)
        btn_del = QPushButton("删除所选")
        btn_del.setObjectName("danger")
        btn_del.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_del.clicked.connect(self._del_rule_row)
        btns.addWidget(btn_del)
        btns.addStretch(1)
        btns.addWidget(self._card_hint("触发词留空 = 任意私聊"))
        v.addLayout(btns)
        return box

    # ---- 自动回复 ----
    def _on_auto_reply_toggle(self, on: bool) -> None:
        self._sync_auto_reply()
        self.log("system", f"[系统] 自动回复已{'启用' if on else '关闭'}")

    def _add_rule_row(self) -> None:
        r = self.table_rules.rowCount()
        self.table_rules.insertRow(r)
        for c, val in enumerate(("", "", "", "")):
            self.table_rules.setItem(r, c, QTableWidgetItem(val))
        # 仅主人列用复选框样式
        item = self.table_rules.item(r, 3)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Unchecked)

    def _del_rule_row(self) -> None:
        rows = sorted({i.row() for i in self.table_rules.selectedIndexes()},
                      reverse=True)
        for r in rows:
            self.table_rules.removeRow(r)
        self._sync_auto_reply()

    def _sync_auto_reply(self) -> None:
        """表格/UI → 引擎。"""
        eng = self.auto_reply
        eng.enabled = self.cb_auto_reply.isChecked()
        eng.masters = [s.strip() for s in
                       self.input_masters.text().split(",") if s.strip()]
        eng.rules = []
        for r in range(self.table_rules.rowCount()):
            def cell(c: int) -> str:
                it = self.table_rules.item(r, c)
                return it.text().strip() if it else ""
            owner_item = self.table_rules.item(r, 3)
            eng.rules.append(ReplyRule(
                trigger=cell(0), reply=cell(1), command=cell(2),
                owner_only=(owner_item is not None
                            and owner_item.checkState() == Qt.CheckState.Checked)))

    def _load_rules_to_table(self) -> None:
        """引擎 → 表格（启动加载配置时用）。"""
        self.table_rules.blockSignals(True)
        self.table_rules.setRowCount(0)
        for rule in self.auto_reply.rules:
            r = self.table_rules.rowCount()
            self.table_rules.insertRow(r)
            self.table_rules.setItem(r, 0, QTableWidgetItem(rule.trigger))
            self.table_rules.setItem(r, 1, QTableWidgetItem(rule.reply))
            self.table_rules.setItem(r, 2, QTableWidgetItem(rule.command))
            owner = QTableWidgetItem("仅主人" if rule.owner_only else "")
            owner.setFlags(owner.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            owner.setCheckState(
                Qt.CheckState.Checked if rule.owner_only
                else Qt.CheckState.Unchecked)
            self.table_rules.setItem(r, 3, owner)
        self.input_masters.setText(", ".join(self.auto_reply.masters))
        self.cb_auto_reply.setChecked(self.auto_reply.enabled)
        self.table_rules.blockSignals(False)

    def _emit_chat(self, text: str, name: str) -> None:
        """recv 线程聊天回调：入信号桥（日志 + 私聊检测）。"""
        # 服务器回执（保存成功/失败、家不存在）由主线程的传送流程读取
        self.bridge.chat.emit(name, text)
        self.bridge.log.emit("chat", text, name)
        self.bridge.private.emit(name, text)

    def _on_private(self, name: str, text: str) -> None:
        """主线程：私聊检测 + 自动回复执行。"""
        row = self.row_index.get(name)
        if row is None or row.client is None or not row.client.running:
            return
        self.handle_private_chat(row.client, name, text)

    def handle_private_chat(self, client: McBotClient, name: str,
                            text: str) -> None:
        """检测私聊 → 命中规则 → 回复/执行命令。"""
        sender = content = None
        try:
            parsed = parse_private_chat(text, name)
            if parsed is None:
                return
            sender, content = parsed
            self.log("system", f"[{name}] 📩 私聊来自 {sender}: {content}")
            if self.key_commands.handle(
                    sender, content, self.auto_reply.masters,
                    reply=lambda text, log_text="": self._reply_key(
                        client, name, sender, text, log_text),
                    log=lambda text: self.log("system", f"[{name}] {text}")):
                return
            if self.teleport.handle(
                    name, sender, content, self.auto_reply.masters,
                    send_command=lambda command, n=name: self._send_command_to(
                        n, command),
                    reply=lambda text, log_text="": self._reply_teleport(
                        name, sender, text, log_text),
                    log=lambda text: self.bridge.log.emit(
                        "system", f"[{name}] {text}", "")):
                return
            hits = self.auto_reply.match(sender, content)
            for rule in hits:
                reply = self.auto_reply.render(rule.reply, sender, content)
                cmd = self.auto_reply.render(rule.command, sender, content)
                if reply:
                    client.send_chat(reply)
                    self.log("system", f"[{name}] 💬 自动回复 {sender}: {reply}")
                if cmd:
                    client.send_command(cmd)
                    self.log("system", f"[{name}] ⚙️ 自动执行: {cmd}")
        except Exception as exc:
            if sender is not None:
                self.log("system", f"[{name}] 自动回复出错: {exc}")

    def _reply_key(self, client: McBotClient, name: str, sender: str,
                   text: str, log_text: str = "") -> None:
        """以 /tell 私聊回复密钥命令，避免密钥进入公屏。"""
        if not client.running:
            return
        client.send_command(f"tell {sender} {text}")
        self.log("system", f"[{name}] 🔏 私聊回复 {sender}: {log_text or text}")

    def _send_command_to(self, name: str, command: str) -> None:
        """按账号名取当前客户端发送命令；定时器线程也会调用，失败抛异常。"""
        row = self.row_index.get(name)
        client = row.client if row is not None else None
        if client is None or not client.running:
            raise RuntimeError("账号未在线")
        client.send_command(command)

    def _reply_teleport(self, name: str, sender: str, text: str,
                        log_text: str = "") -> None:
        """传送流程的私聊回复：可能在定时器线程执行，日志走信号桥。"""
        try:
            self._send_command_to(name, f"tell {sender} {text}")
        except Exception as exc:
            self.bridge.log.emit(
                "system", f"[{name}] 私聊回复失败: {exc}", "")
            return
        self.bridge.log.emit(
            "system", f"[{name}] 🔏 私聊回复 {sender}: {log_text or text}", "")

    def _build_statusbar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("statusbar")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(14, 7, 14, 7)
        lay.setSpacing(10)

        self.status_left = QLabel("McBot 离线模式 · 未连接服务器")
        self.status_left.setObjectName("statusText")
        lay.addWidget(self.status_left)
        lay.addStretch(1)

        # 游戏状态：生命 / 饥饿 / 坐标（取第一个在线账号）
        self.status_game = QLabel("❤ --  🍗 --  XYZ --")
        self.status_game.setObjectName("statusGame")
        lay.addWidget(self.status_game)
        lay.addWidget(make_vsep())

        # 服务器延迟
        self.status_ping = QLabel("延迟 --")
        self.status_ping.setObjectName("statusPing")
        self.status_ping.setToolTip("当前线路延迟，每 5 秒测一次")
        lay.addWidget(self.status_ping)
        lay.addWidget(make_vsep())

        # 系统信息：内存 / 网络上下行（系统版本放 tooltip）
        self.status_sys = QLabel("内存 -- · ↓ -- ↑ --")
        self.status_sys.setObjectName("statusSys")
        self.status_sys.setToolTip(
            f"{self._sys_version} · 内存已用/总量 · 网络下行/上行速率")
        lay.addWidget(self.status_sys)
        lay.addWidget(make_vsep())

        self.checked_label = QLabel("已勾选 0")
        self.checked_label.setObjectName("statusChecked")
        lay.addWidget(self.checked_label)
        return bar

    # ---- 状态栏定时刷新 ----
    def _status_client(self) -> Optional[McBotClient]:
        """取第一个在线的客户端用于展示游戏状态。"""
        for r in self.rows:
            if r.online and r.client is not None:
                return r.client
        return None

    def _refresh_status(self) -> None:
        """每 500ms tick：游戏状态；分频 1s 刷系统信息、5s 测延迟。"""
        self._status_tick += 1

        # 游戏状态（生命/饥饿/坐标）
        client = self._status_client()
        if client is not None:
            hp = max(client.health, 0.0)
            food = int(max(client.food, 0.0))
            self.status_game.setText(
                f"❤ {hp:.1f}  🍗 {food}  "
                f"XYZ {client.x:.1f} / {client.y:.1f} / {client.z:.1f}")
        else:
            self.status_game.setText("❤ --  🍗 --  XYZ --")

        # 延迟显示（按高低着色）
        if self._latency_ms is None:
            if self._latency_err:
                text = self._latency_err
            elif self._status_tick > 2:
                text = "延迟 超时"
            else:
                text = "延迟 --"
            color = C_RED_FG if text == "延迟 超时" or self._latency_err else C_MUTED
            self.status_ping.setText(text)
            self.status_ping.setStyleSheet(f"color: {color};")
        else:
            ms = self._latency_ms
            color = C_GREEN if ms < 80 else (C_YELLOW if ms < 160 else C_RED_FG)
            self.status_ping.setText(f"延迟 {ms} ms")
            self.status_ping.setStyleSheet(f"color: {color};")

        # 每 2 tick（~1s）：内存 + 网络速率
        if self._status_tick % 2 == 0:
            try:
                mem = psutil.virtual_memory()
                self.status_sys.setText(
                    f"内存 {mem.used / 2**30:.1f}/{mem.total / 2**30:.1f}GiB "
                    f"({mem.percent:.0f}%) · "
                    f"↓ {self._fmt_rate(self._net_down)} ↑ {self._fmt_rate(self._net_up)}")
                io = psutil.net_io_counters()
                now = time.monotonic()
                if self._net_prev is not None:
                    t0, rx0, tx0 = self._net_prev
                    dt = now - t0
                    if dt > 0:
                        self._net_down = max((io.bytes_recv - rx0) / dt, 0.0)
                        self._net_up = max((io.bytes_sent - tx0) / dt, 0.0)
                self._net_prev = (now, io.bytes_recv, io.bytes_sent)
            except Exception:
                pass

        # 每 10 tick（~5s）：后台测一次服务器延迟
        if (self._status_tick % 10 == 0 and not self._latency_busy
                and self.input_addr.text().strip()):
            self._ping_latency()

    @staticmethod
    def _fmt_rate(bps: float) -> str:
        if bps >= 2**20:
            return f"{bps / 2**20:.1f}MiB/s"
        if bps >= 2**10:
            return f"{bps / 2**10:.1f}KiB/s"
        return f"{bps:.0f}B/s"

    def _ping_latency(self) -> None:
        """后台线程做 status ping 计时，不阻塞 UI。"""
        host, port = self._parse_addr()
        self._latency_busy = True

        def worker() -> None:
            t0 = time.monotonic()
            try:
                McBotClient.ping_server(host, port, timeout=3.0)
                ms = int((time.monotonic() - t0) * 1000)
                self._latency_ms = ms
                self._latency_err = ""
            except socket.gaierror:
                self._latency_ms = None
                self._latency_err = "解析失败"
            except Exception:
                self._latency_ms = None
                self._latency_err = "超时"
            finally:
                self._latency_busy = False

        threading.Thread(target=worker, daemon=True).start()

    # ---- 日志 ----
    def log(self, kind: str, text: str, account: str = "") -> None:
        """主线程直接写日志（后台线程请用 bridge.log.emit）。"""
        self._on_log(kind, text, account)

    def _online_count(self) -> int:
        return sum(1 for r in self.rows if r.online)

    def _console_visible(self, kind: str, text: str) -> bool:
        """全部日志 = 全部；聊天监控 = 聊天与私聊/回复相关记录。"""
        if self._console_mode == "console":
            return True
        return kind == "chat" or any(
            t in text for t in ("📩", "💬", "⚙️", "🔏"))

    def _on_log(self, kind: str, text: str, account: str = "") -> None:
        # 多账号（≥2 在线）时聊天按账号区分显示；单账号时原样显示
        show = text
        if kind == "chat" and account and self._online_count() >= 2:
            show = f"[{account}] {text}"
        ts = time.strftime('%Y-%m-%d %H:%M:%S')
        self._log_seq += 1
        self._log_lines.append((self._log_seq, kind, show, ts, account))
        if len(self._log_lines) > 2000:
            self._log_lines = self._log_lines[-1500:]
        if self._console_visible(kind, show):
            self._append_console(kind, show, ts)
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            _rotate_log(LOG_FILE)
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"{ts} {show}\n")
            # 每个账号独立聊天记录：logs/chats/<账号>.log
            if kind == "chat" and account:
                CHAT_DIR.mkdir(parents=True, exist_ok=True)
                safe = "".join(c for c in account
                               if c not in '\\/:*?"<>|').strip() or "unknown"
                chat_file = CHAT_DIR / f"{safe}.log"
                _rotate_log(chat_file)
                with open(chat_file, "a", encoding="utf-8") as f:
                    f.write(f"{ts} {text}\n")
        except OSError:
            pass

    def _append_console(self, kind: str, text: str, ts: str) -> None:
        """按类别着色写入控制台：时间戳灰、系统暗、聊天亮、错误红、成功绿。"""
        color = C_DIM if kind == "system" else "#d6e2f0"
        if any(t in text for t in ("❌", "⛔", "⚠", "失败", "错误", "超时")):
            color = C_RED_FG
        elif any(t in text for t in ("✅", "✔", "成功")):
            color = "#56d364"
        elif any(t in text for t in ("📩", "💬", "⚙️", "🔏")):
            color = C_CYAN

        cur = self.console.textCursor()
        cur.movePosition(QTextCursor.MoveOperation.End)
        ts_fmt = QTextCharFormat()
        ts_fmt.setForeground(QColor("#4d5a6c"))
        cur.insertText(f"{ts} ", ts_fmt)
        body_fmt = QTextCharFormat()
        body_fmt.setForeground(QColor(color))
        cur.insertText(text + "\n", body_fmt)
        self.console.setTextCursor(cur)
        self.console.ensureCursorVisible()
        count = max(self.console.blockCount() - 1, 0)
        self.console_count.setText(f"{count} 条")

    def _switch_mode(self, mode: str, active: QPushButton, other: QPushButton) -> None:
        self._console_mode = mode
        active.setObjectName("mode-checked")
        other.setObjectName("mode")
        for w in (active, other):
            w.style().unpolish(w)
            w.style().polish(w)
        # 重建显示：重放日志并按类别着色
        self.console.clear()
        for _seq, kind, text, ts, _account in self._log_lines:
            if self._console_visible(kind, text):
                self._append_console(kind, text, ts)

    def open_log_dir(self) -> None:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        os.startfile(str(LOG_DIR))

    # ---- 连接逻辑 ----
    def _parse_addr(self) -> tuple:
        addr = self.input_addr.text().strip()
        host, _, port = addr.partition(":")
        try:
            port_n = int(port) if port else 25565
        except ValueError:
            port_n = 25565
        return host or addr, port_n

    def online_selected(self) -> None:
        targets = [r for r in self.rows if r.is_checked() and r.status != "在线"]
        if not targets:
            self.log("system", "[系统] 没有可上线的勾选账号")
            return
        self.header_addr.setText(
            f"{self.input_addr.text()}  ·  离线模式")
        # 按上号间隔逐个连接
        delay = self.spin_gap.value() * 1000
        for i, row in enumerate(targets):
            QTimer.singleShot(i * delay, lambda name=row.account_name:
                              self._start_account(name))
        self.log("system", f"[系统] 开始上线 {len(targets)} 个账号，"
                           f"间隔 {self.spin_gap.value()} 秒")

    def _start_account(self, name: str) -> None:
        row = self.row_index.get(name)
        if row is None or row.status == "在线" or row.status == "连接中":
            return
        row.manual_stop = False
        host, port = self._parse_addr()
        line_no = self.current_line
        threading.Thread(target=self._connect_worker,
                         args=(row, host, port, line_no),
                         daemon=True).start()

    def _connect_worker(self, row: AccountRow, host: str, port: int,
                        line_no: int) -> None:
        name = row.account_name
        self.bridge.status.emit(name, "连接中")
        self.bridge.log.emit("system", f"[{name}] 线路 {line_no}: {host}:{port}", "")
        try:
            client = McBotClient(
                host, port=port, username=name,
                on_chat=lambda text, n=name: self._emit_chat(text, n),
                on_disconnect=lambda reason, n=name: self._cb_disconnect(n, reason),
                on_teleport=lambda x, y, z, n=name: self.bridge.teleported.emit(
                    n, x, y, z),
                auto_respawn=self.cb_respawn.isChecked(),
            )
            client.connect()
            row.client = client
            self.bridge.status.emit(name, "在线")
            self.bridge.log.emit("system", f"[{name}] ✅ 进入服务器", "")
            # 服务器要求 /login 时（如 zhaomc），密码非空则延迟自动登录
            if self.cb_login.isChecked() and row.password:
                time.sleep(3)
                if client.running:
                    client.send_command(f"/login {row.password}")
                    self.bridge.log.emit("system", f"[{name}] 🔏 已发送 /login ******", "")
        except Exception as exc:
            self.bridge.status.emit(name, "断开")
            self.bridge.log.emit("system", f"[{name}] ❌ 连接失败: {exc}", "")

    def _on_respawn_toggle(self, on: bool) -> None:
        """勾选变化时实时同步到所有在线账号（运行选项区标注"立即生效"）。"""
        for row in self.rows:
            if row.client is not None:
                row.client.auto_respawn = on

    def _cb_disconnect(self, name: str, reason: str) -> None:
        """recv 线程回调：更新状态并询问主线程是否自动重连。"""
        self.teleport.cancel(name, "连接已断开")
        self.bridge.status.emit(name, "断开")
        self.bridge.log.emit("system", f"[{name}] ⛔ 已断开: {reason}", "")
        self.bridge.disconnected.emit(name)

    def _on_disconnected(self, name: str) -> None:
        row = self.row_index.get(name)
        if row is None or row.manual_stop:
            return
        if self.cb_reconnect.isChecked():
            self.log("system", f"[{name}] 🔄 5 秒后自动重连…")
            QTimer.singleShot(5000, lambda n=name: self._start_account(n))

    def offline_selected(self) -> None:
        n = 0
        for row in self.rows:
            if row.is_checked() and row.status in ("在线", "连接中"):
                row.manual_stop = True
                if row.client:
                    row.client.disconnect()
                    row.client = None
                row.set_status("离线")
                self.log("system", f"[{row.account_name}] ⛔ 手动下线")
                n += 1
        if n == 0:
            self.log("system", "[系统] 没有在线的勾选账号")
        self._refresh_counters()

    def stop_all(self) -> None:
        for row in self.rows:
            if row.status in ("在线", "连接中"):
                row.manual_stop = True
                if row.client:
                    row.client.disconnect()
                    row.client = None
                row.set_status("离线")
        self.log("system", "[系统] 已停止全部连接")
        self._refresh_counters()

    def reset_reconnect(self) -> None:
        """断开并立即重连所选账号（换线路/换地址后使用）。"""
        targets = [r for r in self.rows if r.is_checked()]
        for row in targets:
            row.manual_stop = True
            if row.client:
                row.client.disconnect()
                row.client = None
            row.set_status("离线")
        for i, row in enumerate(targets):
            QTimer.singleShot(i * 1000, lambda n=row.account_name:
                              self._start_account(n))
        self.log("system", f"[系统] 重置重连 {len(targets)} 个账号")

    def _on_status(self, name: str, status: str) -> None:
        row = self.row_index.get(name)
        if row:
            row.set_status(status)
        self._refresh_counters()

    def send_command(self) -> None:
        text = self.input_cmd.text().strip()
        prefix = self.combo_cmd.currentText()
        # 第一项「发送到所选」原样发送；其余作为命令前缀拼接
        if prefix != "发送到所选" and not text:
            text = prefix.split(" ", 1)[0]
        elif prefix != "发送到所选" and text:
            text = prefix.split(" ", 1)[0] + " " + text
        if not text:
            return
        sent = 0
        for row in self.rows:
            if not row.is_checked() or not row.client or not row.client.running:
                continue
            try:
                if text.startswith("/"):
                    row.client.send_command(text)
                else:
                    row.client.send_chat(text)
                sent += 1
            except Exception as exc:
                self.log("system", f"[{row.account_name}] 发送失败: {exc}")
        if sent:
            self.log("system", f"[系统] 已发送给 {sent} 个账号: {text}")
            self.input_cmd.clear()
        else:
            self.log("system", "[系统] 没有在线的勾选账号")

    # ---- 线路 ----
    def _select_line(self, idx: int) -> None:
        self.line_addresses[self.current_line] = self.input_addr.text()
        self.current_line = idx
        for i, btn in enumerate(self.line_buttons):
            btn.setChecked(i == idx - 1)
        saved = self.line_addresses.get(idx, "")
        if saved:
            self.input_addr.setText(saved)
        self.line_hint.setText("已保存" if saved else "")
        self.line_hint.setToolTip(saved)

    def save_line(self) -> None:
        addr = self.input_addr.text().strip()
        self.line_addresses[self.current_line] = addr
        self.line_hint.setText("已保存" if addr else "")
        self.line_hint.setToolTip(addr)
        self.log("system", f"[系统] 线路 {self.current_line} 已保存: {addr}")

    def ping_line(self) -> None:
        host, port = self._parse_addr()
        self.log("system", f"[系统] 测速 {host}:{port} …")

        def worker() -> None:
            t0 = time.perf_counter()
            try:
                McBotClient.ping_server(host, port)
                ms = int((time.perf_counter() - t0) * 1000)
                self.bridge.log.emit("system", f"[系统] {host}:{port} 延迟 {ms} ms", "")
            except socket.gaierror:
                self.bridge.log.emit(
                    "system",
                    f"[系统] 测速失败: 域名解析失败 {host} "
                    f"（请检查地址拼写，或改用服务器 IP）", "")
            except Exception as exc:
                self.bridge.log.emit("system", f"[系统] 测速失败: {exc}", "")

        threading.Thread(target=worker, daemon=True).start()

    # ---- 账号管理 ----
    def _add_row(self, name: str, tag: str = "空", password: str = "") -> None:
        row = AccountRow(name, tag, password, on_change=self._refresh_counters)
        self.rows.append(row)
        self.row_index[name] = row
        # 插到末尾 stretch 之前
        self.list_lay.insertWidget(self.list_lay.count() - 1, row)

    def add_account(self) -> None:
        name = self.input_name.text().strip()
        if not name:
            self.log("system", "[系统] 请输入账号名")
            return
        if name in self.row_index:
            self.log("system", f"[系统] 账号 {name} 已存在")
            return
        self._add_row(name, password=self.input_pwd.text())
        self.input_name.clear()
        self.input_pwd.clear()
        self.log("system", f"[系统] 已添加账号 {name}")
        self._refresh_counters()

    def select_all(self) -> None:
        for r in self.rows:
            r.set_checked(True)

    def invert_select(self) -> None:
        for r in self.rows:
            r.set_checked(not r.is_checked())

    def clear_select(self) -> None:
        for r in self.rows:
            r.set_checked(False)

    def delete_selected(self) -> None:
        for row in [r for r in self.rows if r.is_checked()]:
            row.manual_stop = True
            if row.client:
                row.client.disconnect()
            self.rows.remove(row)
            self.row_index.pop(row.account_name, None)
            self.list_lay.removeWidget(row)
            row.deleteLater()
            self.log("system", f"[系统] 已删除账号 {row.account_name}")
        self._refresh_counters()

    def _refresh_counters(self) -> None:
        total = len(self.rows)
        checked = sum(1 for r in self.rows if r.is_checked())
        online = sum(1 for r in self.rows if r.online)
        self.selected_label.setText(f"已选 {checked} / {total}")
        self.pill_online.setText(f"在线 {online}/{total}")
        self.checked_label.setText(f"已勾选 {checked}")
        addr = self.input_addr.text().strip() or "未设置服务器"
        self.status_left.setText(f"McBot 离线模式 · {addr}")
        # 顶部状态胶囊：有账号在线时点亮
        dot_color = C_GREEN if online else C_MUTED
        self.header_dot.setStyleSheet(
            f"background: {dot_color}; border-radius: 6px;")
        self.status_chip.setProperty("online", "yes" if online else "no")
        for w in (self.status_chip, self.pill_online):
            w.style().unpolish(w)
            w.style().polish(w)
        self.empty_state.setVisible(total == 0)

    def refresh_status(self) -> None:
        self._refresh_counters()
        self.log("system", "[系统] 状态已刷新")

    # ---- Web 登录密钥 ----
    def show_web_key(self) -> None:
        """生成/查看 Web 控制台登录密钥。"""
        if webauth.current() is None:
            webauth.generate()
            self.log("system", "[系统] 🔑 已生成 Web 登录密钥（10 分钟有效）")

        dlg = QDialog(self)
        dlg.setWindowTitle("Web 访问密钥")
        dlg.setObjectName("webKeyDialog")
        dlg.setMinimumWidth(460)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(12)

        title = QLabel("Web 控制台登录密钥")
        title.setStyleSheet("font-size: 16px; font-weight: 600; color: #e6edf3;")
        lay.addWidget(title)

        sub = QLabel("浏览器打开 http://<本机IP>:6099/，输入下面的密钥登录。")
        sub.setWordWrap(True)
        sub.setStyleSheet("color: #8b98a9; font-size: 12.5px;")
        lay.addWidget(sub)

        key_label = QLabel()
        key_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        key_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        key_label.setFont(QFont("Consolas", 16, QFont.Weight.Bold))
        key_label.setStyleSheet(
            "background: #0e131b; border: 1px solid #2a3547; border-radius: 10px;"
            " padding: 14px; color: #9dc0ff; letter-spacing: 2px;")
        lay.addWidget(key_label)

        expiry = QLabel()
        expiry.setAlignment(Qt.AlignmentFlag.AlignCenter)
        expiry.setStyleSheet("color: #8b98a9; font-size: 12.5px;")
        lay.addWidget(expiry)

        note = QLabel(
            "密钥有效期 10 分钟。游戏内私聊 get（仅主人名单）可获取当前密钥，"
            "私聊 xy <密钥> 可续约 10 分钟；两条命令的回复也都是私聊。"
            "网页面板右上角也能续约。")
        note.setWordWrap(True)
        note.setStyleSheet("color: #627086; font-size: 12px;")
        lay.addWidget(note)

        if not self.auto_reply.masters:
            warn = QLabel(
                "尚未配置「主人名单」：游戏内 get/xy 会被忽略。"
                "请先在「自动回复 → 主人名单」填写你的游戏名（逗号分隔）。")
            warn.setWordWrap(True)
            warn.setStyleSheet("color: #d9a441; font-size: 12px;")
            lay.addWidget(warn)

        row = QHBoxLayout()
        row.setSpacing(8)
        copy_btn = QPushButton("复制密钥")
        copy_btn.setObjectName("primary")
        regen_btn = QPushButton("重新生成")
        regen_btn.setObjectName("ghost")
        close_btn = QPushButton("关闭")
        close_btn.setObjectName("ghost")
        row.addWidget(copy_btn)
        row.addWidget(regen_btn)
        row.addStretch(1)
        row.addWidget(close_btn)
        lay.addLayout(row)

        def refresh() -> None:
            record = webauth.current()
            if record is None:
                key_label.setText("密钥已过期")
                key_label.setStyleSheet(
                    "background: #0e131b; border: 1px solid #2a3547;"
                    " border-radius: 10px; padding: 14px; color: #627086;"
                    " letter-spacing: 2px;")
                expiry.setText("请点“重新生成”获取新的密钥")
                copy_btn.setEnabled(False)
                return
            left = webauth.remaining(record)
            minutes, seconds = divmod(left, 60)
            key_label.setText(record["key"])
            key_label.setStyleSheet(
                "background: #0e131b; border: 1px solid #2a3547;"
                " border-radius: 10px; padding: 14px; color: #9dc0ff;"
                " letter-spacing: 2px;")
            expiry.setText(
                f"剩余 {minutes:02d}:{seconds:02d} · "
                f"{webauth.expiry_text(record)} 到期")
            copy_btn.setEnabled(True)

        def copy_key() -> None:
            record = webauth.current()
            if record is None:
                return
            QApplication.clipboard().setText(record["key"])
            copy_btn.setText("已复制")
            QTimer.singleShot(1400, lambda: copy_btn.setText("复制密钥"))

        def regenerate() -> None:
            answer = QMessageBox.question(
                dlg, "重新生成密钥",
                "重新生成会让当前密钥和已登录的网页立即失效，是否继续？")
            if answer != QMessageBox.StandardButton.Yes:
                return
            webauth.generate()
            refresh()
            self.log("system", "[系统] 🔑 已重新生成 Web 登录密钥")

        copy_btn.clicked.connect(copy_key)
        regen_btn.clicked.connect(regenerate)
        close_btn.clicked.connect(dlg.accept)

        refresh()
        timer = QTimer(dlg)
        timer.setInterval(1000)
        timer.timeout.connect(refresh)
        timer.start()
        dlg.exec()

    # ---- 配置保存/加载 ----
    def save_config(self) -> None:
        cfg = {
            "current_line": self.current_line,
            "lines": {str(k): v for k, v in self.line_addresses.items()},
            "addr": self.input_addr.text(),
            "gap": self.spin_gap.value(),
            "auto_reconnect": self.cb_reconnect.isChecked(),
            "auto_login": self.cb_login.isChecked(),
            "auto_respawn": self.cb_respawn.isChecked(),
            "accounts": [
                {"name": r.account_name, "password": r.password}
                for r in self.rows
            ],
            "auto_reply": self.auto_reply.to_dict(),
        }
        try:
            CONFIG_FILE.write_text(
                json.dumps(cfg, ensure_ascii=False, indent=2),
                encoding="utf-8")
            self.log("system", f"[系统] ✅ 配置已保存到 {CONFIG_FILE.name}")
        except OSError as exc:
            self.log("system", f"[系统] ❌ 保存配置失败: {exc}")

    def load_config(self) -> None:
        if not CONFIG_FILE.exists():
            return
        try:
            cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for key, addr in (cfg.get("lines") or {}).items():
            try:
                self.line_addresses[int(key)] = str(addr)
            except ValueError:
                continue
        addr = str(cfg.get("addr", "")).strip()
        if addr:
            self.input_addr.setText(addr)
            self.line_hint.setText("已保存")
            self.line_hint.setToolTip(addr)
        line = int(cfg.get("current_line", self.current_line))
        if 1 <= line <= 4:
            self.current_line = line
            for i, btn in enumerate(self.line_buttons):
                btn.setChecked(i == line - 1)
        self.spin_gap.setValue(int(cfg.get("gap", self.spin_gap.value())))
        self.cb_reconnect.setChecked(bool(cfg.get("auto_reconnect", True)))
        self.cb_login.setChecked(bool(cfg.get("auto_login", True)))
        self.cb_respawn.setChecked(bool(cfg.get("auto_respawn", True)))
        for acc in cfg.get("accounts") or []:
            name = str(acc.get("name", "")).strip()
            if name and name not in self.row_index:
                self._add_row(name, password=str(acc.get("password", "")))
        if "auto_reply" in cfg:
            self.auto_reply.load(cfg["auto_reply"])
            self._load_rules_to_table()
        self._refresh_counters()
        if self.rows:
            self.log("system",
                     f"[系统] 已加载配置：{len(self.rows)} 个账号")

    # ---- Web 控制台桥接（只由 GUI 线程调用；接口名与 webui.BotManager 对齐） ----
    def web_addr(self) -> str:
        return self.input_addr.text().strip()

    def web_snapshot(self, since: int = 0) -> dict:
        """给 Web 面板的状态快照：账号 / 配置 / 自动回复 / 增量日志。"""
        accounts = []
        online = 0
        for row in self.rows:
            c = row.client
            live = row.online and c is not None
            if row.online:
                online += 1
            accounts.append({
                "name": row.account_name,
                "status": row.status,
                "online": row.online,
                "has_password": bool(row.password),
                "error": "",
                "hp": round(float(c.health), 1) if live else None,
                "food": int(c.food) if live else None,
                "x": round(c.x, 2) if live else None,
                "y": round(c.y, 2) if live else None,
                "z": round(c.z, 2) if live else None,
            })
        logs = [
            {"seq": seq, "ts": ts, "kind": kind, "text": text, "account": account}
            for seq, kind, text, ts, account in self._log_lines
            if seq > since
        ][-400:]
        return {
            "ok": True,
            "stats": {"online": online, "total": len(self.rows),
                      "addr": self.web_addr(), "seq": self._log_seq},
            "accounts": accounts,
            "config": {
                "addr": self.web_addr(),
                "gap": int(self.spin_gap.value()),
                "current_line": int(self.current_line),
                "lines": {str(k): v for k, v in self.line_addresses.items()},
                "auto_reconnect": self.cb_reconnect.isChecked(),
                "auto_login": self.cb_login.isChecked(),
                "auto_respawn": self.cb_respawn.isChecked(),
            },
            "auto_reply": self.auto_reply.to_dict(),
            "logs": logs,
        }

    def web_add_account(self, name: str, password: str) -> dict:
        name = (name or "").strip()
        if not name:
            return {"error": "账号名不能为空"}
        if name in self.row_index:
            return {"error": f"账号 {name} 已存在"}
        self._add_row(name, password=password or "")
        self.log("system", f"[系统] 已添加账号 {name}（来自 Web）")
        self._refresh_counters()
        self.save_config()
        return {"ok": True}

    def web_remove_accounts(self, names: list) -> dict:
        removed = 0
        for name in names or []:
            row = self.row_index.get(str(name))
            if row is None:
                continue
            row.manual_stop = True
            if row.client:
                row.client.disconnect()
            self.rows.remove(row)
            self.row_index.pop(row.account_name, None)
            self.list_lay.removeWidget(row)
            row.deleteLater()
            self.log("system", f"[系统] 已删除账号 {row.account_name}（来自 Web）")
            removed += 1
        if removed:
            self._refresh_counters()
            self.save_config()
        return {"ok": True, "removed": removed}

    def web_connect_many(self, names: list) -> dict:
        if not self.web_addr():
            return {"error": "请先填写服务器地址"}
        targets = [self.row_index[n] for n in (names or [])
                   if n in self.row_index]
        if not targets:
            return {"error": "没有可上线的账号"}
        delay = self.spin_gap.value() * 1000   # 与桌面端一致：按上号间隔逐个连接
        for i, row in enumerate(targets):
            QTimer.singleShot(
                i * delay,
                lambda name=row.account_name: self._start_account(name))
        self.log("system", f"[系统] 开始上线 {len(targets)} 个账号，"
                           f"间隔 {self.spin_gap.value()} 秒（来自 Web）")
        return {"ok": True, "queued": [r.account_name for r in targets]}

    def web_disconnect(self, names: list, quiet: bool = False) -> dict:
        offline = 0
        for name in names or []:
            row = self.row_index.get(str(name))
            if row is None or row.status not in ("在线", "连接中"):
                continue
            row.manual_stop = True
            if row.client:
                row.client.disconnect()
                row.client = None
            row.set_status("离线")
            offline += 1
            if not quiet:
                self.log("system", f"[{row.account_name}] ⛔ 手动下线（来自 Web）")
        if offline:
            self._refresh_counters()
        return {"ok": True, "offline": offline}

    def web_send_command(self, text: str, names: list) -> dict:
        text = (text or "").strip()
        if not text:
            return {"error": "内容不能为空"}
        targets = ([self.row_index[n] for n in (names or [])
                    if n in self.row_index] if names else list(self.rows))
        sent = 0
        for row in targets:
            client = row.client
            if client is None or not client.running:
                continue
            try:
                if text.startswith("/"):
                    client.send_command(text)
                else:
                    client.send_chat(text)
                sent += 1
            except Exception as exc:  # noqa: BLE001
                self.log("system", f"[{row.account_name}] 发送失败: {exc}")
        if sent:
            self.log("system", f"[系统] 已发送给 {sent} 个账号: {text}（来自 Web）")
            return {"ok": True, "sent": sent}
        return {"error": "没有在线的目标账号"}

    def web_update_config(self, data: dict) -> dict:
        if "addr" in data:
            addr = str(data.get("addr") or "").strip()
            self.input_addr.setText(addr)
            self.line_addresses[self.current_line] = addr
            self.line_hint.setText("已保存" if addr else "")
            self.line_hint.setToolTip(addr)
        if "gap" in data:
            try:
                self.spin_gap.setValue(max(1, min(300, int(data["gap"]))))
            except (TypeError, ValueError):
                pass
        for key, box in (("auto_reconnect", self.cb_reconnect),
                         ("auto_login", self.cb_login),
                         ("auto_respawn", self.cb_respawn)):
            if key in data:
                box.setChecked(bool(data[key]))
        lines = data.get("lines")
        if isinstance(lines, dict):
            for key, value in lines.items():
                try:
                    self.line_addresses[int(key)] = str(value)
                except (TypeError, ValueError):
                    continue
        if "current_line" in data:
            try:
                line = max(1, min(4, int(data["current_line"])))
            except (TypeError, ValueError):
                line = 1
            self.current_line = line
            for i, btn in enumerate(self.line_buttons):
                btn.setChecked(i == line - 1)
        for row in self.rows:   # 自动重生开关立即生效
            if row.client is not None:
                row.client.auto_respawn = self.cb_respawn.isChecked()
        self._refresh_counters()
        self.save_config()
        return {"ok": True}

    def web_update_auto_reply(self, data: dict) -> dict:
        self.auto_reply.load(data or {})
        self._load_rules_to_table()
        self.save_config()
        self.log("system", "[系统] 自动回复已"
                           + ("启用" if self.auto_reply.enabled else "停用")
                           + "（来自 Web）")
        return {"ok": True}

    # ---- 退出 ----
    def closeEvent(self, event) -> None:  # noqa: N802
        self.save_config()
        for row in self.rows:
            row.manual_stop = True
            if row.client:
                row.client.disconnect()
        super().closeEvent(event)


def _parse_args(argv: Optional[List[str]] = None):
    parser = argparse.ArgumentParser(
        description="MC 多开机器人：桌面端 + Web 控制台（默认同时启动）")
    parser.add_argument("--web-host", default="0.0.0.0",
                        help="Web 监听地址（默认 0.0.0.0，局域网可访问）")
    parser.add_argument("--web-port", type=int, default=6099,
                        help="Web 监听端口（默认 6099）")
    parser.add_argument("--no-web", action="store_true",
                        help="只启动桌面端，不启动 Web 控制台")
    # 其余参数（如 Qt 的 -platform）原样交给 QApplication
    return parser.parse_known_args(argv)


def main(argv: Optional[List[str]] = None) -> None:
    args, qt_args = _parse_args(argv)
    app = QApplication([sys.argv[0]] + list(qt_args))
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft YaHei UI", 10))
    # 调色板兜底：原生控件（占位文字/选择高亮/输入底色）与主题一致
    pal = app.palette()
    pal.setColor(QPalette.ColorRole.PlaceholderText, QColor("#5b6879"))
    pal.setColor(QPalette.ColorRole.Window, QColor(C_BG))
    pal.setColor(QPalette.ColorRole.Base, QColor(C_INPUT_BG))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor(C_PANEL2))
    pal.setColor(QPalette.ColorRole.Text, QColor(C_TEXT))
    pal.setColor(QPalette.ColorRole.WindowText, QColor(C_TEXT))
    pal.setColor(QPalette.ColorRole.Button, QColor(C_PANEL))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(C_TEXT))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(C_BLUE))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#0b1018"))
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor(C_PANEL2))
    pal.setColor(QPalette.ColorRole.ToolTipText, QColor(C_TEXT))
    app.setPalette(pal)
    win = MainWindow()
    if not args.no_web:
        # Web 面板与桌面端共用同一批机器人：浏览器里的操作直接驱动本窗口
        try:
            httpd, url = webui.start(args.web_host, args.web_port,
                                     manager=DesktopWebManager(win))
        except OSError as exc:
            win.log("system",
                    f"[系统] ❌ Web 控制台启动失败（{args.web_host}:"
                    f"{args.web_port}）：{exc}")
        else:
            win.log("system", f"[系统] 🌐 Web 控制台已启动：{url}")
            app.aboutToQuit.connect(lambda: webui.stop(httpd))
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
