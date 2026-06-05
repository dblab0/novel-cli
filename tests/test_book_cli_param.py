"""测试 --book CLI 参数功能。

测试 NovelCLI.create() 中 book_name 参数的校验逻辑，
包括书名存在/不存在、agent 类型、数据库配置等场景。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from pydantic import SecretStr

import pytest

from novel_cli.app import NovelCLI
from novel_cli.exception import NovelCLIException
from novel_cli.agentspec import NOVEL_AGENT_FILE, DEFAULT_AGENT_FILE
from novel_cli.config import NovelDBConfig


async def test_book_param_with_novel_agent_book_exists(
    config, session
) -> None:
    """测试 --book + novel agent + 书名存在时 state 正确设置。

    验证当使用 novel agent（有小说搜索工具）且指定书名存在于数据库时，
    session.state.current_book 被正确设置。
    """
    # 设置 novel_db 配置以启用数据库连接
    config.services.novel_db = NovelDBConfig(pg_password=SecretStr("test-password"))

    # Mock NovelStore.list_books() 返回包含书名的列表
    with patch("novel_cli.app.NovelStore") as mock_store_class:
        mock_store = AsyncMock()
        mock_store.list_books.return_value = ["凡人修仙传", "遮天", "完美世界"]
        mock_store_class.return_value = mock_store

        # 创建 NovelCLI 实例，指定 novel agent 和书名
        cli = await NovelCLI.create(
            session,
            config=config,
            agent_file=NOVEL_AGENT_FILE,
            book_name="凡人修仙传",
        )

        # 验证 current_book 已被正确设置
        assert cli.session.state.current_book == "凡人修仙传"


async def test_book_param_with_novel_agent_book_not_exists(
    config, session
) -> None:
    """测试 --book + novel agent + 书名不存在时报错退出。

    验证当使用 novel agent 且指定书名不存在于数据库时，
    抛出 NovelCLIException 并包含可用书籍列表信息。
    """
    # 设置 novel_db 配置以启用数据库连接
    config.services.novel_db = NovelDBConfig(pg_password=SecretStr("test-password"))

    # Mock NovelStore.list_books() 返回不包含指定书名的列表
    with patch("novel_cli.app.NovelStore") as mock_store_class:
        mock_store = AsyncMock()
        mock_store.list_books.return_value = ["遮天", "完美世界"]
        mock_store_class.return_value = mock_store

        # 验证抛出 NovelCLIException
        with pytest.raises(NovelCLIException) as exc_info:
            await NovelCLI.create(
                session,
                config=config,
                agent_file=NOVEL_AGENT_FILE,
                book_name="不存在书名",
            )

        # 验证错误消息包含书名和可用书籍列表
        error_msg = str(exc_info.value)
        assert "不存在书名" in error_msg
        assert "遮天" in error_msg
        assert "完美世界" in error_msg


async def test_book_param_with_default_agent_warning(
    config, session
) -> None:
    """测试 --book + default agent（无小说搜索工具）时 warning log 且正常启动。

    验证当使用 default agent（无小说搜索工具）时，
    --book 参数被忽略，logger.warning 被调用，且正常启动无异常。
    """
    # 创建 NovelCLI 实例，使用 default agent（无小说搜索工具）
    with patch("novel_cli.app.logger.warning") as mock_warning:
        cli = await NovelCLI.create(
            session,
            config=config,
            agent_file=DEFAULT_AGENT_FILE,
            book_name="凡人修仙传",
        )

        # 验证 logger.warning 被调用
        mock_warning.assert_called_once()
        call_args = mock_warning.call_args[0][0]
        assert "--book 参数被忽略" in call_args

        # 验证 current_book 保持 None
        assert cli.session.state.current_book is None


async def test_book_param_with_db_not_configured(
    config, session
) -> None:
    """测试 --book + 数据库未配置时报错退出。

    验证当 novel_db.pg_password 为空时，
    抛出 NovelCLIException 并包含特定错误消息。
    """
    # 设置 novel_db 配置，pg_password 为空
    config.services.novel_db = NovelDBConfig(pg_password=SecretStr(""))

    # 验证抛出 NovelCLIException
    with pytest.raises(NovelCLIException) as exc_info:
        await NovelCLI.create(
            session,
            config=config,
            agent_file=NOVEL_AGENT_FILE,
            book_name="凡人修仙传",
        )

    # 验证错误消息
    error_msg = str(exc_info.value)
    assert "小说知识库未配置" in error_msg
    assert "--book" in error_msg


async def test_book_param_overrides_existing_book(
    config, session
) -> None:
    """测试恢复会话 + --book 不同书名时覆盖成功。

    验证当会话已有 current_book="旧书名" 时，
    使用 --book 指定新书名能成功覆盖。
    """
    # 设置 novel_db 配置以启用数据库连接
    config.services.novel_db = NovelDBConfig(pg_password=SecretStr("test-password"))

    # 设置会话已有 current_book="旧书名"
    session.state.current_book = "旧书名"

    # Mock NovelStore.list_books() 返回包含新书名的列表
    with patch("novel_cli.app.NovelStore") as mock_store_class:
        mock_store = AsyncMock()
        mock_store.list_books.return_value = ["新书名", "其他书"]
        mock_store_class.return_value = mock_store

        # 创建 NovelCLI 实例，指定新书名
        cli = await NovelCLI.create(
            session,
            config=config,
            agent_file=NOVEL_AGENT_FILE,
            book_name="新书名",
        )

        # 验证 current_book 已被覆盖为新书名
        assert cli.session.state.current_book == "新书名"