"""aiohttp 客户端会话工具。

本模块提供创建预配置的 aiohttp 客户端会话的功能，
包含 SSL 证书验证和默认超时设置。
"""

from __future__ import annotations

import ssl

import aiohttp
import certifi

# 使用 certifi 提供的 CA 证书创建 SSL 上下文
_ssl_context = ssl.create_default_context(cafile=certifi.where())

# 默认超时配置
_DEFAULT_TIMEOUT = aiohttp.ClientTimeout(
    total=120,
    sock_read=60,
    sock_connect=15,
)


def new_client_session(
    *,
    timeout: aiohttp.ClientTimeout | None = None,
) -> aiohttp.ClientSession:
    """创建新的 aiohttp 客户端会话。

    使用 certifi 提供的 CA 证书进行 SSL 验证，并配置默认超时。

    Args:
        timeout: 自定义超时配置，如果为 None 则使用默认配置。

    Returns:
        配置好的 aiohttp.ClientSession 实例。
    """
    return aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(ssl=_ssl_context),
        timeout=timeout or _DEFAULT_TIMEOUT,
    )