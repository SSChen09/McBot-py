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

### 桌面端 + Web 控制台（同启）

```powershell
python -m mcbot.gui
```

启动后**桌面窗口与 Web 控制台同时可用**：浏览器打开
`http://127.0.0.1:6099/`，同一局域网内可用 `http://<本机IP>:6099/` 访问。
Web 面板与桌面端是**同一个进程、同一批机器人**——浏览器里的账号状态、
上下线、命令发送、自动回复改动都会直接作用于桌面端的账号，不会重复登录。

可选参数：

```powershell
python -m mcbot.gui --web-port 7000          # 改 Web 端口
python -m mcbot.gui --web-host 127.0.0.1     # 只允许本机访问
python -m mcbot.gui --no-web                 # 只要桌面端，不启动 Web
```

桌面窗口右上角「Web 密钥」可生成登录密钥；启动时控制台会打印
`🌐 Web 控制台已启动：http://…` 一行，端口被占用时会提示失败原因但桌面端照常运行。

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
- **私聊传送点**：主人私聊 `sethome <名称>` 后，机器人会 `/tpa` 到你身边，
  到达后执行 `/sethome <名称>` 保存传送点并 `/home home` 回到出生点；
  私聊 `goto <名称>` 后，机器人会 `/home <名称>` 前往传送点，再 `/tpahere`
  邀请你，你接受后机器人 `/home home` 回到出生点。名称到序号的映射保存在
  `home.json`（与 `config.json` 同级，按账号分组、自动创建），两个功能都只对
  主人名单生效。
- **日志**：控制台输出实时显示；每个账号的聊天记录写入 `logs/chats/<账号>.log`；
  日志与聊天记录文件超过 **16MB** 时自动舍弃最早的记录，保留末尾约一半继续写入。

配置保存在 `config.json`（与 `mcbot/` 同级），GUI 退出时自动保存，
包含线路、账号、间隔、开关、自动回复规则等。

### Web 界面（浏览器控制台）

桌面端启动时 Web 面板已自动运行（见上一节），无需额外命令。若要在
**没有桌面窗口的机器上**单独运行 Web 面板（例如服务器后台常驻）：

```powershell
python -m mcbot.webui --host 0.0.0.0 --port 6099
```

单独运行时 Web 进程自带一套账号管理（`BotManager`），与桌面端互不共享连接，
只共用 `config.json`；这种情况下**不要让两端同时上线同一账号**，也不要同时
修改配置（后保存的一方会覆盖）。

- 与桌面端同启时共享同一批机器人：账号状态、上下线、命令、自动回复
  改动即时生效，页面每 1.2 秒增量拉取状态与日志。
- 支持账号增删、批量上线/下线、测速、日志查看（全部/聊天监控）、
  命令发送与自动回复编辑。
- 后端仅使用 Python 标准库（`http.server` + `ThreadingHTTPServer`），
  不依赖 PySide6；运行日志写入 `logs/web.log`。

#### 登录密钥

Web 面板必须登录才能查看和操作。密钥只能由**桌面端**生成：

- 桌面端点顶栏「Web 密钥」生成/查看，密钥形如 `ABCD-EFGH-JKLM`，有效 **10 分钟**；
- 浏览器输入密钥登录，登录后页面右上角显示剩余时间，点「续约」可再延长 10 分钟；
- 游戏内私聊机器人 `get`（仅限自动回复主人名单 masters）会以**私聊**回复当前密钥；
- 游戏内私聊 `xy <密钥>` 可续约 10 分钟，回复同样是私聊；
- 重新生成密钥会让旧密钥与已登录的网页立即失效。

密钥保存在 `web_auth.json`（与 `config.json` 同级）；该文件不要提交或外传。
主人名单为空时，游戏内的 `get` / `xy` 命令会被忽略，并私聊提示先配置主人
名单。桌面端同启模式下只有一套账号，游戏内密钥命令由该进程处理；单独运行
Web 面板时则由 Web 进程处理（共用 `mcbot/keycmd.py`）。

#### 游戏内传送点命令

同样只对主人名单生效（与 `get` / `xy` 共用 masters）：

- 私聊 `sethome 家`：机器人回复后向你发送 `/tpa`；你在游戏内接受传送后，
  机器人执行 `/sethome 家` 保存并回复，随后 `/home home` 回到出生点。
  机器人会读取服务器回执：名称被拒绝（如 `错误：无效的家名称！`）时私聊
  告知失败原因，不再误报保存成功。
- 私聊 `goto 家`：机器人执行 `/home 家` 前往已保存的传送点，到达后向你
  发送 `/tpahere`；你接受后机器人 `/home home` 回到出生点。服务器上不存在
  该名称时会私聊提示重新保存。
- `sethome` 等待 `/tpa` 生效 60 秒、`goto` 等待 `/home` 生效 30 秒、等待你接受
  邀请 90 秒；超时会私聊告知并（goto 或已开始保存时）回到出生点。
- 名称不区分大小写，最多 32 个字符且不能含空格；每个账号有独立的一份
  名称 → 序号映射，保存在 `home.json`
  （`{"accounts": {"账号名": {"名称": 序号}}}`）。
- 服务端家名称使用**你给的名称**而不是序号：本服使用的 LiteTeleport 会拒绝
  纯数字家名称（`无效的家名称！`），序号只用于 `home.json` 里的本地编号。

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
├── home.json            # 私聊传送点：按账号保存名称 → 序号（自动创建）
├── web_auth.json        # Web 登录密钥（桌面端生成，自动创建）
├── requirements.txt     # 第三方依赖
├── logs/                # 运行日志（GUI 日志 + 各账号聊天记录）
├── tests/               # 单元测试（python -m unittest）
└── mcbot/
    ├── __main__.py      # CLI 入口（python -m mcbot）
    ├── gui.py           # PySide6 桌面端（python -m mcbot.gui，默认同启 Web）
    ├── theme.py         # 界面主题配色与全局 QSS
    ├── webui.py         # Web 控制台后端（python -m mcbot.webui 可单独运行）
    ├── web_bridge.py    # 同启桥接：Web 请求 → 桌面端账号（Qt 线程投递）
    ├── webauth.py       # Web 登录密钥的生成/校验/续约
    ├── keycmd.py        # 游戏内 get/xy 私聊命令（GUI/Web 共用）
    ├── teleport.py      # 私聊 sethome/goto 传送点流程（GUI/Web 共用）
    ├── web/             # Web 前端（index.html / login.html / style.css / app.js）
    ├── client.py        # 协议客户端：连接/登录/包处理/自动重生
    ├── network.py       # 套接字 + VarInt 帧 + zlib 压缩
    ├── datatypes.py     # 协议数据类型读写（VarInt/UUID/字符串等）
    ├── versions.py      # 各协议版本的包 ID 调色板
    ├── chat.py          # 聊天组件 JSON 解析与颜色渲染
    └── auto_reply.py    # 自动回复引擎（触发词/主人名单/命令模板）
```

## 支持的协议版本

| 协议号 | 游戏版本 | 说明 |
|--------|----------|------|
| 765    | 1.20.4   | 基线版本 |
| 767    | 1.21.1   | 支持 1.21 / 1.21.1 |
| 769    | 1.21.4   | 最新支持版本（1.21.2+ 每 tick 发 `client_tick_end`） |

服务器协议不在支持列表时，自动回退到最大支持版本（需服务端允许）。
新增版本的方式与 MCC 相同：在 `versions.py` 中加一张包 ID 表即可。

> 1.21.2+ 的服务端按 tick 校验移动包：每个客户端 tick 只接受一个带位置的包，
> 收到 `client_tick_end` 才重置计数（Paper 26.3 违反时断开
> `multiplayer.disconnect.invalid_player_movement`）。客户端因此以 20 TPS
> 发送空的 `client_tick_end`，并在每个移动包后同批紧跟一个 `client_tick_end`
> （见 `client.py` 的 `_tick_loop` / `_send_movement`）。
