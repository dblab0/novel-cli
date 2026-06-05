"""Tests for SearchNovel formatter functions."""

from __future__ import annotations

from novel_cli.store.models import (
    CorpusResult,
    CorpusSentence,
    Entity,
    GraphRelation,
    GraphResult,
    RelType,
)
from novel_cli.tools.novel.formatter import (
    MAX_CONTENT_LENGTH,
    format_corpus_result,
    format_entity_result,
    format_graph_result,
)


class TestFormatEntityResult:
    """Tests for format_entity_result."""

    def test_format_entity_result_with_data(
        self, sample_entities: list[Entity]
    ) -> None:
        """Test entity search result formatting with data."""
        result = format_entity_result(sample_entities)

        assert not result.is_error
        assert "找到 2 个实体" in result.output
        assert "凡人修仙传_人物_韩立_0" in result.output
        assert "韩立" in result.output
        assert "匹配度: 0.8500" in result.output

    def test_format_entity_result_empty(self) -> None:
        """Test entity search result formatting when empty."""
        result = format_entity_result([])

        assert not result.is_error  # Empty result is not an error
        assert result.output == "未找到匹配的实体。"
        assert "SearchEntity" in result.message
        assert "SearchCorpus" in result.message

    def test_format_entity_result_long_description(self) -> None:
        """Test entity result truncates long description."""
        long_desc = "这是一个非常长的描述" * 50  # ~300 characters
        entity = Entity(
            id="凡人修仙传_人物_测试_0",
            name="测试",
            type="人物",
            book="凡人修仙传",
            description=long_desc,
            score=0.9,
        )

        result = format_entity_result([entity])

        # Description should be truncated to 200 characters
        assert "..." in result.output
        assert len(result.output) < len(long_desc) + 100

    def test_format_entity_result_without_score(self) -> None:
        """Test entity result without score."""
        entity = Entity(
            id="凡人修仙传_人物_韩立_0",
            name="韩立",
            type="人物",
            book="凡人修仙传",
            description="韩立是凡人修仙传的主角",
            score=None,
        )

        result = format_entity_result([entity])

        assert "匹配度" not in result.output


class TestFormatGraphResult:
    """Tests for format_graph_result."""

    def test_format_graph_types(self, sample_rel_types: list[RelType]) -> None:
        """Test relationship types formatting."""
        data = GraphResult(types=sample_rel_types)
        result = format_graph_result(
            entity_id="凡人修仙传_人物_韩立_0",
            sub_action="types",
            data=data,
        )

        assert not result.is_error
        assert "POSSESS" in result.output
        assert "拥有关系" in result.output
        assert "MENTOR" in result.output
        assert "3 种关系类型" in result.brief

    def test_format_graph_types_empty(self) -> None:
        """Test empty relationship types."""
        data = GraphResult(types=[])
        result = format_graph_result(
            entity_id="凡人修仙传_人物_韩立_0",
            sub_action="types",
            data=data,
        )

        assert not result.is_error
        assert "没有任何关系类型" in result.output

    def test_format_graph_desc(self) -> None:
        """Test entity description formatting."""
        data = GraphResult(description="韩立是凡人修仙传的主角，性格谨慎")
        result = format_graph_result(
            entity_id="凡人修仙传_人物_韩立_0",
            sub_action="desc",
            data=data,
        )

        assert not result.is_error
        assert "韩立是凡人修仙传的主角" in result.output
        assert result.brief == "实体描述"

    def test_format_graph_desc_empty(self) -> None:
        """Test empty description."""
        data = GraphResult(description="")
        result = format_graph_result(
            entity_id="凡人修仙传_人物_韩立_0",
            sub_action="desc",
            data=data,
        )

        assert not result.is_error
        assert "没有描述信息" in result.output

    def test_format_graph_related(
        self, sample_relations: list[GraphRelation]
    ) -> None:
        """Test related entities formatting."""
        data = GraphResult(related=sample_relations)
        result = format_graph_result(
            entity_id="凡人修仙传_人物_韩立_0",
            sub_action="related",
            data=data,
        )

        assert not result.is_error
        assert "凡人修仙传_法宝_掌天瓶_0" in result.output
        assert "POSSESS" in result.output
        assert "2 个相关实体" in result.brief

    def test_format_graph_related_empty(self) -> None:
        """Test empty related entities."""
        data = GraphResult(related=[])
        result = format_graph_result(
            entity_id="凡人修仙传_人物_韩立_0",
            sub_action="related",
            data=data,
        )

        assert not result.is_error
        assert "没有相关实体" in result.output

    def test_format_graph_rels(self, sample_relations: list[GraphRelation]) -> None:
        """Test relationship details formatting."""
        data = GraphResult(rels=sample_relations)
        result = format_graph_result(
            entity_id="凡人修仙传_人物_韩立_0",
            sub_action="rels",
            data=data,
        )

        assert not result.is_error
        assert "凡人修仙传_法宝_掌天瓶_0" in result.output
        assert "韩立拥有掌天瓶" in result.output
        assert "2 条关系详情" in result.brief

    def test_format_graph_rels_empty(self) -> None:
        """Test empty relationship details."""
        data = GraphResult(rels=[])
        result = format_graph_result(
            entity_id="凡人修仙传_人物_韩立_0",
            sub_action="rels",
            data=data,
        )

        assert not result.is_error
        assert "没有关系详情" in result.output


class TestFormatCorpusResult:
    """Tests for format_corpus_result."""

    def test_format_corpus_chapter(self) -> None:
        """Test full chapter formatting."""
        data = CorpusResult(
            text="第173章完整内容...",
            hint="已返回完整章节内容",
            brief="第173章",
        )
        result = format_corpus_result(book="凡人修仙传", data=data)

        assert not result.is_error
        assert "第173章完整内容..." in result.output
        assert "已返回原文内容" in result.message
        assert result.brief == "第173章"

    def test_format_corpus_sentences(self) -> None:
        """Test sentences formatting."""
        sentences = [
            CorpusSentence(sentence_index=10, text="韩立从储物袋中取出"),
            CorpusSentence(sentence_index=11, text="掌天瓶在月光下发光"),
        ]
        data = CorpusResult(sentences=sentences, hint="找到匹配", brief="2条句子")
        result = format_corpus_result(book="凡人修仙传", data=data)

        assert not result.is_error
        assert "10→" in result.output
        assert "11→" in result.output

    def test_format_corpus_truncation(self) -> None:
        """Test content truncation for long text."""
        long_text = "韩立" * 10000  # ~20000 characters, at limit
        data = CorpusResult(text=long_text, hint="章节内容", brief="长章节")
        result = format_corpus_result(book="凡人修仙传", data=data)

        # At limit, should not truncate
        assert not result.is_error
        assert "内容已截断" not in result.output

    def test_format_corpus_truncation_over_limit(self) -> None:
        """Test content truncation when over limit."""
        long_text = "韩立" * 15000  # ~30000 characters, over limit
        data = CorpusResult(text=long_text, hint="章节内容", brief="长章节")
        result = format_corpus_result(book="凡人修仙传", data=data)

        assert not result.is_error
        assert len(result.output) <= MAX_CONTENT_LENGTH + 200  # Truncated + hint
        assert "内容已截断" in result.output
        assert "start" in result.output
        assert "end" in result.output
        assert result.brief == "内容已截断"