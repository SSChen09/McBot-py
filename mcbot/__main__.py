"""McBot 命令行入口：连接服务器并在终端显示聊天栏。

输入栏固定在终端最底部，以 "> " 提示符显示；聊天消息到达时
先清除当前输入行、打印消息，再重绘输入行，输入内容不会被顶掉。
"""
import argparse
import sys
import threading

import msvcrt

from mcbot.client import McBotClient


def _enable_vt() -> None:
    """Windows 下为 stdout 开启虚拟终端序列（清行/颜色）支持。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        k32 = ctypes.windll.kernel32
        handle = k32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint()
        if k32.GetConsoleMode(handle, ctypes.byref(mode)):
            k32.SetConsoleMode(handle, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except Exception:
        pass


class Console:
    """底部固定输入行的终端控制。

    所有输出都经过同一把锁：先清除输入行 → 打印内容 → 重绘 "> {缓冲}"，
    因此后台聊天刷新永远不会顶走正在输入的内容。
    """

    PROMPT = "> "

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buf = ""  # 当前输入缓冲（由主线程写、打印线程读）

    # ---- 输出 ----
    def emit(self, text: str) -> None:
        """打印一行消息（自动保护输入行）。"""
        with self._lock:
            sys.stdout.write("\r\x1b[2K" + text + "\n" + self.PROMPT + self._buf)
            sys.stdout.flush()

    def _redraw(self) -> None:
        """重绘输入行（须持锁调用）。"""
        sys.stdout.write("\r\x1b[2K" + self.PROMPT + self._buf)
        sys.stdout.flush()

    # ---- 输入缓冲操作（仅主线程调用，但与打印共用锁）----
    def append(self, ch: str) -> None:
        with self._lock:
            self._buf += ch
            sys.stdout.write(ch)
            sys.stdout.flush()

    def backspace(self) -> None:
        with self._lock:
            if self._buf:
                self._buf = self._buf[:-1]
                self._redraw()

    def take(self) -> str:
        """取出并清空缓冲，把已输入内容定格为一行。"""
        with self._lock:
            line = self._buf
            self._buf = ""
            sys.stdout.write("\r\x1b[2K" + self.PROMPT + line + "\n")
            sys.stdout.flush()
            return line

    def resume(self) -> None:
        """打印完消息/提示后恢复输入行。"""
        with self._lock:
            self._redraw()


def _read_key() -> str:
    """读取一个按键（Windows: msvcrt），忽略方向键等转义序列。"""
    ch = msvcrt.getwch()
    if ch in ("\x00", "\xe0"):  # 扩展键（方向键等）：吞掉后续码
        msvcrt.getwch()
        return ""
    return ch


def main() -> None:
    parser = argparse.ArgumentParser(description="Minecraft 控制台聊天客户端")
    parser.add_argument("--host", required=True, help="服务器地址")
    parser.add_argument("--port", type=int, default=25565, help="端口（默认 25565）")
    parser.add_argument("--username", default="McBot", help="用户名")
    args = parser.parse_args()

    _enable_vt()
    console = Console()

    # 1. 版本探测（ping_server 是静态方法；connect 内部也会自动探测）
    try:
        info = McBotClient.ping_server(args.host, args.port)
        ver = info["version"]
        players = info["players"]
        print(f"[服务器] {ver.get('name', '?')} (协议 {ver.get('protocol', '?')}) "
              f"在线 {players.get('online', '?')}/{players.get('max', '?')}")
    except Exception:
        print("[警告] 版本探测失败，按服务器实际协议自动选择")

    # 2. 连接（含登录与配置阶段）
    client = McBotClient(
        args.host,
        port=args.port,
        username=args.username,
        on_chat=console.emit,
        on_disconnect=lambda reason: console.emit(f"[断开] {reason}"),
    )
    print(f"[连接] 正在以 {args.username} 登录 {args.host}:{args.port} ...")
    try:
        client.connect()
    except Exception as exc:
        print(f"[失败] {exc}")
        sys.exit(1)
    print("[已连接] 输入聊天内容回车发送，'/quit' 退出，Ctrl+C 亦可退出。")
    console.resume()  # 绘制 "> " 输入行

    # 3. 控制台输入循环（主线程，字符级行编辑 + kbhit 轮询）
    # 轮询而非阻塞读键：断开时 client.running 变 False，循环能立即自动退出
    import time

    try:
        while client.running:
            if not msvcrt.kbhit():
                time.sleep(0.05)
                continue
            ch = _read_key()
            if not ch:
                continue
            if ch in ("\r", "\n"):            # 回车：发送
                line = console.take()
                if not line.strip():
                    continue
                if line in ("/quit", "/exit"):
                    break
                try:
                    if line.startswith("/"):
                        client.send_command(line)
                    else:
                        client.send_chat(line)
                except Exception as exc:
                    console.emit(f"[错误] 发送失败: {exc}")
                    break
            elif ch in ("\x08", "\x7f"):      # 退格
                console.backspace()
            elif ch == "\x03":                # Ctrl+C
                print()
                break
            elif ch >= " ":                   # 可打印字符（含 IME 上屏的中文）
                console.append(ch)
    except KeyboardInterrupt:
        print()
    finally:
        client.disconnect()
        client.join(timeout=3.0)


if __name__ == "__main__":
    main()
