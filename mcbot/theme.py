"""界面主题：配色常量与全局 QSS。

集中放置所有视觉参数，方便后续统一调整；gui.py 只负责控件结构。
"""

# ---- 配色 ----
C_BG = "#0b1018"            # 主背景
C_PANEL = "#131a25"         # 外层面板
C_PANEL2 = "#1a2230"        # 面板内浅层
C_CARD = "#151d29"          # 卡片
C_BORDER = "#26303f"        # 主边框
C_BORDER_SOFT = "#1e2735"   # 卡片描边
C_TEXT = "#e6edf3"          # 主文字
C_DIM = "#8b98a9"           # 次要文字
C_MUTED = "#5f6d80"         # 弱化文字
C_BLUE = "#5b8cff"          # 主题蓝
C_VIOLET = "#8b5cf6"
C_ROW_SEL = "#16283f"       # 选中行背景
C_ROW_SEL_BD = "#31527d"    # 选中行边框
C_GREEN = "#3fb950"         # 在线绿
C_YELLOW = "#d9a441"        # 连接中
C_CYAN = "#39c5cf"          # 私聊/提示
C_RED_FG = "#f85149"        # 断开红
C_INPUT_BG = "#0e131b"      # 输入框背景

AVATAR_COLORS = [
    "#5b8cff", "#8b5cf6", "#2ea043", "#d9a441",
    "#39c5cf", "#e06c9f", "#e3803a", "#6e7c91",
]

STYLE = f"""
* {{ outline: 0; }}

QWidget {{
    color: {C_TEXT};
    font-family: "Microsoft YaHei UI", "Segoe UI", "PingFang SC", sans-serif;
    font-size: 13px;
}}
QWidget#root {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #0c121c, stop:0.5 #0a0e15, stop:1 #0b1018);
}}
QDialog {{
    background: #0d1420;
}}
QLabel {{ background: transparent; }}

/* ---------- 顶栏 ---------- */
QFrame#header {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #18202e, stop:0.55 #131a25, stop:1 #111722);
    border: 1px solid {C_BORDER};
    border-radius: 14px;
}}
QLabel#brand {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #6b95ff, stop:1 {C_VIOLET});
    border-radius: 11px;
    color: white;
    font-size: 14px;
    font-weight: 800;
}}
QLabel#title {{ color: {C_TEXT}; font-size: 20px; font-weight: 700; }}
QLabel#subtitle {{ color: {C_MUTED}; font-size: 11px; }}
QFrame#chip {{
    background: rgba(139, 152, 169, 0.08);
    border: 1px solid {C_BORDER};
    border-radius: 14px;
}}
QFrame#chip[online="yes"] {{
    background: rgba(63, 185, 80, 0.10);
    border: 1px solid rgba(63, 185, 80, 0.30);
}}
QLabel#pillOnline {{
    color: {C_DIM};
    font-size: 12px;
    font-weight: 600;
    padding: 3px 10px 3px 2px;
}}
QFrame#chip[online="yes"] QLabel#pillOnline {{ color: #56d364; }}

/* ---------- 按钮 ---------- */
QPushButton {{
    background: rgba(255, 255, 255, 0.03);
    color: {C_TEXT};
    border: 1px solid {C_BORDER};
    border-radius: 8px;
    padding: 6px 14px;
    min-height: 20px;
}}
QPushButton:hover {{ background: #222c3b; border-color: #3a4a63; }}
QPushButton:pressed {{ background: #1a222e; }}
QPushButton:disabled {{
    color: #4c5a6d;
    background: rgba(255, 255, 255, 0.015);
    border-color: #1d2634;
}}
QPushButton#primary {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #6b95ff, stop:1 #4f7ef5);
    border: 1px solid #6b95ff;
    color: white;
    font-weight: 600;
}}
QPushButton#primary:hover {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #7ba2ff, stop:1 #5a88fa);
    border-color: #8fb2ff;
}}
QPushButton#primary:pressed {{ background: #4d7df2; }}
QPushButton#primary:disabled {{
    background: #26364f;
    border-color: #26364f;
    color: #6f7f94;
}}
QPushButton#danger {{
    background: transparent;
    border: 1px solid rgba(248, 81, 73, 0.35);
    color: #ff8f8a;
}}
QPushButton#danger:hover {{
    background: rgba(248, 81, 73, 0.12);
    border-color: rgba(248, 81, 73, 0.60);
}}
QPushButton#danger:pressed {{ background: rgba(248, 81, 73, 0.20); }}
QPushButton#ghost {{
    background: transparent;
    border: 1px solid transparent;
    color: {C_DIM};
}}
QPushButton#ghost:hover {{ background: rgba(255, 255, 255, 0.05); color: {C_TEXT}; }}

QPushButton#line-btn {{
    background: {C_PANEL2};
    border: 1px solid {C_BORDER};
    border-radius: 8px;
    padding: 4px 0;
    min-width: 30px;
    font-weight: 600;
    color: {C_DIM};
}}
QPushButton#line-btn:hover {{ border-color: #3a4a63; color: {C_TEXT}; }}
QPushButton#line-btn:checked {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #6b95ff, stop:1 #4f7ef5);
    border-color: #6b95ff;
    color: white;
}}
QPushButton#line-btn[lineOk="true"] {{ border-bottom: 2px solid {C_GREEN}; }}

QPushButton#mode {{
    background: transparent;
    border: 1px solid transparent;
    color: {C_DIM};
    padding: 4px 12px;
    font-size: 12px;
    min-height: 16px;
}}
QPushButton#mode:hover {{ color: {C_TEXT}; background: rgba(255, 255, 255, 0.05); }}
QPushButton#mode-checked {{
    background: rgba(91, 140, 255, 0.16);
    border: 1px solid rgba(91, 140, 255, 0.45);
    color: #b9ccff;
    padding: 4px 12px;
    font-size: 12px;
    font-weight: 600;
    min-height: 16px;
}}

/* ---------- 输入控件 ---------- */
QLineEdit, QSpinBox, QComboBox {{
    background: {C_INPUT_BG};
    border: 1px solid #273244;
    border-radius: 8px;
    padding: 6px 10px;
    color: {C_TEXT};
    selection-background-color: {C_BLUE};
    selection-color: white;
    min-height: 20px;
}}
QLineEdit:hover, QSpinBox:hover, QComboBox:hover {{ border-color: #33415a; }}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{
    border-color: {C_BLUE};
    background: #0d141e;
}}
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled {{ color: #4c5a6d; }}
QSpinBox::up-button, QSpinBox::down-button {{ width: 0; border: none; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{
    background: {C_CARD};
    border: 1px solid #33415a;
    border-radius: 8px;
    padding: 4px;
    color: {C_TEXT};
    selection-background-color: {C_ROW_SEL};
    outline: 0;
}}
QComboBox QAbstractItemView::item {{
    min-height: 26px;
    padding: 2px 8px;
    border-radius: 5px;
}}

/* ---------- 复选框 ---------- */
QCheckBox {{ background: transparent; spacing: 8px; color: {C_TEXT}; }}
QCheckBox::indicator {{
    width: 17px; height: 17px;
    border-radius: 5px;
    border: 1px solid #33415a;
    background: {C_INPUT_BG};
}}
QCheckBox::indicator:hover {{ border-color: {C_BLUE}; }}
QCheckBox::indicator:checked {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #6b95ff, stop:1 #4f7ef5);
    border-color: {C_BLUE};
}}
QCheckBox::indicator:disabled {{ border-color: #26303f; background: #111722; }}
QTableView::indicator {{
    width: 15px; height: 15px;
    border-radius: 4px;
    border: 1px solid #33415a;
    background: {C_INPUT_BG};
}}
QTableView::indicator:checked {{ background: {C_BLUE}; border-color: {C_BLUE}; }}

/* ---------- 面板与卡片 ---------- */
QFrame#panel {{
    background: rgba(19, 25, 35, 0.92);
    border: 1px solid {C_BORDER};
    border-radius: 14px;
}}
QFrame#card {{
    background: {C_CARD};
    border: 1px solid {C_BORDER_SOFT};
    border-radius: 12px;
}}
QFrame#cmdbar {{
    background: #101722;
    border: 1px solid {C_BORDER_SOFT};
    border-radius: 10px;
}}
QFrame#divider {{
    background: {C_BORDER_SOFT};
    min-height: 1px;
    max-height: 1px;
    border: none;
}}
QLabel#cardTitle {{ font-size: 14px; font-weight: 600; color: {C_TEXT}; }}
QLabel#cardHint {{ font-size: 11px; color: {C_MUTED}; }}
QLabel#formLabel {{ color: {C_DIM}; font-size: 12px; }}
QLabel#section {{ color: {C_MUTED}; font-size: 11px; font-weight: 600; }}
QLabel#lineHint {{ color: #56d364; font-size: 11px; }}
QLabel#countChip {{
    color: {C_DIM};
    background: rgba(255, 255, 255, 0.05);
    border-radius: 8px;
    padding: 1px 8px;
    font-size: 11px;
}}

/* ---------- 账号行 ---------- */
QFrame#row {{
    background: rgba(21, 29, 41, 0.55);
    border: 1px solid transparent;
    border-radius: 10px;
}}
QFrame#row:hover {{ background: #1a2230; border-color: #2a3648; }}
QFrame#row-checked {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 rgba(22, 40, 63, 0.95), stop:1 rgba(20, 32, 50, 0.55));
    border: 1px solid {C_ROW_SEL_BD};
    border-radius: 10px;
}}
QFrame#row-checked:hover {{ border-color: #3d6394; }}
QLabel#name {{ color: {C_TEXT}; font-size: 14px; font-weight: 600; }}
QLabel#rowSub {{ color: {C_DIM}; font-size: 11px; }}
QLabel#avatar {{ color: white; font-weight: 700; }}
QLabel#tag {{
    color: #9dbcff;
    background: rgba(91, 140, 255, 0.12);
    border: 1px solid rgba(91, 140, 255, 0.18);
    border-radius: 7px;
    padding: 1px 8px;
    font-size: 10px;
}}
QLabel#tag-gray {{
    color: {C_MUTED};
    background: rgba(139, 152, 169, 0.08);
    border: 1px solid rgba(139, 152, 169, 0.12);
    border-radius: 7px;
    padding: 1px 8px;
    font-size: 10px;
}}
QLabel#dot {{ background: transparent; }}

/* ---------- 空状态 ---------- */
QFrame#empty {{
    background: rgba(255, 255, 255, 0.012);
    border: 1px dashed #2b3a4f;
    border-radius: 12px;
}}
QLabel#emptyIcon {{ font-size: 30px; }}
QLabel#emptyTitle {{ color: #b9c5d4; font-size: 13px; font-weight: 600; }}
QLabel#emptyHint {{ color: {C_MUTED}; font-size: 11px; }}

/* ---------- 控制台 ---------- */
QPlainTextEdit#console {{
    background: #090d13;
    border: 1px solid #1d2735;
    border-radius: 10px;
    padding: 10px;
    color: {C_TEXT};
    selection-background-color: #27446b;
    font-family: "Cascadia Mono", "JetBrains Mono", "Consolas", monospace;
    font-size: 12px;
}}

/* ---------- 表格 ---------- */
QTableWidget {{
    background: {C_INPUT_BG};
    alternate-background-color: #111823;
    border: 1px solid #1d2735;
    border-radius: 10px;
    gridline-color: transparent;
    color: {C_TEXT};
    selection-background-color: {C_ROW_SEL};
    selection-color: {C_TEXT};
}}
QTableWidget::item {{ padding: 4px 6px; border: none; }}
QTableWidget::item:selected {{ background: {C_ROW_SEL}; }}
QHeaderView::section {{
    background: #121925;
    color: {C_MUTED};
    border: none;
    border-bottom: 1px solid {C_BORDER_SOFT};
    padding: 6px 8px;
    font-size: 11px;
    font-weight: 600;
}}
QTableCornerButton::section {{ background: #121925; border: none; }}

/* ---------- 滚动条 ---------- */
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{
    background: #2b3648;
    border-radius: 4px;
    min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{ background: #3a4a63; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{
    background: #2b3648;
    border-radius: 4px;
    min-width: 28px;
}}
QScrollBar::handle:horizontal:hover {{ background: #3a4a63; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QAbstractScrollArea::corner {{ background: transparent; }}

/* ---------- 分割条 ---------- */
QSplitter::handle {{ background: transparent; }}
QSplitter::handle:hover {{ background: rgba(91, 140, 255, 0.25); }}
QSplitter::handle:horizontal {{ width: 8px; }}
QSplitter::handle:vertical {{ height: 8px; }}

/* ---------- 状态栏 ---------- */
QFrame#statusbar {{
    background: rgba(9, 13, 19, 0.55);
    border: none;
    border-top: 1px solid #1a2331;
}}
QLabel#statusText {{ color: {C_DIM}; font-size: 11px; }}
QLabel#statusGame {{ color: #c3d1e2; font-size: 11px; }}
QLabel#statusPing {{ color: {C_MUTED}; font-size: 11px; font-weight: 600; }}
QLabel#statusSys {{ color: {C_MUTED}; font-size: 11px; }}
QLabel#statusChecked {{ color: {C_DIM}; font-size: 11px; }}
QLabel#statusMeta {{ color: {C_MUTED}; font-size: 11px; }}
QFrame#vsep {{ background: #232e3e; border: none; }}

QToolTip {{
    background: #1a2331;
    color: {C_TEXT};
    border: 1px solid #33415a;
    padding: 6px 8px;
    border-radius: 6px;
}}
"""
