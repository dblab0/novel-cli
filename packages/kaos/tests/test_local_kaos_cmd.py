"""通过 cmd.exe /c 运行命令的 `kaos.exec` 测试。"""

from __future__ import annotations

import asyncio
import os
import platform
from collections.abc import Generator
from pathlib import Path

import pytest
from inline_snapshot import snapshot

import kaos
from kaos import reset_current_kaos, set_current_kaos
from kaos.local import LocalKaos

pytestmark = pytest.mark.skipif(
    platform.system() != "Windows", reason="cmd.exe 测试仅在 Windows 上运行。"
)


@pytest.fixture(autouse=True)
def local_kaos(tmp_path: Path) -> Generator[LocalKaos]:
    """将 LocalKaos 设为当前 KAOS 并为每个测试隔离 cwd。"""
    local = LocalKaos()
    token = set_current_kaos(local)
    old_cwd = Path.cwd()
    os.chdir(tmp_path)
    try:
        yield local
    finally:
        os.chdir(old_cwd)
        reset_current_kaos(token)


async def run_cmd(command: str) -> tuple[int, str, str]:
    """通过 kaos.exec 执行 cmd.exe 命令并收集退出码和流输出。"""
    process = await kaos.exec("cmd.exe", "/c", f"chcp 65001>nul & {command}")
    assert process.stdout is not None
    assert process.stderr is not None

    stdout_task = asyncio.create_task(process.stdout.read())
    stderr_task = asyncio.create_task(process.stderr.read())
    exit_code = await process.wait()
    stdout_data, stderr_data = await asyncio.gather(stdout_task, stderr_task)
    return exit_code, stdout_data.decode("utf-8"), stderr_data.decode("utf-8")


async def test_simple_command():
    """确保基本的 cmd.exe 命令可以运行。"""
    exit_code, stdout, stderr = await run_cmd("echo Hello Windows")

    assert exit_code == 0
    assert stdout.strip() == snapshot("Hello Windows")
    assert stderr == snapshot("")


async def test_command_with_error():
    """失败的命令应返回非零退出码。"""
    exit_code, stdout, stderr = await run_cmd("exit /b 1")

    assert exit_code == 1
    assert stdout == snapshot("")
    assert stderr == snapshot("")


async def test_command_chaining():
    """使用 && 链接命令应该可以工作。"""
    exit_code, stdout, stderr = await run_cmd("echo First&& echo Second")

    assert exit_code == 0
    assert stdout.replace("\r\n", "\n") == snapshot("First\nSecond\n")
    assert stderr == snapshot("")


async def test_file_operations():
    """使用 cmd 重定向进行基本的文件写入/读取。"""
    file_path = Path("test_file.txt")
    exit_code, stdout, stderr = await run_cmd(f"echo Test content> {file_path}")
    assert exit_code == 0
    assert stdout == snapshot("")
    assert stderr == snapshot("")
    assert file_path.is_file()

    exit_code, stdout, stderr = await run_cmd(f"type {file_path}")
    assert exit_code == 0
    assert stdout == snapshot("Test content\r\n")
    assert stderr == snapshot("")
