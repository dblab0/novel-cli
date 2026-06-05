"""代理环境变量规范化工具。

本模块提供代理环境变量的规范化功能，将 socks:// 前缀转换为 socks5://，
以确保 httpx 和 aiohttp 能够正确识别代理配置。
"""

from __future__ import annotations

import os

# 代理相关环境变量名称
_PROXY_ENV_VARS = (
    "ALL_PROXY",
    "all_proxy",
    "HTTP_PROXY",
    "http_proxy",
    "HTTPS_PROXY",
    "https_proxy",
)

_SOCKS_PREFIX = "socks://"
_SOCKS5_PREFIX = "socks5://"


def normalize_proxy_env() -> None:
    """将代理环境变量中的 socks:// 重写为 socks5://。

    许多代理工具（如 V2RayN、Clash 等）设置 ``ALL_PROXY=socks://...``，
    但 httpx 和 aiohttp 仅识别 ``socks5://``。由于 ``socks://`` 实际上是
    ``socks5://`` 的别名，此函数执行安全的原地替换，使下游 HTTP 客户端
    能够正常工作。
    """
    for var in _PROXY_ENV_VARS:
        value = os.environ.get(var)
        if value is not None and value.lower().startswith(_SOCKS_PREFIX):
            os.environ[var] = _SOCKS5_PREFIX + value[len(_SOCKS_PREFIX) :]