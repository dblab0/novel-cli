"""终端控制工具模块。

提供终端光标位置检测、TTY 状态恢复等功能。
"""

from __future__ import annotations

import contextlib
import os
import re
import sys
import time


def ensure_new_line() -> None:
    """确保下一个提示符从第 0 列开始，无论之前的命令输出如何。"""

    if not sys.stdout.isatty() or not sys.stdin.isatty():
        return

    needs_break = True
    if sys.platform == "win32":
        column = _cursor_column_windows()
        needs_break = column not in (None, 0)
    else:
        column = _cursor_column_unix()
        needs_break = column not in (None, 1)

    if needs_break:
        _write_newline()


def ensure_tty_sane() -> None:
    """恢复基本 TTY 设置，确保原始模式操作后 Ctrl-C 正常工作。"""
    if sys.platform == "win32" or not sys.stdin.isatty():
        return

    try:
        import termios
    except Exception:
        return

    try:
        fd = sys.stdin.fileno()
        attrs = termios.tcgetattr(fd)
    except Exception:
        return

    desired = termios.ISIG | termios.IEXTEN | termios.ICANON | termios.ECHO
    if (attrs[3] & desired) == desired:
        return

    attrs[3] |= desired
    with contextlib.suppress(OSError):
        termios.tcsetattr(fd, termios.TCSADRAIN, attrs)


def _cursor_position_unix() -> tuple[int, int] | None:
    """在 Unix 上获取光标位置（行，列）。两者均为 1 起始索引。

    Returns:
        光标位置元组 (row, column)，若获取失败则返回 None。
    """
    assert sys.platform != "win32"

    import select
    import termios
    import tty

    _CURSOR_QUERY = "\x1b[6n"
    _CURSOR_POSITION_RE = re.compile(r"\x1b\[(\d+);(\d+)R")

    fd = sys.stdin.fileno()
    oldterm = termios.tcgetattr(fd)

    try:
        tty.setcbreak(fd)
        sys.stdout.write(_CURSOR_QUERY)
        sys.stdout.flush()

        response = ""
        deadline = time.monotonic() + 0.2
        while time.monotonic() < deadline:
            timeout = max(0.01, deadline - time.monotonic())
            ready, _, _ = select.select([sys.stdin], [], [], timeout)
            if not ready:
                continue
            try:
                chunk = os.read(fd, 32)
            except OSError:
                break
            if not chunk:
                break
            response += chunk.decode(encoding="utf-8", errors="ignore")
            match = _CURSOR_POSITION_RE.search(response)
            if match:
                return int(match.group(1)), int(match.group(2))
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, oldterm)

    return None


def _cursor_column_unix() -> int | None:
    """在 Unix 上获取光标列位置。

    Returns:
        光标列号（1 起始），若获取失败则返回 None。
    """
    pos = _cursor_position_unix()
    return pos[1] if pos else None


def _cursor_position_windows() -> tuple[int, int] | None:
    """在 Windows 上获取光标位置（行，列）。两者均为 1 起始索引。

    Returns:
        光标位置元组 (row, column)，若获取失败则返回 None。
    """
    assert sys.platform == "win32"

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    _STD_OUTPUT_HANDLE = -11  # Windows API 标准输出句柄常量
    handle = kernel32.GetStdHandle(_STD_OUTPUT_HANDLE)
    invalid_handle_value = ctypes.c_void_p(-1).value
    if handle in (0, invalid_handle_value):
        return None

    class COORD(ctypes.Structure):
        _fields_ = [("X", wintypes.SHORT), ("Y", wintypes.SHORT)]

    class SMALL_RECT(ctypes.Structure):
        _fields_ = [
            ("Left", wintypes.SHORT),
            ("Top", wintypes.SHORT),
            ("Right", wintypes.SHORT),
            ("Bottom", wintypes.SHORT),
        ]

    class CONSOLE_SCREEN_BUFFER_INFO(ctypes.Structure):
        _fields_ = [
            ("dwSize", COORD),
            ("dwCursorPosition", COORD),
            ("wAttributes", wintypes.WORD),
            ("srWindow", SMALL_RECT),
            ("dwMaximumWindowSize", COORD),
        ]

    csbi = CONSOLE_SCREEN_BUFFER_INFO()
    if not kernel32.GetConsoleScreenBufferInfo(handle, ctypes.byref(csbi)):
        return None

    # Windows 返回 0 起始索引，转换为 1 起始以保持一致性
    return int(csbi.dwCursorPosition.Y) + 1, int(csbi.dwCursorPosition.X) + 1


def _cursor_column_windows() -> int | None:
    """在 Windows 上获取光标列位置。

    Returns:
        光标列号（1 起始），若获取失败则返回 None。
    """
    pos = _cursor_position_windows()
    return pos[1] if pos else None


def _write_newline() -> None:
    """向标准输出写入换行符并刷新。"""
    sys.stdout.write("\n")
    sys.stdout.flush()


def get_cursor_row() -> int | None:
    """获取当前光标行号（1 起始索引）。

    Returns:
        光标行号，若无法获取则返回 None。
    """
    if not sys.stdout.isatty() or not sys.stdin.isatty():
        return None

    if sys.platform == "win32":
        pos = _cursor_position_windows()
    else:
        pos = _cursor_position_unix()

    return pos[0] if pos else None


if __name__ == "__main__":
    print("test", end="", flush=True)
    ensure_new_line()
    print("next line")
