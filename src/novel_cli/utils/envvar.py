"""环境变量读取工具。

本模块提供类型安全的环境变量读取功能，支持布尔值和整数的解析。
"""

from __future__ import annotations

import os

# 被视为 True 的字符串值集合
_TRUE_VALUES = {"1", "true", "t", "yes", "y"}


def get_env_bool(name: str, default: bool = False) -> bool:
    """读取布尔类型的环境变量。

    Args:
        name: 环境变量名称。
        default: 环境变量不存在时的默认值。

    Returns:
        解析后的布尔值。如果环境变量值为 "1"、"true"、"t"、"yes" 或 "y"（不区分大小写），
        则返回 True；否则返回 False。
    """
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in _TRUE_VALUES


def get_env_int(name: str, default: int) -> int:
    """读取整数类型的环境变量。

    Args:
        name: 环境变量名称。
        default: 环境变量不存在或解析失败时的默认值。

    Returns:
        解析后的整数值，或默认值。
    """
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default