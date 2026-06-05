"""在子进程中运行 NovelCLI 的工作器模块。

本模块是运行 NovelCLI wire 模式的子进程入口点。
从磁盘读取会话配置并执行 NovelCLI.run_wire_stdio()。

使用方式：
    python -m novel_cli.web.runner.worker <session_id>
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any
from uuid import UUID

from novel_cli import logger
from novel_cli.app import NovelCLI, enable_logging
from novel_cli.cli.mcp import get_global_mcp_config_file
from novel_cli.exception import MCPConfigError
from novel_cli.web.store.sessions import load_session_by_id


async def run_worker(session_id: UUID) -> None:
    """运行指定会话的 NovelCLI 工作器。

    Args:
        session_id: 会话唯一 ID。

    Raises:
        ValueError: 会话不存在时抛出。
    """
    # 通过 Web 存储根据 ID 查找会话
    joint_session = load_session_by_id(session_id)
    if joint_session is None:
        raise ValueError(f"Session not found: {session_id}")

    # 获取 novel-cli 会话对象
    session = joint_session.novel_cli_session

    # 加载默认 MCP 配置文件（如存在）
    default_mcp_file = get_global_mcp_config_file()
    mcp_configs: list[dict[str, Any]] = []
    if default_mcp_file.exists():
        raw = default_mcp_file.read_text(encoding="utf-8")
        try:
            mcp_configs = [json.loads(raw)]
        except json.JSONDecodeError:
            logger.warning(
                "Invalid JSON in MCP config file: {path}",
                path=default_mcp_file,
            )

    # 检测是否为恢复的会话（磁盘上有之前的状态）
    # 与全新的会话（应遵循 config.default_plan_mode）
    resumed = (session.dir / "state.json").exists()

    # 从 session state 读取 agent_file 和 book_name
    agent_file_path = None
    if session.state.agent_file:
        from pathlib import Path as _Path

        agent_file_path = _Path(session.state.agent_file)
        if not agent_file_path.exists():
            logger.error(
                "Agent 文件不存在: {path}，无法启动 Worker",
                path=agent_file_path,
            )
            sys.exit(1)

    book_name = session.state.current_book

    # 创建带 MCP 配置的 NovelCLI 实例
    try:
        novel_cli = await NovelCLI.create(
            session,
            mcp_configs=mcp_configs or None,
            resumed=resumed,
            agent_file=agent_file_path,
            book_name=book_name,
        )
    except MCPConfigError as exc:
        logger.warning(
            "Invalid MCP config in {path}: {error}. Starting without MCP.",
            path=default_mcp_file,
            error=exc,
        )
        novel_cli = await NovelCLI.create(
            session,
            mcp_configs=None,
            resumed=resumed,
            agent_file=agent_file_path,
            book_name=book_name,
        )

    # 以 wire stdio 模式运行
    await novel_cli.run_wire_stdio()


def main() -> None:
    """工作器子进程的入口点。"""
    from novel_cli.utils.proctitle import set_process_title
    from novel_cli.utils.proxy import normalize_proxy_env

    normalize_proxy_env()
    set_process_title("novel-code-worker")

    if len(sys.argv) < 2:
        print("Usage: python -m novel_cli.web.runner.worker <session_id>", file=sys.stderr)
        sys.exit(1)

    try:
        session_id = UUID(sys.argv[1])
    except ValueError:
        print(f"Invalid session ID: {sys.argv[1]}", file=sys.stderr)
        sys.exit(1)

    # 为子进程启用日志
    enable_logging(debug=False)

    # 运行异步工作器
    asyncio.run(run_worker(session_id))


if __name__ == "__main__":
    main()
