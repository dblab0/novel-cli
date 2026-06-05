"""子进程环境处理工具。

本模块提供在 PyInstaller 打包的应用程序中启动子进程时处理环境变量的工具。
主要解决 PyInstaller 引导程序修改 LD_LIBRARY_PATH 导致的库冲突问题，
确保外部程序能够正确加载系统库。

参考：https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html
"""

from __future__ import annotations

import os
import sys

# PyInstaller 在 Linux 上可能修改的环境变量
_PYINSTALLER_LD_VARS = [
    "LD_LIBRARY_PATH",
    "LD_PRELOAD",
]


def get_clean_env(base_env: dict[str, str] | None = None) -> dict[str, str]:
    """获取适合子进程使用的干净环境变量。

    在 PyInstaller 打包的 Linux 应用程序中，此函数恢复原始的库路径环境变量，
    防止子进程加载不兼容的打包库。

    Args:
        base_env: 基础环境变量字典。如果为 None，则使用 os.environ。

    Returns:
        适合子进程使用的环境变量字典。
    """
    env = dict(base_env if base_env is not None else os.environ)

    # 仅在 Linux 上的 PyInstaller 打包环境中处理
    if not getattr(sys, "frozen", False) or sys.platform != "linux":
        return env

    for var in _PYINSTALLER_LD_VARS:
        orig_key = f"{var}_ORIG"
        if orig_key in env:
            # 恢复 PyInstaller 引导程序保存的原始值
            env[var] = env[orig_key]
        elif var in env:
            # 变量在 PyInstaller 修改前不存在，因此删除它
            del env[var]

    return env


def get_noninteractive_env(base_env: dict[str, str] | None = None) -> dict[str, str]:
    """获取用于非交互式子进程的环境变量。

    基于 :func:`get_clean_env` 构建，并额外配置 git 快速失败而非等待用户输入，
    因为用户输入永远不会到来。

    Args:
        base_env: 基础环境变量字典。如果为 None，则使用 os.environ。

    Returns:
        适合非交互式子进程使用的环境变量字典。
    """
    env = get_clean_env(base_env)

    # GIT_TERMINAL_PROMPT=0 使 git 失败而非等待凭据输入
    env.setdefault("GIT_TERMINAL_PROMPT", "0")

    return env