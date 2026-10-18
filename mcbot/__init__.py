"""McBot-py: Minecraft 控制台客户端的 Python 重构实现。

对照 Minecraft Console Client (MCC) 的核心原理，用纯标准库 Python 实现：
- 连接服务器：握手 / 登录 / 压缩 / 配置阶段 / 游戏阶段
- 聊天栏显示：聊天组件(JSON) 解析、§ 颜色码渲染为 ANSI 终端颜色
"""

__version__ = "0.1.0"
