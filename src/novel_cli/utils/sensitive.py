"""敏感文件检测工具。

本模块提供检测敏感文件路径的功能，用于保护凭据、私钥等敏感信息不被意外泄露。
仅包含高置信度的敏感文件模式，以降低误报风险。
"""

from __future__ import annotations

import fnmatch
from pathlib import PurePath

# 高置信度的敏感文件模式
# 仅包含误报风险极低的模式
SENSITIVE_PATTERNS: list[str] = [
    # 环境变量 / 密钥文件
    ".env",
    ".env.*",
    # SSH 私钥
    "id_rsa",
    "id_ed25519",
    "id_ecdsa",
    # 云服务凭据（基于路径，也包含纯文件名以处理简化路径场景）
    ".aws/credentials",
    ".gcp/credentials",
    "credentials",
]

# 匹配 .env.* 但不是敏感文件的模板/示例文件
SENSITIVE_EXEMPTIONS: set[str] = {
    ".env.example",
    ".env.sample",
    ".env.template",
}


def is_sensitive_file(path: str) -> bool:
    """检查文件路径是否匹配敏感文件模式。

    Args:
        path: 要检查的文件路径。

    Returns:
        如果匹配敏感文件模式且不在豁免列表中，返回 True；否则返回 False。
    """
    name = PurePath(path).name
    if name in SENSITIVE_EXEMPTIONS:
        return False
    for pattern in SENSITIVE_PATTERNS:
        if "/" in pattern:
            if path.endswith(pattern) or ("/" + pattern) in path:
                return True
        else:
            if fnmatch.fnmatch(name, pattern):
                return True
    return False


def sensitive_file_warning(paths: list[str]) -> str:
    """生成跳过敏感文件的警告消息。

    Args:
        paths: 被跳过的敏感文件路径列表。

    Returns:
        描述跳过敏感文件的警告消息字符串。
    """
    names = sorted({PurePath(p).name for p in paths})
    file_list = ", ".join(names[:5])
    if len(names) > 5:
        file_list += f", ... ({len(names)} files total)"
    return (
        f"Skipped {len(paths)} sensitive file(s) ({file_list}) "
        f"to protect secrets. These files may contain credentials or private keys."
    )