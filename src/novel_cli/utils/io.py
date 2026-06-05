"""原子化 JSON 写入工具。

本模块提供原子化写入 JSON 文件的功能，通过临时文件和原子重命名操作，
确保在写入过程中断不会损坏原有数据。
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


def atomic_json_write(data: Any, path: Path) -> None:
    """原子化地将 JSON 数据写入文件。

    使用临时文件和 os.replace 实现原子化写入，防止进程崩溃时数据损坏：
    要么保留旧文件完整，要么完全提交新文件。

    Args:
        data: 要写入的 JSON 数据。
        path: 目标文件路径。

    Raises:
        OSError: 文件操作失败时抛出。
    """
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise