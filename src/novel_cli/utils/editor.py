"""外部编辑器工具模块。

提供在 $VISUAL/$EDITOR 中编辑文本的功能。
"""

from __future__ import annotations

import contextlib
import os
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

from novel_cli.utils.logging import logger
from novel_cli.utils.subprocess_env import get_clean_env

# VSCode 需要 --wait 参数以阻塞直到文件关闭。
_EDITOR_CANDIDATES = [
    (["code", "--wait"], "code"),
    (["vim"], "vim"),
    (["vi"], "vi"),
    (["nano"], "nano"),
]
"""编辑器候选列表，包含命令和二进制名称。"""


def get_editor_command(configured: str = "") -> list[str] | None:
    """确定要使用的编辑器命令。

    优先级顺序：*configured*（配置） -> $VISUAL -> $EDITOR -> 自动检测。
    自动检测顺序：code --wait -> vim -> vi -> nano。

    Args:
        configured: 配置文件中指定的编辑器命令。

    Returns:
        编辑器命令列表，如果找不到可用编辑器则返回 None。
    """
    if configured:
        try:
            return shlex.split(configured)
        except ValueError:
            logger.warning("Invalid configured editor value: {}", configured)

    for var in ("VISUAL", "EDITOR"):
        value = os.environ.get(var)
        if value:
            try:
                return shlex.split(value)
            except ValueError:
                logger.warning("Invalid {} value: {}", var, value)
                continue

    for cmd, binary in _EDITOR_CANDIDATES:
        if shutil.which(binary):
            return cmd

    return None


def edit_text_in_editor(text: str, configured: str = "") -> str | None:
    """在外部编辑器中打开文本并返回编辑结果。

    Args:
        text: 要编辑的原始文本。
        configured: 配置文件中指定的编辑器命令。

    Returns:
        编辑后的文本，如果编辑器失败或用户未保存则返回 None。
    """
    editor_cmd = get_editor_command(configured)
    if editor_cmd is None:
        logger.warning("No editor found. Set $VISUAL or $EDITOR.")
        return None

    fd, tmpfile = tempfile.mkstemp(suffix=".md", prefix="novel-edit-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)

        mtime_before = os.path.getmtime(tmpfile)

        try:
            returncode = subprocess.call(editor_cmd + [tmpfile], env=get_clean_env())
        except OSError as exc:
            logger.warning("Failed to launch editor {}: {}", editor_cmd, exc)
            return None

        if returncode != 0:
            logger.warning("Editor exited with non-zero return code: {}", returncode)
            return None

        mtime_after = os.path.getmtime(tmpfile)
        if mtime_after == mtime_before:
            return None

        edited = Path(tmpfile).read_text(encoding="utf-8")
        if edited.endswith("\n"):
            edited = edited[:-1]

        return edited
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmpfile)