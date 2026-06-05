"""Tests for CorpusStore."""

from __future__ import annotations

from novel_cli.store.corpus_store import _MAX_CONTENT_LENGTH


class TestCorpusStoreCreation:
    """Tests for CorpusStore creation and constants."""

    def test_corpus_store_creation(self) -> None:
        """Test CorpusStore creation."""
        from unittest.mock import MagicMock

        from novel_cli.store.corpus_store import CorpusStore

        pool = MagicMock()
        store = CorpusStore(pool)

        assert store._pool is pool

    def test_max_content_length_constant(self) -> None:
        """Test max content length constant."""
        assert _MAX_CONTENT_LENGTH == 20000


class TestCorpusStoreQueryDispatch:
    """Tests for CorpusStore query dispatch logic."""

    async def test_query_no_params(self) -> None:
        """Test query with insufficient parameters."""
        from novel_cli.store.corpus_store import CorpusStore
        from unittest.mock import AsyncMock

        pool = AsyncMock()
        store = CorpusStore(pool)

        result = await store.query("凡人修仙传")

        # No chapter_id or keywords
        assert result.hint == "请提供 chapter_id 或 keywords 参数"
        assert result.brief == "缺少查询参数"

    async def test_query_sentence_range_invalid(self) -> None:
        """Test query sentence range with invalid range."""
        from novel_cli.store.corpus_store import CorpusStore
        from unittest.mock import AsyncMock

        pool = AsyncMock()
        store = CorpusStore(pool)

        result = await store._query_sentence_range(
            book="凡人修仙传",
            chapter_id=173,
            sentence_range=[10],  # Only one element
        )

        assert result.hint == "sentence_range 需要 [start, end]"
        assert result.brief == "参数错误"


class TestCorpusResultToText:
    """Tests for CorpusResult.to_text method (via corpus query)."""

    def test_corpus_result_from_sentences(self) -> None:
        """Test CorpusResult with sentences."""
        from novel_cli.store.models import CorpusResult, CorpusSentence

        sentences = [
            CorpusSentence(sentence_index=10, text="韩立从储物袋中取出"),
            CorpusSentence(sentence_index=11, text="掌天瓶发光"),
        ]
        result = CorpusResult(sentences=sentences)

        text = result.to_text()
        assert "10→" in text
        assert "11→" in text
        assert "韩立" in text

    def test_corpus_result_with_text(self) -> None:
        """Test CorpusResult with text."""
        from novel_cli.store.models import CorpusResult

        result = CorpusResult(text="完整章节内容")

        assert result.to_text() == "完整章节内容"

    def test_corpus_result_multi_chapter_separator(self) -> None:
        """多章节时在章节切换处插入分隔标识。"""
        from novel_cli.store.models import CorpusResult, CorpusSentence

        sentences = [
            CorpusSentence(
                sentence_index=23, text="凉水舒畅", document_id=5, title="第五章"
            ),
            CorpusSentence(
                sentence_index=24, text="功法讨论", document_id=5, title="第五章"
            ),
            CorpusSentence(
                sentence_index=0, text="墨大夫密谋", document_id=8, title="第八章"
            ),
            CorpusSentence(
                sentence_index=1, text="余子童出声", document_id=8, title="第八章"
            ),
        ]
        result = CorpusResult(sentences=sentences)
        text = result.to_text()

        assert "--- 第五章 ---" in text
        assert "--- 第八章 ---" in text
        # 章节分隔前有空行
        assert "\n\n--- 第八章 ---" in text
        # 句子索引正常展示
        assert "23→" in text
        assert "0→" in text

    def test_corpus_result_no_document_id_no_separator(self) -> None:
        """句子没有 document_id 时不插入分隔标识。"""
        from novel_cli.store.models import CorpusResult, CorpusSentence

        sentences = [
            CorpusSentence(sentence_index=10, text="第一句"),
            CorpusSentence(sentence_index=11, text="第二句"),
        ]
        result = CorpusResult(sentences=sentences)
        text = result.to_text()

        assert "---" not in text
        assert "10→" in text
        assert "11→" in text