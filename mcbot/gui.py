"""MC 多开机器人 —— Qt6 图形界面（PySide6），真实连接逻辑。

界面：顶栏状态、左侧账号列表（勾选/状态点/分类标签）、右侧操作参数 /
命令发送 / 运行选项 / 控制台输出，底部信息栏。

连接：每个账号对应一个 McBotClient（离线模式），上线所选按上号间隔
逐个连接；聊天实时进入控制台；掉线可自动重连；密码非空时连接成功后
自动发送 /login。
"""
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
from PySide6.QtGui import QFont, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
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
from mcbot.auto_reply import AutoReplyEngine, ReplyRule, parse_private_chat

# ---- 配色 ----
C_BG = "#0d1117"          # 主背景
C_PANEL = "#161b22"       # 面板
C_PANEL2 = "#1c2129"      # 面板内浅层
C_BORDER = "#2b313a"      # 边框
C_TEXT = "#c9d1d9"        # 主文字
C_DIM = "#8b949e"         # 次要文字
C_BLUE = "#2f81f7"        # 主题蓝
C_BLUE_DARK = "#1f6feb"
C_ROW_SEL = "#12324f"     # 选中行背景
C_ROW_SEL_BD = "#264b73"  # 选中行边框
C_GREEN = "#2ea043"       # 在线绿
C_YELLOW = "#d29922"      # 连接中
C_RED_FG = "#f85149"      # 断开红
C_RED = "#a40e26"         # 危险按钮
C_INPUT_BG = "#0d1117"

STYLE = f"""
QWidget {{
    background: {C_BG};
    color: {C_TEXT};
    font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
    font-size: 13px;
}}
QFrame#panel {{
    background: {C_PANEL};
    border: 1px solid {C_BORDER};
    border-radius: 10px;
}}
QLabel {{ background: transparent; }}
QLabel#title    {{ color: {C_TEXT}; font-size: 19px; font-weight: bold; }}
QLabel#dim      {{ color: {C_DIM}; }}
QLabel#section  {{ color: {C_DIM}; font-size: 12px; }}
QLabel#pill {{
    background: {C_PANEL2};
    border: 1px solid {C_BORDER};
    border-radius: 9px;
    padding: 4px 14px;
    color: {C_TEXT};
}}
QLabel#pill-green {{ color: {C_GREEN}; }}
QLabel#tag {{
    color: #79c0ff;
    background: rgba(47, 129, 247, 0.12);
    border-radius: 4px;
    padding: 1px 8px;
    font-size: 12px;
}}
QLabel#tag-gray {{
    color: {C_DIM};
    background: rgba(139, 148, 158, 0.12);
    border-radius: 4px;
    padding: 1px 8px;
    font-size: 12px;
}}
QLabel#dot {{ background: transparent; }}

/* 普通按钮 */
QPushButton {{ background: transparent; color: {C_TEXT}; border: 1px solid {C_BORDER}; border-radius: 6px; padding: 6px 16px; }}
QPushButton:hover  {{ background: #262c36; }}
QPushButton:pressed {{ background: #1a1f27; }}
QPushButton:disabled {{ color: #566070; background: {C_PANEL}; }}

/* 主题蓝按钮 */
QPushButton#primary {{
    background: {C_BLUE};
    border: 1px solid {C_BLUE};
    color: white;
    font-weight: bold;
}}
QPushButton#primary:hover  {{ background: {C_BLUE_DARK}; }}
QPushButton#primary:pressed {{ background: #195bcc; }}
QPushButton#primary:disabled {{ background: #24405f; border-color: #24405f; color: #7d8894; }}

/* 危险红按钮 */
QPushButton#danger {{
    background: {C_RED};
    border: 1px solid #c2323c;
    color: #ffd7d9;
}}
QPushButton#danger:hover {{ background: #b6232c; }}

/* 线路数字按钮 */
QPushButton#line-btn {{
    background: {C_PANEL2};
    border: 1px solid {C_BORDER};
    border-radius: 6px;
    padding: 4px 12px;
    min-width: 26px;
}}
QPushButton#line-btn:checked {{
    background: {C_BLUE};
    border: 1px solid {C_BLUE};
    color: white;
    font-weight: bold;
}}
QPushButton#line-btn[lineOk="true"] {{
    border-bottom: 2px solid {C_GREEN};
}}

/* 控制台模式切换 */
QPushButton#mode-checked {{
    background: {C_BLUE};
    border: 1px solid {C_BLUE};
    color: white;
    font-weight: bold;
}}
QPushButton#mode {{ background: transparent; border: none; color: {C_DIM}; }}

/* 输入框 */
QLineEdit, QSpinBox {{
    background: {C_INPUT_BG};
    border: 1px solid {C_BORDER};
    border-radius: 6px;
    padding: 6px 10px;
    color: {C_TEXT};
}}
QLineEdit:focus {{ border: 1px solid {C_BLUE}; }}
QSpinBox::up-button, QSpinBox::down-button {{ width: 0; border: none; }}

QComboBox {{
    background: {C_INPUT_BG};
    border: 1px solid {C_BORDER};
    border-radius: 6px;
    padding: 6px 10px;
    color: {C_TEXT};
}}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {C_PANEL2};
    border: 1px solid {C_BORDER};
    selection-background-color: {C_ROW_SEL};
    color: {C_TEXT};
}}

/* 复选框 */
QCheckBox {{ background: transparent; spacing: 8px; color: {C_TEXT}; }}
QCheckBox::indicator {{
    width: 18px; height: 18px;
    border-radius: 5px;
    border: 1px solid {C_BORDER};
    background: {C_INPUT_BG};
}}
QCheckBox::indicator:checked {{
    background: {C_BLUE};
    border: 1px solid {C_BLUE};
}}
QCheckBox::indicator:hover {{ border: 1px solid {C_BLUE}; }}

/* 账号行 */
QFrame#row {{ background: rgba(28, 33, 41, 0.55); border: 1px solid {C_BORDER}; border-radius: 8px; }}
QFrame#row-checked {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 rgba(18, 50, 79, 0.55), stop:1 rgba(18, 50, 79, 0.25));
    border: 1px solid {C_ROW_SEL_BD};
    border-radius: 8px;
}}
QLabel#name {{ color: {C_TEXT}; font-size: 14px; }}

/* 滚动区域 */
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: #30363d;
    border-radius: 5px;
    min-height: 30px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}

/* 控制台 */
QPlainTextEdit#console {{
    background: {C_INPUT_BG};
    border: 1px solid {C_BORDER};
    border-radius: 8px;
    padding: 8px;
    font-family: "Consolas", "Microsoft YaHei UI", monospace;
    font-size: 13px;
    color: {C_TEXT};
}}

/* 自动回复规则表 */
QTableWidget {{
    background: {C_INPUT_BG};
    border: 1px solid {C_BORDER};
    border-radius: 6px;
    gridline-color: {C_BORDER};
    color: {C_TEXT};
    selection-background-color: {C_ROW_SEL};
}}
QTableWidget::item {{ padding: 4px; }}

/* 背包槽位 */
QPushButton#slot {{
    background: {C_INPUT_BG};
    border: 1px solid {C_BORDER};
    border-radius: 6px;
    font-size: 11px;
    padding: 2px;
}}
QPushButton#slot:hover {{ border-color: {C_BLUE}; }}
QPushButton#slot:disabled {{ color: #444c56; background: transparent; border-style: dashed; }}
QPushButton#slot:checked {{ border: 2px solid {C_BLUE}; background: {C_ROW_SEL}; color: white; }}

/* 分割条（可拖动调整各区域大小） */
QSplitter::handle {{ background: {C_BORDER}; }}
QSplitter::handle:horizontal {{ width: 6px; }}
QSplitter::handle:vertical   {{ height: 6px; }}
QSplitter::handle:hover {{ background: {C_BLUE}; }}
QHeaderView::section {{
    background: {C_PANEL2};
    color: {C_DIM};
    border: none;
    border-bottom: 1px solid {C_BORDER};
    padding: 5px 8px;
}}
"""

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


class AccountRow(QFrame):
    """单个账号行：勾选框 + 状态点 + 用户名 + 右侧分类标签。"""

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

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)

        self.check = CheckableBox()
        self.check.setChecked(True)
        self.check.stateChanged.connect(self._on_check)
        lay.addWidget(self.check)

        self.dot = make_dot(STATUS_DOT["离线"])
        lay.addWidget(self.dot)

        self.name_label = QLabel(name)
        self.name_label.setObjectName("name")
        lay.addWidget(self.name_label)
        lay.addStretch(1)

        self.tag_label = QLabel(tag)
        self.tag_label.setObjectName("tag-gray" if tag == "空" else "tag")
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
        self.dot.setStyleSheet(
            f"QLabel#dot {{ background: {STATUS_DOT.get(status, '#6e7681')};"
            f" border-radius: 5px; }}")
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
        self.setWindowTitle("MC 多开机器人")
        self.resize(1360, 880)
        self.setMinimumSize(1100, 700)

        self.rows: List[AccountRow] = []
        self.row_index: Dict[str, AccountRow] = {}
        self.line_addresses: Dict[int, str] = {}   # 线路号(1-4) → 地址
        self.current_line = 2
        self._log_lines: List[tuple] = []          # (kind, text)
        self._console_mode = "monitor"             # monitor | console

        # 自动回复引擎（私聊检测 + 规则执行 + 主人权限）
        self.auto_reply = AutoReplyEngine()

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
        root.setContentsMargins(14, 10, 14, 8)
        root.setSpacing(10)

        root.addWidget(self._build_header())

        # 左右两栏用可拖动分割条，自适应窗口大小
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(6)
        splitter.addWidget(self._build_account_panel())
        splitter.addWidget(self._build_right_panel())
        splitter.setStretchFactor(0, 42)
        splitter.setStretchFactor(1, 58)
        splitter.setSizes([560, 760])
        root.addWidget(splitter, 1)

        root.addWidget(self._build_statusbar())

    def _build_header(self) -> QFrame:
        bar = QFrame()
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(10, 6, 6, 6)
        lay.setSpacing(10)

        self.header_dot = make_dot("#6e7681", 12)
        lay.addWidget(self.header_dot)

        title = QLabel("MC 多开机器人")
        title.setObjectName("title")
        lay.addWidget(title)

        self.header_addr = QLabel("未连接服务器")
        self.header_addr.setObjectName("dim")
        lay.addWidget(self.header_addr)
        lay.addStretch(1)

        self.pill_online = QLabel("在线 0/0")
        self.pill_online.setObjectName("pill pill-green")
        lay.addWidget(self.pill_online)

        btn_stop = QPushButton("停止连接")
        btn_stop.setObjectName("danger")
        btn_stop.clicked.connect(self.stop_all)
        lay.addWidget(btn_stop)

        btn_refresh = QPushButton("刷新")
        btn_refresh.clicked.connect(self.refresh_status)
        lay.addWidget(btn_refresh)

        btn_save_cfg = QPushButton("保存配置")
        btn_save_cfg.clicked.connect(self.save_config)
        lay.addWidget(btn_save_cfg)
        return bar

    def _build_account_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("panel")
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        head = QHBoxLayout()
        head.addWidget(QLabel("账号"))
        hint = QLabel("勾选后批量操作")
        hint.setObjectName("dim")
        head.addWidget(hint)
        head.addStretch(1)
        self.selected_label = QLabel("已选 0 / 0")
        self.selected_label.setObjectName("dim")
        head.addWidget(self.selected_label)
        lay.addLayout(head)

        self.list_lay = QVBoxLayout()
        self.list_lay.setSpacing(4)
        self.list_lay.setContentsMargins(0, 0, 4, 0)
        self.list_lay.addStretch(1)   # 行统一靠顶排列
        list_host = QWidget()
        list_host.setLayout(self.list_lay)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(list_host)
        lay.addWidget(scroll, 1)

        lay.addLayout(self._btn_row([
            ("全选", None, self.select_all),
            ("反选", None, self.invert_select),
            ("清空勾选", None, self.clear_select),
            ("上线所选", "primary", self.online_selected),
        ]))
        lay.addLayout(self._btn_row([
            ("下线所选", None, self.offline_selected),
            ("重置重连", None, self.reset_reconnect),
            ("删除所选", "danger", self.delete_selected),
            ("日志文件夹", None, self.open_log_dir),
        ]))
        return panel

    @staticmethod
    def _btn_row(items) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(6)
        for text, obj, slot in items:
            btn = QPushButton(text)
            if obj:
                btn.setObjectName(obj)
            if slot:
                btn.clicked.connect(slot)
            row.addWidget(btn)
        row.addStretch(1)
        return row

    def _build_right_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("panel")
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(10)

        sec = QLabel("操作参数")
        sec.setObjectName("section")
        lay.addWidget(sec)

        # --- 添加账号 ---
        add_row = QHBoxLayout()
        add_row.setSpacing(8)
        add_row.addWidget(QLabel("添加账号"))
        add_row.addWidget(QLabel("账号"))
        self.input_name = QLineEdit()
        self.input_name.setPlaceholderText("填账号名")
        self.input_name.setFixedWidth(150)
        add_row.addWidget(self.input_name)
        add_row.addWidget(QLabel("密码"))
        self.input_pwd = QLineEdit()
        self.input_pwd.setPlaceholderText("填密码")
        self.input_pwd.setEchoMode(QLineEdit.Password)
        self.input_pwd.setFixedWidth(150)
        add_row.addWidget(self.input_pwd)
        btn_add = QPushButton("添加")
        btn_add.setObjectName("primary")
        btn_add.clicked.connect(self.add_account)
        add_row.addWidget(btn_add)
        add_row.addStretch(1)
        lay.addLayout(add_row)

        # --- 线路 ---
        line_row = QHBoxLayout()
        line_row.setSpacing(8)
        line_row.addWidget(QLabel("线路"))
        self.line_buttons: List[QPushButton] = []
        for i in range(1, 5):
            btn = QPushButton(str(i))
            btn.setObjectName("line-btn")
            btn.setCheckable(True)
            btn.clicked.connect(lambda _, idx=i: self._select_line(idx))
            self.line_buttons.append(btn)
            line_row.addWidget(btn)
        self.line_hint = QLabel("")
        self.line_hint.setStyleSheet("color: #7ee787;")
        line_row.addWidget(self.line_hint)
        self.input_addr = QLineEdit()
        self.input_addr.setPlaceholderText("服务器地址 如 mc.example.com:25565")
        self.input_addr.setMinimumWidth(220)
        line_row.addWidget(self.input_addr, 1)
        btn_ping = QPushButton("测速")
        btn_ping.setObjectName("primary")
        btn_ping.clicked.connect(self.ping_line)
        line_row.addWidget(btn_ping)
        btn_save = QPushButton("保存线路")
        btn_save.clicked.connect(self.save_line)
        line_row.addWidget(btn_save)
        lay.addLayout(line_row)
        self.line_buttons[self.current_line - 1].setChecked(True)

        # --- 上号间隔 ---
        gap_row = QHBoxLayout()
        gap_row.setSpacing(8)
        gap_row.addWidget(QLabel("上号间隔"))
        self.spin_gap = QSpinBox()
        self.spin_gap.setRange(1, 300)
        self.spin_gap.setFixedWidth(80)
        gap_row.addWidget(self.spin_gap)
        btn_gap = QPushButton("应用间隔")
        btn_gap.setObjectName("primary")
        btn_gap.clicked.connect(
            lambda: self.bridge.log.emit(
                "system", f"[系统] 上号间隔已设为 {self.spin_gap.value()} 秒", ""))
        gap_row.addWidget(btn_gap)
        gap_row.addWidget(QLabel("秒一个"))
        gap_row.addStretch(1)
        lay.addLayout(gap_row)

        # --- 运行选项 ---
        opt_box = QFrame()
        opt_box.setObjectName("panel")
        opt_lay = QHBoxLayout(opt_box)
        opt_lay.setContentsMargins(14, 10, 14, 10)
        opt_lay.setSpacing(18)
        opt_title = QLabel("运行选项（点一下立即生效）")
        opt_title.setObjectName("section")
        opt_lay.addWidget(opt_title)
        opt_lay.addStretch(1)
        self.cb_reconnect = CheckableBox("掉线自动重连")
        self.cb_reconnect.setChecked(True)
        opt_lay.addWidget(self.cb_reconnect)
        self.cb_login = CheckableBox("连接后自动 /login")
        self.cb_login.setChecked(True)
        opt_lay.addWidget(self.cb_login)
        self.cb_respawn = CheckableBox("死亡自动重生")
        self.cb_respawn.setChecked(True)
        self.cb_respawn.toggled.connect(self._on_respawn_toggle)
        opt_lay.addWidget(self.cb_respawn)
        lay.addWidget(opt_box)

        # --- 自动回复（私聊检测 + 主人权限） ---
        # 上下分割：自动回复在上、控制台在下，可拖动调整各自高度
        vsplit = QSplitter(Qt.Orientation.Vertical)
        vsplit.setChildrenCollapsible(False)
        vsplit.setHandleWidth(6)
        vsplit.addWidget(self._build_auto_reply())

        # --- 控制台输出 ---
        console_box = QFrame()
        console_box.setObjectName("panel")
        cb_lay = QVBoxLayout(console_box)
        cb_lay.setContentsMargins(14, 12, 14, 12)
        cb_lay.setSpacing(8)
        head2 = QHBoxLayout()
        head2.setSpacing(4)
        btn_console = QPushButton("控制台输出")
        btn_console.setObjectName("mode")
        btn_monitor = QPushButton("监 控（聊天）")
        btn_monitor.setObjectName("mode-checked")
        btn_console.clicked.connect(lambda: self._switch_mode("console", btn_console, btn_monitor))
        btn_monitor.clicked.connect(lambda: self._switch_mode("monitor", btn_monitor, btn_console))
        head2.addWidget(btn_console)
        head2.addWidget(btn_monitor)
        head2.addStretch(1)
        cb_lay.addLayout(head2)

        self.console = QPlainTextEdit()
        self.console.setObjectName("console")
        self.console.setReadOnly(True)
        self.console.setMaximumBlockCount(2000)
        cb_lay.addWidget(self.console)
        vsplit.addWidget(console_box)

        vsplit.setStretchFactor(0, 3)
        vsplit.setStretchFactor(1, 5)
        vsplit.setSizes([320, 420])
        lay.addWidget(vsplit, 1)

        # --- 命令发送（面板底部） ---
        cmd_row = QHBoxLayout()
        cmd_row.setSpacing(8)
        self.combo_cmd = QComboBox()
        self.combo_cmd.addItems(["发送到所选"])
        self.combo_cmd.setMinimumWidth(170)
        cmd_row.addWidget(self.combo_cmd)
        self.input_cmd = QLineEdit()
        self.input_cmd.setPlaceholderText("输入要发送到所选账号的内容或命令…")
        cmd_row.addWidget(self.input_cmd, 1)
        btn_send = QPushButton("发送")
        btn_send.setObjectName("primary")
        btn_send.clicked.connect(self.send_command)
        cmd_row.addWidget(btn_send)
        self.input_cmd.returnPressed.connect(self.send_command)
        lay.addLayout(cmd_row)
        return panel

    def _build_auto_reply(self) -> QFrame:
        """自动回复面板：总开关 + 主人名单 + 规则表（增删）。"""
        box = QFrame()
        box.setObjectName("panel")
        v = QVBoxLayout(box)
        v.setContentsMargins(14, 10, 14, 10)
        v.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(10)
        title = QLabel("自动回复（检测私聊 → 回复 / 执行命令）")
        title.setObjectName("section")
        head.addWidget(title)
        self.cb_auto_reply = CheckableBox("启用")
        self.cb_auto_reply.setChecked(False)
        self.cb_auto_reply.toggled.connect(self._on_auto_reply_toggle)
        head.addWidget(self.cb_auto_reply)
        head.addWidget(QLabel("主人（逗号分隔，控制仅主人可触发的规则）"))
        self.input_masters = QLineEdit()
        self.input_masters.setPlaceholderText("如: Steve,Alex")
        self.input_masters.setFixedWidth(260)
        self.input_masters.editingFinished.connect(self._sync_auto_reply)
        head.addWidget(self.input_masters)
        head.addStretch(1)
        v.addLayout(head)

        # 规则表：触发词 / 回复 / 执行命令 / 仅主人
        # 列宽交互式（可拖动表头分隔线调整），初始按比例分配
        self.table_rules = QTableWidget(0, 4)
        self.table_rules.setHorizontalHeaderLabels(
            ["触发词(空=任意私聊)", "回复内容(可含 {name}/{msg})", "执行命令", "仅主人"])
        header = self.table_rules.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setMinimumSectionSize(80)
        header.setStretchLastSection(False)
        self.table_rules.verticalHeader().setVisible(False)
        self.table_rules.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        self.table_rules.setMinimumHeight(90)
        self.table_rules.itemChanged.connect(lambda _: self._sync_auto_reply())
        v.addWidget(self.table_rules, 1)
        # 首次显示后按可用宽度比例分列（保持自适应）
        QTimer.singleShot(0, self._resize_rule_columns)

        btns = QHBoxLayout()
        btns.setSpacing(8)
        btn_add = QPushButton("添加规则")
        btn_add.clicked.connect(self._add_rule_row)
        btns.addWidget(btn_add)
        btn_del = QPushButton("删除所选")
        btn_del.setObjectName("danger")
        btn_del.clicked.connect(self._del_rule_row)
        btns.addWidget(btn_del)
        btns.addStretch(1)
        v.addLayout(btns)
        return box

    def _resize_rule_columns(self) -> None:
        """按可用宽度比例分配规则表前三列（第 4 列按内容自适应）。"""
        vw = self.table_rules.viewport().width()
        if vw <= 0:
            return
        for col, ratio in ((0, 0.22), (1, 0.45), (2, 0.33)):
            self.table_rules.setColumnWidth(col, int(vw * ratio))

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

    def _build_statusbar(self) -> QFrame:
        bar = QFrame()
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(10, 6, 10, 4)
        self.status_left = QLabel("McBot 离线模式 · 未连接服务器")
        self.status_left.setObjectName("dim")
        lay.addWidget(self.status_left)
        lay.addStretch(1)

        # 游戏状态：生命 / 饥饿 / 坐标（取第一个在线账号）
        self.status_game = QLabel("❤ --  🍗 --  坐标 --")
        lay.addWidget(self.status_game)

        sep1 = QLabel("│")
        sep1.setObjectName("dim")
        lay.addWidget(sep1)

        # 服务器延迟
        self.status_ping = QLabel("延迟 -- ms")
        self.status_ping.setObjectName("dim")
        lay.addWidget(self.status_ping)

        sep2 = QLabel("│")
        sep2.setObjectName("dim")
        lay.addWidget(sep2)

        # 系统信息：版本 / 内存 / 网络上下行
        self.status_sys = QLabel(f"{self._sys_version} · 内存 -- · ↓ -- ↑ --")
        self.status_sys.setObjectName("dim")
        self.status_sys.setToolTip("系统版本 · 内存已用/总量 · 网络下行/上行速率")
        lay.addWidget(self.status_sys)

        sep3 = QLabel("│")
        sep3.setObjectName("dim")
        lay.addWidget(sep3)

        self.checked_label = QLabel("已勾选 0 个账号")
        self.checked_label.setObjectName("dim")
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
            hearts = int(hp / 2)
            hunger = int(max(client.food, 0.0) / 2)
            self.status_game.setText(
                f"❤ {hearts} ({hp:.1f})  🍗 {hunger}  "
                f"坐标 {client.x:.1f} / {client.y:.1f} / {client.z:.1f}")
        else:
            self.status_game.setText("❤ --  🍗 --  坐标 --")

        # 延迟显示
        if self._latency_ms is None:
            if self._latency_err:
                self.status_ping.setText(f"延迟 {self._latency_err}")
            elif self._status_tick > 2:
                self.status_ping.setText("延迟 超时")
            else:
                self.status_ping.setText("延迟 -- ms")
        else:
            self.status_ping.setText(f"延迟 {self._latency_ms} ms")

        # 每 2 tick（~1s）：内存 + 网络速率
        if self._status_tick % 2 == 0:
            try:
                mem = psutil.virtual_memory()
                self.status_sys.setText(
                    f"{self._sys_version} · "
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

    def _on_log(self, kind: str, text: str, account: str = "") -> None:
        # 多账号（≥2 在线）时聊天按账号区分显示；单账号时原样显示
        show = text
        if kind == "chat" and account and self._online_count() >= 2:
            show = f"[{account}] {text}"
        self._log_lines.append((kind, show))
        if len(self._log_lines) > 2000:
            self._log_lines = self._log_lines[-1500:]
        if self._console_mode == "monitor" or kind == "system":
            self.console.appendPlainText(show)
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            ts = time.strftime('%H:%M:%S')
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

    def _switch_mode(self, mode: str, active: QPushButton, other: QPushButton) -> None:
        self._console_mode = mode
        active.setObjectName("mode-checked")
        other.setObjectName("mode")
        for w in (active, other):
            w.style().unpolish(w)
            w.style().polish(w)
        # 重建显示
        self.console.clear()
        for kind, text in self._log_lines:
            if mode == "monitor" or kind == "system":
                self.console.appendPlainText(text)

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
        self.header_dot.setStyleSheet(
            f"QLabel#dot {{ background: {C_GREEN}; border-radius: 6px; }}")
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
        self.header_dot.setStyleSheet(
            f"QLabel#dot {{ background: #6e7681; border-radius: 6px; }}")
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
        self.line_hint.setText(saved)

    def save_line(self) -> None:
        addr = self.input_addr.text().strip()
        self.line_addresses[self.current_line] = addr
        self.line_hint.setText(addr)
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
        self.checked_label.setText(f"已勾选 {checked} 个账号")
        self.status_left.setText(
            f"McBot 离线模式 · {self.input_addr.text()} · 在线 {online}/{total}")

    def refresh_status(self) -> None:
        self._refresh_counters()
        self.log("system", "[系统] 状态已刷新")

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
            self.line_hint.setText(addr)
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

    # ---- 退出 ----
    def closeEvent(self, event) -> None:  # noqa: N802
        self.save_config()
        for row in self.rows:
            row.manual_stop = True
            if row.client:
                row.client.disconnect()
        super().closeEvent(event)


def main() -> None:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft YaHei UI", 10))
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
