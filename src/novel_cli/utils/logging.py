"""标准错误流重定向模块。

将 stderr 重定向到日志系统，用于捕获和记录第三方库的错误输出。
"""

from __future__ import annotations

import codecs
import contextlib
import locale
import os
import sys
import threading
from collections.abc import Iterator
from typing import IO

from novel_cli import logger


class StderrRedirector:
    """标准错误流重定向器。

    将 stderr 内容捕获并转发到日志系统。

    Attributes:
        _level: 日志级别。
        _encoding: 文本编码。
        _installed: 是否已安装重定向。
        _lock: 线程锁。
        _original_fd: 原始 stderr 文件描述符。
        _read_fd: 读取端文件描述符。
        _thread: 消费线程。
    """

    def __init__(self, level: str = "ERROR") -> None:
        """初始化重定向器。

        Args:
            level: 日志级别，默认为 "ERROR"。
        """
        self._level = level
        self._encoding: str | None = None
        self._installed = False
        self._lock = threading.Lock()
        self._original_fd: int | None = None
        self._read_fd: int | None = None
        self._thread: threading.Thread | None = None

    def install(self) -> None:
        """安装 stderr 重定向。

        创建管道并将 stderr 重定向到管道写入端，
        启动后台线程从管道读取端消费数据并记录日志。
        """
        with self._lock:
            if self._installed:
                return
            with contextlib.suppress(Exception):
                sys.stderr.flush()
            if self._original_fd is None:
                with contextlib.suppress(OSError):
                    self._original_fd = os.dup(2)
            if self._encoding is None:
                self._encoding = (
                    sys.stderr.encoding or locale.getpreferredencoding(False) or "utf-8"
                )
            read_fd, write_fd = os.pipe()
            os.dup2(write_fd, 2)
            os.close(write_fd)
            self._read_fd = read_fd
            self._thread = threading.Thread(
                target=self._drain, name="novel-stderr-redirect", daemon=True
            )
            self._thread.start()
            self._installed = True

    def uninstall(self) -> None:
        """卸载 stderr 重定向。

        恢复原始 stderr 并等待消费线程结束。
        """
        with self._lock:
            if not self._installed:
                return
            if self._original_fd is not None:
                os.dup2(self._original_fd, 2)
            self._installed = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _drain(self) -> None:
        """消费管道数据的后台线程主循环。

        从管道读取数据，按行解码并记录日志。
        """
        buffer = ""
        read_fd = self._read_fd
        if read_fd is None:
            return
        encoding = self._encoding or "utf-8"
        decoder = codecs.getincrementaldecoder(encoding)(errors="replace")
        try:
            while True:
                chunk = os.read(read_fd, 4096)
                if not chunk:
                    break
                buffer += decoder.decode(chunk)
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    self._log_line(line)
        except Exception:
            logger.exception("Failed to read redirected stderr")
        finally:
            buffer += decoder.decode(b"", final=True)
            if buffer:
                self._log_line(buffer)
            with contextlib.suppress(OSError):
                os.close(read_fd)

    def _log_line(self, line: str) -> None:
        """记录单行日志。

        Args:
            line: 要记录的日志行。
        """
        text = line.rstrip("\r")
        if not text:
            return
        logger.opt(depth=2).log(self._level, text)

    def open_original_stderr_handle(self) -> IO[bytes] | None:
        """打开原始 stderr 的文件句柄。

        Returns:
            原始 stderr 的二进制写入句柄，如果不存在则返回 None。
        """
        if self._original_fd is None:
            return None
        dup_fd = os.dup(self._original_fd)
        os.set_inheritable(dup_fd, True)
        return os.fdopen(dup_fd, "wb", closefd=True)


_stderr_redirector: StderrRedirector | None = None


def redirect_stderr_to_logger(level: str = "ERROR") -> None:
    """将 stderr 重定向到日志系统。

    Args:
        level: 日志级别，默认为 "ERROR"。
    """
    global _stderr_redirector
    if _stderr_redirector is None:
        _stderr_redirector = StderrRedirector(level=level)
    _stderr_redirector.install()


def restore_stderr() -> None:
    """恢复原始 stderr。

    卸载 stderr 重定向，恢复到系统默认行为。
    """
    if _stderr_redirector is not None:
        _stderr_redirector.uninstall()


@contextlib.contextmanager
def open_original_stderr() -> Iterator[IO[bytes] | None]:
    """上下文管理器，用于访问原始 stderr 句柄。

    Yields:
        原始 stderr 的二进制写入句柄，如果不存在重定向则返回 None。
    """
    redirector = _stderr_redirector
    if redirector is None:
        yield None
        return
    stream = redirector.open_original_stderr_handle()
    try:
        yield stream
    finally:
        if stream is not None:
            stream.close()