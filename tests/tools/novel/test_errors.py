"""Tests for SearchNovel error classes."""

from __future__ import annotations

from inline_snapshot import snapshot

from novel_cli.tools.novel.errors import (
    BookNotFoundError,
    DBConnectionError,
    EntityNotFoundError,
    InvalidEntityIdError,
    MissingParamError,
    SearchNovelError,
)


class TestSearchNovelError:
    """Tests for base SearchNovelError."""

    def test_base_error_creation(self) -> None:
        """Test base error creation."""
        err = SearchNovelError(message="测试错误", brief="测试")

        assert err.message == "测试错误"
        assert err.brief == "测试"
        assert str(err) == "测试错误"

    def test_base_to_tool_error(self) -> None:
        """Test base error to_tool_error conversion."""
        err = SearchNovelError(message="测试错误", brief="测试简短")
        result = err.to_tool_error()

        assert result.is_error
        assert result.message == "测试错误"
        assert result.brief == "测试简短"


class TestMissingParamError:
    """Tests for MissingParamError."""

    def test_missing_query_to_tool_error(self) -> None:
        """Test missing query parameter error."""
        err = MissingParamError(
            message='entity 模式需要提供 query 参数。示例: action="entity", query="韩立"',
            brief="参数缺失: query",
        )
        result = err.to_tool_error()

        assert result.is_error
        assert result.brief == snapshot("参数缺失: query")
        assert "query" in result.message
        assert "示例" in result.message

    def test_missing_sub_action_to_tool_error(self) -> None:
        """Test missing sub_action parameter error."""
        err = MissingParamError(
            message='graph 模式需要提供 sub_action 参数。可选值: '
            'types(关系类型), desc(描述), related(相关实体), rels(关系详情)。'
            '建议先使用 types 查看有哪些关系类型',
            brief="缺少 sub_action",
        )
        result = err.to_tool_error()

        assert result.is_error
        assert result.brief == snapshot("缺少 sub_action")
        assert "types" in result.message
        assert "desc" in result.message

    def test_missing_rel_types_to_tool_error(self) -> None:
        """Test missing rel_types parameter error."""
        err = MissingParamError(
            message='related/rels 操作需要 rel_types 参数。请先使用 sub_action="types" '
            '查询可用的关系类型，再指定需要的类型',
            brief="缺少 rel_types",
        )
        result = err.to_tool_error()

        assert result.is_error
        assert result.brief == snapshot("缺少 rel_types")
        assert "types" in result.message

    def test_missing_entity_id_to_tool_error(self) -> None:
        """Test missing entity_id parameter error."""
        err = MissingParamError(
            message='graph 模式需要提供 entity_id 参数。请先使用 entity 模式搜索获取 entity_id',
            brief="参数缺失: entity_id",
        )
        result = err.to_tool_error()

        assert result.is_error
        assert result.brief == snapshot("参数缺失: entity_id")

    def test_missing_book_to_tool_error(self) -> None:
        """Test missing book parameter error."""
        err = MissingParamError(
            message='corpus 模式需要提供 book 参数。请提供书名进行查询',
            brief="参数缺失: book",
        )
        result = err.to_tool_error()

        assert result.is_error
        assert result.brief == snapshot("参数缺失: book")


class TestEntityNotFoundError:
    """Tests for EntityNotFoundError."""

    def test_entity_not_found_to_tool_error(self) -> None:
        """Test entity not found error."""
        err = EntityNotFoundError(
            message='未找到实体「凡人修仙传_人物_韩非_0」。'
            '可能原因: 1) entity_id 拼写错误 2) 该实体不存在。'
            '建议使用 entity 模式重新搜索',
            brief="实体未找到",
        )
        result = err.to_tool_error()

        assert result.is_error
        assert result.brief == snapshot("实体未找到")
        assert "韩非" in result.message
        assert "entity 模式" in result.message


class TestInvalidEntityIdError:
    """Tests for InvalidEntityIdError."""

    def test_invalid_entity_id_to_tool_error(self) -> None:
        """Test invalid entity_id format error."""
        err = InvalidEntityIdError(
            message='实体ID「韩立」格式不正确。正确格式: 书名_类型_名称_序号，'
            '例如: 凡人修仙传_人物_韩立_0。请先使用 entity 模式搜索获取正确的 entity_id',
            brief="entity_id 格式错误",
        )
        result = err.to_tool_error()

        assert result.is_error
        assert result.brief == snapshot("entity_id 格式错误")
        assert "书名_类型_名称_序号" in result.message
        assert "entity 模式" in result.message


class TestDBConnectionError:
    """Tests for DBConnectionError."""

    def test_db_connection_to_tool_error(self) -> None:
        """Test database connection error."""
        err = DBConnectionError(
            message='小说知识库连接失败: Connection refused。'
            '请检查数据库服务是否启动: '
            'docker compose -f docker/docker-compose.novel-db.yaml up -d',
            brief="数据库连接失败",
        )
        result = err.to_tool_error()

        assert result.is_error
        assert result.brief == snapshot("数据库连接失败")
        assert "docker" in result.message
        assert "Connection refused" in result.message


class TestBookNotFoundError:
    """Tests for BookNotFoundError."""

    def test_book_not_found_to_tool_error(self) -> None:
        """Test book not found error."""
        err = BookNotFoundError(
            message='未找到书名「凡人修真传」。请检查书名是否正确，确保与数据库中的书名完全匹配',
            brief="书名不存在",
        )
        result = err.to_tool_error()

        assert result.is_error
        assert result.brief == snapshot("书名不存在")
        assert "凡人修真传" in result.message
        assert "书名是否正确" in result.message