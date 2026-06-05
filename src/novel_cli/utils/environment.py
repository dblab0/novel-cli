"""运行环境检测工具。

本模块提供检测操作系统和 Shell 环境的功能，用于适配不同平台的命令执行。
"""

from __future__ import annotations

import os
import platform
from dataclasses import dataclass
from typing import Literal

from kaos.path import KaosPath


@dataclass(slots=True, frozen=True, kw_only=True)
class Environment:
    """运行环境信息。

    Attributes:
        os_kind: 操作系统类型（Windows、Linux、macOS 或其他）。
        os_arch: 操作系统架构。
        os_version: 操作系统版本。
        shell_name: Shell 名称（bash、sh 或 Windows PowerShell）。
        shell_path: Shell 可执行文件路径。
    """

    os_kind: Literal["Windows", "Linux", "macOS"] | str
    os_arch: str
    os_version: str
    shell_name: Literal["bash", "sh", "Windows PowerShell"]
    shell_path: KaosPath

    @staticmethod
    async def detect() -> Environment:
        """检测当前运行环境。

        Returns:
            包含操作系统和 Shell 信息的 Environment 实例。
        """
        match platform.system():
            case "Darwin":
                os_kind = "macOS"
            case "Windows":
                os_kind = "Windows"
            case "Linux":
                os_kind = "Linux"
            case system:
                os_kind = system

        os_arch = platform.machine()
        os_version = platform.version()

        if os_kind == "Windows":
            shell_name = "Windows PowerShell"
            system_root = os.environ.get("SYSTEMROOT", r"C:\Windows")
            possible_paths = [
                KaosPath(
                    os.path.join(
                        system_root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe"
                    )
                ),
            ]
            fallback_path = KaosPath("powershell.exe")
            for path in possible_paths:
                if await path.is_file():
                    shell_path = path
                    break
            else:
                shell_path = fallback_path
        else:
            possible_paths = [
                KaosPath("/bin/bash"),
                KaosPath("/usr/bin/bash"),
                KaosPath("/usr/local/bin/bash"),
            ]
            fallback_path = KaosPath("/bin/sh")
            for path in possible_paths:
                if await path.is_file():
                    shell_name = "bash"
                    shell_path = path
                    break
            else:
                shell_name = "sh"
                shell_path = fallback_path

        return Environment(
            os_kind=os_kind,
            os_arch=os_arch,
            os_version=os_version,
            shell_name=shell_name,
            shell_path=shell_path,
        )