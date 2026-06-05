"""键盘监听模块。

本模块提供跨平台的键盘事件监听功能，支持 Unix 和 Windows 系统。
通过 KeyboardListener 类可以异步获取键盘输入事件，包括方向键、
回车键、Esc键、Tab键以及数字键等。

主要组件：
    - KeyEvent: 键盘事件枚举类型
    - KeyboardListener: 异步键盘监听器类
    - listen_for_keyboard: 便捷的键盘监听异步生成器函数

使用示例：
    async for event in listen_for_keyboard():
        print(event)
"""

from __future__ import annotations

import asyncio
import sys
import threading
import time
from collections.abc import AsyncGenerator, Callable
from enum import Enum, auto

from novel_cli.utils.aioqueue import Queue


class KeyEvent(Enum):
    """键盘事件枚举类。

    定义所有可识别的键盘事件类型，用于表示用户按键操作。

    Attributes:
        UP: 上方向键事件。
        DOWN: 下方向键事件。
        LEFT: 左方向键事件。
        RIGHT: 右方向键事件。
        ENTER: 回车键事件。
        ESCAPE: Esc键事件。
        TAB: Tab键事件。
        SPACE: 空格键事件。
        CTRL_E: Ctrl+E 组合键事件。
        NUM_1: 数字键 1 事件。
        NUM_2: 数字键 2 事件。
        NUM_3: 数字键 3 事件。
        NUM_4: 数字键 4 事件。
        NUM_5: 数字键 5 事件。
        NUM_6: 数字键 6 事件。
    """

    UP = auto()
    DOWN = auto()
    LEFT = auto()
    RIGHT = auto()
    ENTER = auto()
    ESCAPE = auto()
    TAB = auto()
    SPACE = auto()
    CTRL_E = auto()
    NUM_1 = auto()
    NUM_2 = auto()
    NUM_3 = auto()
    NUM_4 = auto()
    NUM_5 = auto()
    NUM_6 = auto()


class KeyboardListener:
    """异步键盘监听器类。

    在独立线程中监听键盘输入，并通过异步队列将事件传递给主协程。
    支持暂停和恢复监听功能，适用于需要临时禁用键盘输入的场景。

    Attributes:
        _queue: 异步事件队列，存储接收到的键盘事件。
        _cancel_event: 取消事件，用于停止监听线程。
        _pause_event: 暂停事件，用于暂停监听线程。
        _paused_event: 已暂停事件，用于确认暂停状态。
        _listener: 监听线程对象。
        _loop: 当前 asyncio 事件循环引用。
    """

    def __init__(self) -> None:
        """初始化键盘监听器。"""
        self._queue = Queue[KeyEvent]()
        self._cancel_event = threading.Event()
        self._pause_event = threading.Event()
        self._paused_event = threading.Event()
        self._listener: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    async def start(self) -> None:
        """启动键盘监听。

        创建并启动监听线程，开始接收键盘事件。
        如果监听器已启动，则不执行任何操作。
        """
        if self._listener is not None:
            return
        self._loop = asyncio.get_running_loop()

        def emit(event: KeyEvent) -> None:
            if self._loop is None:
                return
            self._loop.call_soon_threadsafe(self._queue.put_nowait, event)

        self._listener = threading.Thread(
            target=_listen_for_keyboard_thread,
            args=(self._cancel_event, self._pause_event, self._paused_event, emit),
            name="novel-cli-keyboard-listener",
            daemon=True,
        )
        self._listener.start()

    async def stop(self) -> None:
        """停止键盘监听。

        设置取消事件并等待监听线程结束。
        """
        self._cancel_event.set()
        self._pause_event.clear()
        if self._listener and self._listener.is_alive():
            await asyncio.to_thread(self._listener.join)

    def _pause_sync(self) -> None:
        """同步方式暂停监听。

        设置暂停事件并等待暂停确认。
        """
        self._pause_event.set()
        self._paused_event.wait()

    async def pause(self) -> None:
        """暂停键盘监听。

        通过同步方法在独立线程中执行暂停操作。
        适用于需要临时禁用键盘输入的场景（如显示 pager）。
        """
        await asyncio.to_thread(self._pause_sync)

    def _resume_sync(self) -> None:
        """同步方式恢复监听。

        清除暂停事件并等待监听线程恢复运行。
        """
        self._pause_event.clear()
        while self._paused_event.is_set() and not self._cancel_event.is_set():
            time.sleep(0.01)

    async def resume(self) -> None:
        """恢复键盘监听。

        通过同步方法在独立线程中执行恢复操作。
        """
        await asyncio.to_thread(self._resume_sync)

    async def get(self) -> KeyEvent:
        """获取下一个键盘事件。

        从异步队列中等待并返回下一个键盘事件。

        Returns:
            KeyEvent: 接收到的键盘事件。
        """
        return await self._queue.get()


async def listen_for_keyboard() -> AsyncGenerator[KeyEvent]:
    """键盘监听异步生成器。

    创建键盘监听器并持续生成键盘事件，在退出时自动停止监听。

    Yields:
        KeyEvent: 每次用户按键时产生的键盘事件。

    示例:
        async for event in listen_for_keyboard():
            if event == KeyEvent.ENTER:
                print("用户按下回车键")
    """
    listener = KeyboardListener()
    await listener.start()

    try:
        while True:
            yield await listener.get()
    finally:
        await listener.stop()


def _listen_for_keyboard_thread(
    cancel: threading.Event,
    pause: threading.Event,
    paused: threading.Event,
    emit: Callable[[KeyEvent], None],
) -> None:
    """键盘监听线程入口函数。

    根据当前平台选择对应的键盘监听实现。

    Args:
        cancel: 取消事件，用于停止监听。
        pause: 暂停事件，用于暂停监听。
        paused: 已暂停事件，用于确认暂停状态。
        emit: 事件发射回调函数。
    """
    if sys.platform == "win32":
        _listen_for_keyboard_windows(cancel, pause, paused, emit)
    else:
        _listen_for_keyboard_unix(cancel, pause, paused, emit)


def _listen_for_keyboard_unix(
    cancel: threading.Event,
    pause: threading.Event,
    paused: threading.Event,
    emit: Callable[[KeyEvent], None],
) -> None:
    """Unix 平台键盘监听实现。

    使用 termios 模块将终端设置为原始模式，直接读取字符输入。
    支持识别方向键序列和其他特殊按键。

    Args:
        cancel: 取消事件，用于停止监听。
        pause: 暂停事件，用于暂停监听。
        paused: 已暂停事件，用于确认暂停状态。
        emit: 事件发射回调函数。

    Raises:
        RuntimeError: 在 Windows 平台上调用时抛出。
    """
    if sys.platform == "win32":
        raise RuntimeError("Unix keyboard listener requires a non-Windows platform")

    import termios

    fd = sys.stdin.fileno()
    oldterm = termios.tcgetattr(fd)
    rawattr = termios.tcgetattr(fd)
    rawattr[3] = rawattr[3] & ~termios.ICANON & ~termios.ECHO
    rawattr[6][termios.VMIN] = 0
    rawattr[6][termios.VTIME] = 0
    raw_enabled = False

    def enable_raw() -> None:
        """启用终端原始模式。"""
        nonlocal raw_enabled
        if raw_enabled:
            return
        termios.tcsetattr(fd, termios.TCSANOW, rawattr)
        raw_enabled = True

    def disable_raw() -> None:
        """禁用终端原始模式，恢复默认设置。"""
        nonlocal raw_enabled
        if not raw_enabled:
            return
        termios.tcsetattr(fd, termios.TCSANOW, oldterm)
        raw_enabled = False

    enable_raw()

    try:
        while not cancel.is_set():
            if pause.is_set():
                disable_raw()
                paused.set()
                time.sleep(0.01)
                continue
            if paused.is_set():
                paused.clear()
                enable_raw()

            try:
                c = sys.stdin.buffer.read(1)
            except (OSError, ValueError):
                c = b""

            if not c:
                if cancel.is_set():
                    break
                time.sleep(0.01)
                continue

            if c == b"\x1b":
                sequence = c
                for _ in range(2):
                    if cancel.is_set():
                        break
                    try:
                        fragment = sys.stdin.buffer.read(1)
                    except (OSError, ValueError):
                        fragment = b""
                    if not fragment:
                        break
                    sequence += fragment
                    if sequence in _ARROW_KEY_MAP:
                        break

                event = _ARROW_KEY_MAP.get(sequence)
                if event is not None:
                    emit(event)
                elif sequence == b"\x1b":
                    emit(KeyEvent.ESCAPE)
            elif c in (b"\r", b"\n"):
                emit(KeyEvent.ENTER)
            elif c == b" ":
                emit(KeyEvent.SPACE)
            elif c == b"\t":
                emit(KeyEvent.TAB)
            elif c == b"\x05":  # Ctrl+E
                emit(KeyEvent.CTRL_E)
            elif c == b"1":
                emit(KeyEvent.NUM_1)
            elif c == b"2":
                emit(KeyEvent.NUM_2)
            elif c == b"3":
                emit(KeyEvent.NUM_3)
            elif c == b"4":
                emit(KeyEvent.NUM_4)
            elif c == b"5":
                emit(KeyEvent.NUM_5)
            elif c == b"6":
                emit(KeyEvent.NUM_6)
    finally:
        termios.tcsetattr(fd, termios.TCSAFLUSH, oldterm)


def _listen_for_keyboard_windows(
    cancel: threading.Event,
    pause: threading.Event,
    paused: threading.Event,
    emit: Callable[[KeyEvent], None],
) -> None:
    """Windows 平台键盘监听实现。

    使用 msvcrt 模块读取键盘输入，支持识别扩展键（方向键等）。

    Args:
        cancel: 取消事件，用于停止监听。
        pause: 暂停事件，用于暂停监听。
        paused: 已暂停事件，用于确认暂停状态。
        emit: 事件发射回调函数。

    Raises:
        RuntimeError: 在非 Windows 平台上调用时抛出。
    """
    if sys.platform != "win32":
        raise RuntimeError("Windows keyboard listener requires a Windows platform")

    import msvcrt

    while not cancel.is_set():
        if pause.is_set():
            paused.set()
            time.sleep(0.01)
            continue
        if paused.is_set():
            paused.clear()

        if msvcrt.kbhit():
            c = msvcrt.getch()

            # 处理特殊按键（方向键等）
            if c in (b"\x00", b"\xe0"):
                # 扩展键，读取下一字节
                extended = msvcrt.getch()
                event = _WINDOWS_KEY_MAP.get(extended)
                if event is not None:
                    emit(event)
            elif c == b"\x1b":
                sequence = c
                for _ in range(2):
                    if cancel.is_set():
                        break
                    fragment = msvcrt.getch() if msvcrt.kbhit() else b""
                    if not fragment:
                        break
                    sequence += fragment
                    if sequence in _ARROW_KEY_MAP:
                        break

                event = _ARROW_KEY_MAP.get(sequence)
                if event is not None:
                    emit(event)
                elif sequence == b"\x1b":
                    emit(KeyEvent.ESCAPE)
            elif c in (b"\r", b"\n"):
                emit(KeyEvent.ENTER)
            elif c == b" ":
                emit(KeyEvent.SPACE)
            elif c == b"\t":
                emit(KeyEvent.TAB)
            elif c == b"\x05":  # Ctrl+E
                emit(KeyEvent.CTRL_E)
            elif c == b"1":
                emit(KeyEvent.NUM_1)
            elif c == b"2":
                emit(KeyEvent.NUM_2)
            elif c == b"3":
                emit(KeyEvent.NUM_3)
            elif c == b"4":
                emit(KeyEvent.NUM_4)
            elif c == b"5":
                emit(KeyEvent.NUM_5)
            elif c == b"6":
                emit(KeyEvent.NUM_6)
        else:
            if cancel.is_set():
                break
            time.sleep(0.01)


# 方向键 ANSI 序列映射表
_ARROW_KEY_MAP: dict[bytes, KeyEvent] = {
    b"\x1b[A": KeyEvent.UP,
    b"\x1b[B": KeyEvent.DOWN,
    b"\x1b[C": KeyEvent.RIGHT,
    b"\x1b[D": KeyEvent.LEFT,
}

# Windows 扩展键映射表
_WINDOWS_KEY_MAP: dict[bytes, KeyEvent] = {
    b"H": KeyEvent.UP,  # 上方向键
    b"P": KeyEvent.DOWN,  # 下方向键
    b"M": KeyEvent.RIGHT,  # 右方向键
    b"K": KeyEvent.LEFT,  # 左方向键
}


if __name__ == "__main__":

    async def dev_main():
        """开发测试入口。"""
        async for event in listen_for_keyboard():
            print(event)

    asyncio.run(dev_main())