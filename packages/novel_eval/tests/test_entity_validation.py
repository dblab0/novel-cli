"""实体验证测试。

测试 _run_entity_validation 函数，使用 mock 替代真实的数据库连接。
"""

from unittest.mock import AsyncMock, MagicMock, patch

from novel_eval.models import EvalCase
from novel_eval.tasks.tool_usage.generator import (
    _run_entity_validation,
    _run_entity_validation_async,
)


class TestRunEntityValidation:
    """_run_entity_validation 同步包装函数测试。"""

    def test_empty_cases(self) -> None:
        """测试空用例列表直接返回。"""
        result = _run_entity_validation([], book="凡人修仙传")
        assert result == []

    @patch("novel_eval.tasks.tool_usage.generator._run_entity_validation_async")
    def test_case_without_entities(self, mock_async: AsyncMock) -> None:
        """测试无 involved_entities 的用例。"""
        cases = [
            EvalCase(
                id="L1-001",
                book="凡人修仙传",
                question="测试问题",
                meta={},
            ),
        ]
        # 模拟异步函数的行为：无实体时设置 entity_hits=0
        updated_cases = [cases[0]]
        updated_cases[0].validation = {
            "entity_hits": 0,
            "top_hit": "",
            "status": "无涉及实体",
        }
        mock_async.return_value = updated_cases

        result = _run_entity_validation(cases, book="凡人修仙传")
        assert len(result) == 1
        assert result[0].validation is not None
        assert result[0].validation["entity_hits"] == 0
        assert result[0].validation["status"] == "无涉及实体"

    @patch("novel_eval.tasks.tool_usage.generator._run_entity_validation_async")
    def test_case_with_empty_entities_list(self, mock_async: AsyncMock) -> None:
        """测试 involved_entities 为空列表的用例。"""
        cases = [
            EvalCase(
                id="L1-001",
                book="凡人修仙传",
                question="测试问题",
                meta={"involved_entities": []},
            ),
        ]
        updated_cases = [cases[0]]
        updated_cases[0].validation = {
            "entity_hits": 0,
            "top_hit": "",
            "status": "无涉及实体",
        }
        mock_async.return_value = updated_cases

        result = _run_entity_validation(cases, book="凡人修仙传")
        assert result[0].validation is not None
        assert result[0].validation["entity_hits"] == 0

    @patch("novel_eval.tasks.tool_usage.generator._run_entity_validation_async")
    def test_delegates_to_async(self, mock_async: AsyncMock) -> None:
        """测试同步函数委托给异步实现。"""
        cases = [
            EvalCase(
                id="L1-001",
                book="凡人修仙传",
                question="韩立是谁？",
                meta={"involved_entities": ["韩立"]},
            ),
        ]
        # 模拟异步函数返回
        mock_async.return_value = cases

        _result = _run_entity_validation(cases, book="凡人修仙传")

        mock_async.assert_called_once_with(cases, "凡人修仙传")


class TestRunEntityValidationAsync:
    """_run_entity_validation_async 异步函数测试。"""

    async def test_successful_entity_search(self) -> None:
        """测试实体搜索成功时正确填充 validation。"""
        # 构造 mock Entity 对象
        mock_entity = MagicMock()
        mock_entity.name = "韩立"
        mock_entity.description = "韩立是凡人修仙传的男主角，修炼长春功"

        # 构造 mock NovelStore
        mock_store = AsyncMock()
        mock_store.search_entities_by_name = AsyncMock(return_value=[mock_entity])
        mock_store.connect = AsyncMock()
        mock_store.close = AsyncMock()

        cases = [
            EvalCase(
                id="L1-001",
                book="凡人修仙传",
                question="韩立是谁？",
                meta={"involved_entities": ["韩立"]},
            ),
        ]

        with patch("novel_cli.store.NovelStore", return_value=mock_store), \
             patch("novel_cli.config.NovelDBConfig", return_value=MagicMock()):
            result = await _run_entity_validation_async(cases, book="凡人修仙传")

        assert len(result) == 1
        validation = result[0].validation
        assert validation is not None
        assert validation["entity_hits"] == 1
        assert validation["status"] == "已验证"
        assert "韩立" in validation["top_hit"]
        # 验证调用了正确的参数
        mock_store.search_entities_by_name.assert_called_once_with(
            name="韩立",
            book="凡人修仙传",
        )

    async def test_multiple_entities_search(self) -> None:
        """测试多实体搜索时累加 entity_hits。"""
        mock_entity_han = MagicMock()
        mock_entity_han.name = "韩立"
        mock_entity_han.description = "男主角"

        mock_entity_nangong = MagicMock()
        mock_entity_nangong.name = "南宫婉"
        mock_entity_nangong.description = "女主角"

        mock_store = AsyncMock()
        mock_store.connect = AsyncMock()
        mock_store.close = AsyncMock()
        # 第一次搜索返回韩立，第二次返回南宫婉
        mock_store.search_entities_by_name = AsyncMock(
            side_effect=[[mock_entity_han], [mock_entity_nangong]]
        )

        cases = [
            EvalCase(
                id="L1-001",
                book="凡人修仙传",
                question="韩立和南宫婉的关系？",
                meta={"involved_entities": ["韩立", "南宫婉"]},
            ),
        ]

        with patch("novel_cli.store.NovelStore", return_value=mock_store), \
             patch("novel_cli.config.NovelDBConfig", return_value=MagicMock()):
            result = await _run_entity_validation_async(cases, book="凡人修仙传")

        validation = result[0].validation
        assert validation is not None
        assert validation["entity_hits"] == 2
        assert validation["status"] == "已验证"
        assert "韩立" in validation["top_hit"]

    async def test_entity_not_found(self) -> None:
        """测试实体未命中时正确标记状态。"""
        mock_store = AsyncMock()
        mock_store.connect = AsyncMock()
        mock_store.close = AsyncMock()
        mock_store.search_entities_by_name = AsyncMock(return_value=[])

        cases = [
            EvalCase(
                id="L1-001",
                book="凡人修仙传",
                question="不存在的人物是谁？",
                meta={"involved_entities": ["不存在的角色"]},
            ),
        ]

        with patch("novel_cli.store.NovelStore", return_value=mock_store), \
             patch("novel_cli.config.NovelDBConfig", return_value=MagicMock()):
            result = await _run_entity_validation_async(cases, book="凡人修仙传")

        validation = result[0].validation
        assert validation is not None
        assert validation["entity_hits"] == 0
        assert validation["status"] == "未命中"

    async def test_connection_failure(self) -> None:
        """测试数据库连接失败时标记验证失败。"""
        mock_store = AsyncMock()
        mock_store.connect = AsyncMock(side_effect=Exception("连接超时"))
        mock_store.close = AsyncMock()

        cases = [
            EvalCase(
                id="L1-001",
                book="凡人修仙传",
                question="韩立是谁？",
                meta={"involved_entities": ["韩立"]},
            ),
        ]

        with patch("novel_cli.store.NovelStore", return_value=mock_store), \
             patch("novel_cli.config.NovelDBConfig", return_value=MagicMock()):
            result = await _run_entity_validation_async(cases, book="凡人修仙传")

        validation = result[0].validation
        assert validation is not None
        assert validation["entity_hits"] == -1
        assert "验证失败" in validation["status"]

    async def test_description_truncated_to_100_chars(self) -> None:
        """测试 description 截断到 100 字符。"""
        long_desc = "这是一个非常长的描述" * 20  # 远超 100 字符

        mock_entity = MagicMock()
        mock_entity.name = "韩立"
        mock_entity.description = long_desc

        mock_store = AsyncMock()
        mock_store.connect = AsyncMock()
        mock_store.close = AsyncMock()
        mock_store.search_entities_by_name = AsyncMock(return_value=[mock_entity])

        cases = [
            EvalCase(
                id="L1-001",
                book="凡人修仙传",
                question="韩立是谁？",
                meta={"involved_entities": ["韩立"]},
            ),
        ]

        with patch("novel_cli.store.NovelStore", return_value=mock_store), \
             patch("novel_cli.config.NovelDBConfig", return_value=MagicMock()):
            result = await _run_entity_validation_async(cases, book="凡人修仙传")

        assert result[0].validation is not None
        top_hit = result[0].validation["top_hit"]
        # top_hit 格式为 "name: desc_preview"，desc 部分不超过 100 字符
        desc_part = top_hit.split(": ", 1)[1]
        assert len(desc_part) <= 100

    async def test_uses_case_book_when_book_param_empty(self) -> None:
        """测试 book 参数为空时使用 case.book 作为回退。"""
        mock_store = AsyncMock()
        mock_store.connect = AsyncMock()
        mock_store.close = AsyncMock()
        mock_store.search_entities_by_name = AsyncMock(return_value=[])

        cases = [
            EvalCase(
                id="L1-001",
                book="凡人修仙传",
                question="韩立是谁？",
                meta={"involved_entities": ["韩立"]},
            ),
        ]

        with patch("novel_cli.store.NovelStore", return_value=mock_store), \
             patch("novel_cli.config.NovelDBConfig", return_value=MagicMock()):
            await _run_entity_validation_async(cases, book="")

        # book 参数为空，应使用 case.book
        mock_store.search_entities_by_name.assert_called_once_with(
            name="韩立",
            book="凡人修仙传",
        )

    async def test_single_entity_search_failure_continues(self) -> None:
        """测试单个实体搜索失败时不影响整体流程。"""
        mock_store = AsyncMock()
        mock_store.connect = AsyncMock()
        mock_store.close = AsyncMock()
        # 第一次搜索抛异常，第二次正常返回
        mock_entity = MagicMock()
        mock_entity.name = "南宫婉"
        mock_entity.description = "女主角"
        mock_store.search_entities_by_name = AsyncMock(
            side_effect=[Exception("搜索异常"), [mock_entity]]
        )

        cases = [
            EvalCase(
                id="L1-001",
                book="凡人修仙传",
                question="韩立和南宫婉？",
                meta={"involved_entities": ["韩立", "南宫婉"]},
            ),
        ]

        with patch("novel_cli.store.NovelStore", return_value=mock_store), \
             patch("novel_cli.config.NovelDBConfig", return_value=MagicMock()):
            result = await _run_entity_validation_async(cases, book="凡人修仙传")

        # 韩立搜索失败，南宫婉搜索成功
        validation = result[0].validation
        assert validation is not None
        assert validation["entity_hits"] == 1
        assert validation["status"] == "已验证"
