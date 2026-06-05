"""Tests for store data models."""

from __future__ import annotations

from inline_snapshot import snapshot

from novel_cli.store.models import (
    CorpusResult,
    CorpusSentence,
    Entity,
    GraphRelation,
    GraphResult,
    RelType,
)


class TestEntity:
    """Tests for Entity model."""

    def test_entity_creation(self) -> None:
        """Test basic entity creation."""
        entity = Entity(
            id="凡人修仙传_人物_韩立_0",
            name="韩立",
            type="人物",
            book="凡人修仙传",
            description="韩立是凡人修仙传的主角",
        )

        assert entity.id == "凡人修仙传_人物_韩立_0"
        assert entity.name == "韩立"
        assert entity.type == "人物"
        assert entity.book == "凡人修仙传"
        assert entity.match_type == "vector"
        assert entity.score is None

    def test_entity_with_score(self) -> None:
        """Test entity with score field."""
        entity = Entity(
            id="凡人修仙传_人物_韩立_0",
            name="韩立",
            type="人物",
            book="凡人修仙传",
            description="韩立是凡人修仙传的主角",
            score=0.85,
            match_type="hybrid",
            vector_rank=1,
            name_rank=2,
        )

        assert entity.score == 0.85
        assert entity.match_type == "hybrid"
        assert entity.vector_rank == 1
        assert entity.name_rank == 2

    def test_entity_serialization(self) -> None:
        """Test entity serialization to dict."""
        entity = Entity(
            id="凡人修仙传_人物_韩立_0",
            name="韩立",
            type="人物",
            book="凡人修仙传",
            description="韩立是凡人修仙传的主角",
        )

        data = entity.model_dump()
        assert data == snapshot({
            "id": "凡人修仙传_人物_韩立_0",
            "name": "韩立",
            "type": "人物",
            "book": "凡人修仙传",
            "description": "韩立是凡人修仙传的主角",
            "score": None,
            "match_type": "vector",
            "vector_rank": None,
            "name_rank": None,
            "alias": "",
        })

    def test_entity_model_copy(self) -> None:
        """Test entity model_copy for updating fields."""
        entity = Entity(
            id="凡人修仙传_人物_韩立_0",
            name="韩立",
            type="人物",
            book="凡人修仙传",
            description="韩立是凡人修仙传的主角",
            match_type="vector",
        )

        updated = entity.model_copy(update={"score": 0.9, "match_type": "hybrid"})
        assert updated.score == 0.9
        assert updated.match_type == "hybrid"
        # Original unchanged
        assert entity.score is None
        assert entity.match_type == "vector"


class TestRelType:
    """Tests for RelType model."""

    def test_rel_type_creation(self) -> None:
        """Test basic rel type creation."""
        rel_type = RelType(
            type="POSSESS",
            description="拥有关系",
            category="拥有",
        )

        assert rel_type.type == "POSSESS"
        assert rel_type.description == "拥有关系"
        assert rel_type.category == "拥有"


class TestGraphRelation:
    """Tests for GraphRelation model."""

    def test_graph_relation_creation(self) -> None:
        """Test basic graph relation creation."""
        relation = GraphRelation(
            relationship_type="POSSESS",
            other_node_id="凡人修仙传_法宝_掌天瓶_0",
            description="韩立拥有掌天瓶",
        )

        assert relation.relationship_type == "POSSESS"
        assert relation.other_node_id == "凡人修仙传_法宝_掌天瓶_0"
        assert relation.description == "韩立拥有掌天瓶"

    def test_graph_relation_without_description(self) -> None:
        """Test graph relation without description."""
        relation = GraphRelation(
            relationship_type="USE",
            other_node_id="凡人修仙传_功法_青元剑诀_0",
        )

        assert relation.description == ""


class TestGraphResult:
    """Tests for GraphResult model."""

    def test_graph_result_empty(self) -> None:
        """Test empty graph result."""
        result = GraphResult()

        assert result.types == []
        assert result.description == ""
        assert result.related == []
        assert result.rels == []

    def test_graph_result_with_types(self, sample_rel_types: list[RelType]) -> None:
        """Test graph result with types."""
        result = GraphResult(types=sample_rel_types)

        assert len(result.types) == 3
        assert result.types[0].type == "POSSESS"

    def test_graph_result_with_description(self) -> None:
        """Test graph result with description."""
        result = GraphResult(description="韩立是凡人修仙传的主角")

        assert result.description == "韩立是凡人修仙传的主角"

    def test_graph_result_with_related(
        self, sample_relations: list[GraphRelation]
    ) -> None:
        """Test graph result with related entities."""
        result = GraphResult(related=sample_relations)

        assert len(result.related) == 2
        assert result.related[0].relationship_type == "POSSESS"


class TestCorpusSentence:
    """Tests for CorpusSentence model."""

    def test_corpus_sentence_creation(self) -> None:
        """Test basic corpus sentence creation."""
        sentence = CorpusSentence(
            sentence_index=10,
            text="韩立从储物袋中取出一件物品",
        )

        assert sentence.sentence_index == 10
        assert sentence.text == "韩立从储物袋中取出一件物品"
        assert sentence.document_id is None
        assert sentence.title is None

    def test_corpus_sentence_with_document(self) -> None:
        """Test corpus sentence with document info."""
        sentence = CorpusSentence(
            sentence_index=10,
            text="韩立从储物袋中取出一件物品",
            document_id=173,
            title="第173章",
            source_name="凡人修仙传",
        )

        assert sentence.document_id == 173
        assert sentence.title == "第173章"
        assert sentence.source_name == "凡人修仙传"


class TestCorpusResult:
    """Tests for CorpusResult model."""

    def test_corpus_result_empty(self) -> None:
        """Test empty corpus result."""
        result = CorpusResult()

        assert result.sentences == []
        assert result.text == ""
        assert result.hint == ""
        assert result.brief == ""

    def test_corpus_result_to_text_with_text(self) -> None:
        """Test to_text when text is provided."""
        result = CorpusResult(text="完整章节内容")

        assert result.to_text() == "完整章节内容"

    def test_corpus_result_to_text_with_sentences(
        self, sample_sentences: list[CorpusSentence]
    ) -> None:
        """Test to_text when sentences are provided."""
        result = CorpusResult(sentences=sample_sentences)

        text = result.to_text()
        assert "10→ 韩立从储物袋中取出一件物品" in text
        assert "11→ 掌天瓶在月光下散发出淡淡的光芒" in text
        assert "12→ 他小心翼翼地将灵力注入其中" in text

    def test_corpus_result_to_text_empty(self) -> None:
        """Test to_text when both text and sentences are empty."""
        result = CorpusResult()

        assert result.to_text() == ""

    def test_corpus_result_with_hint(self) -> None:
        """Test corpus result with hint and brief."""
        result = CorpusResult(
            text="章节内容",
            hint="已返回完整章节内容",
            brief="第173章",
        )

        assert result.hint == "已返回完整章节内容"
        assert result.brief == "第173章"