# McBot-py

Minecraft 多账号机器人（离线模式），Python 重构实现。

## 项目来源

本项目的参考实现是 **Minecraft Console Client (MCC)**——一个 C# 编写的

McBot-py 对照 MCC 的核心原理，用 Python 重新实现：

- **协议实现**：握手 / 登录 / 数据压缩 / 配置阶段 / 游戏阶段，
  支持协议 765（1.20.4）、767（1.21/1.21.1）、769（1.21.4），
  包 ID 调色板按版本查表（对应 MCC 的 `PacketPaletteXXX`）。
- **聊天栏**：聊天组件（JSON/NBT）解析，§ 颜色码渲染为 ANSI 终端颜色。

## 运行环境

- 操作系统：**Windows**（CLI 模式使用 `msvcrt` 读键；GUI 模式跨平台亦可运行）
- Python：**3.12+**
- 依赖：见 [requirements.txt](requirements.txt)
  - `PySide6`：图形界面（Qt6）
  - `psutil`：状态栏系统信息（内存占用等）
  - 协议核心（`client.py` / `network.py` / `datatypes.py`）为**纯标准库**，
    无第三方依赖

安装依赖：

```powershell
pip install -r requirements.txt
```

## 使用方法

### 图形界面（多开机器人）

```powershell
python -m mcbot.gui
```

界面功能：

- **账号管理**：添加/删除账号，勾选后批量「上线所选 / 下线所选」，
  按「上号间隔」逐个连接；每个账号一个独立客户端线程。
- **多线路**：保存 4 条服务器线路，「测速」按钮对当前线路做 DNS 解析 +
  版本握手测速，显示延迟。
- **运行选项**：
  - 掉线自动重连
  - 连接后自动 `/login`（账号密码非空时）
  - 死亡自动重生
- **命令发送**：向所选账号发送聊天内容或命令（如 `/register`）。
- **自动回复**：检测私聊触发词，自动回复文本或执行命令；
  可限定仅主人（masters）触发。
- **日志**：控制台输出实时显示；每个账号的聊天记录写入 `logs/chats/<账号>.log`；
  日志与聊天记录文件超过 **16MB** 时自动舍弃最早的记录，保留末尾约一半继续写入。

配置保存在 `config.json`（与 `mcbot/` 同级），GUI 退出时自动保存，
包含线路、账号、间隔、开关、自动回复规则等。

### 命令行（单账号聊天客户端）

```powershell
python -m mcbot --host <服务器地址> [--port 25565] [--username McBot]
```

- 自动探测服务器版本与在线人数；
- 连接后终端底部固定 `> ` 输入行，聊天消息到达不会顶掉正在输入的内容；
- 输入内容回车发送，`/xxx` 形式作为命令发送；
- `/quit` 或 `Ctrl+C` 退出。

> 注意：CLI 入口依赖 `msvcrt`，仅支持 Windows。

## 目录结构

```
McBot-py/
├── config.json          # GUI 配置（账号/线路/开关/自动回复）
├── requirements.txt     # 第三方依赖
├── logs/                # 运行日志（GUI 日志 + 各账号聊天记录）
└── mcbot/
    ├── __main__.py      # CLI 入口（python -m mcbot）
    ├── gui.py           # PySide6 图形界面（python -m mcbot.gui）
    ├── client.py        # 协议客户端：连接/登录/包处理/自动重生
    ├── network.py       # 套接字 + VarInt 帧 + zlib 压缩
    ├── datatypes.py     # 协议数据类型读写（VarInt/Position/NBT 等）
    ├── components.py    # 1.20.5+ 物品组件解析（预留，当前无调用方）
    ├── versions.py      # 各协议版本的包 ID 调色板
    ├── chat.py          # 聊天组件 JSON 解析与颜色渲染
    ├── auto_reply.py    # 自动回复引擎（触发词/主人名单/命令模板）
    └── item_names.py    # 物品 ID → 中文名映射（预留，当前无调用方）
```

## 支持的协议版本

| 协议号 | 游戏版本 | 说明 |
|--------|----------|------|
| 765    | 1.20.4   | 基线版本 |
| 767    | 1.21.1   | 支持 1.21 / 1.21.1 |
| 769    | 1.21.4   | 最新支持版本 |

服务器协议不在支持列表时，自动回退到最大支持版本（需服务端允许）。
新增版本的方式与 MCC 相同：在 `versions.py` 中加一张包 ID 表即可。
