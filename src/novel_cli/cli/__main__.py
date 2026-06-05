"""CLI 模块入口点。

提供 `python -m novel_cli.cli` 的直接入口。
"""

from __future__ import annotations

import sys

from novel_cli.cli import cli

if __name__ == "__main__":
    # 规范化代理环境变量
    from novel_cli.utils.proxy import normalize_proxy_env

    normalize_proxy_env()
    sys.exit(cli())
