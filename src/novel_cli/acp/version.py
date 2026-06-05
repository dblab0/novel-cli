"""ACP 协议版本管理模块。

本模块定义了 ACP 协议的版本规格和版本协商机制，用于服务器与客户端之间的协议版本匹配。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ACPVersionSpec:
    """ACP 协议版本规格。

    描述一个支持的 ACP 协议版本，包含协议版本号、规格标签和对应的 SDK 版本。

    Attributes:
        protocol_version: 协议版本号（协商整数，当前为 1）。
        spec_tag: ACP 规格标签（例如 "v0.10.8"）。
        sdk_version: 对应的 SDK 版本（例如 "0.8.0"）。
    """

    protocol_version: int  # 协商整数（当前为 1）
    spec_tag: str  # ACP 规格标签（例如 "v0.10.8"）
    sdk_version: str  # 对应的 SDK 版本（例如 "0.8.0")


# 当前版本规格
CURRENT_VERSION = ACPVersionSpec(
    protocol_version=1,
    spec_tag="v0.10.8",
    sdk_version="0.8.0",
)

# 支持的版本映射表
SUPPORTED_VERSIONS: dict[int, ACPVersionSpec] = {
    1: CURRENT_VERSION,
}

# 最小协议版本号
MIN_PROTOCOL_VERSION = 1


def negotiate_version(client_protocol_version: int) -> ACPVersionSpec:
    """与客户端协商协议版本。

    返回不超过客户端请求版本的最高服务器支持版本。如果客户端版本低于
    ``MIN_PROTOCOL_VERSION``，服务器仍然返回当前版本，以便客户端决定是否断开连接。

    Args:
        client_protocol_version: 客户端请求的协议版本号。

    Returns:
        协商后的 ACP 版本规格。
    """
    if client_protocol_version < MIN_PROTOCOL_VERSION:
        return CURRENT_VERSION

    # 查找最高支持版本（不超过客户端版本）
    best: ACPVersionSpec | None = None
    for ver, spec in SUPPORTED_VERSIONS.items():
        if ver <= client_protocol_version and (best is None or ver > best.protocol_version):
            best = spec

    return best if best is not None else CURRENT_VERSION