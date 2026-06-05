"""语料存储模块，通过 PostgreSQL 实现小说全文检索功能。"""

# pyright: reportUnknownVariableType=false
# pyright: reportUnknownMemberType=false
# pyright: reportUnknownArgumentType=false
# pyright: reportUnknownParameterType=false
# pyright: reportMissingImports=false
# pyright: reportAssignmentType=false

from __future__ import annotations

from typing import TYPE_CHECKING

from novel_cli.store.models import CorpusResult, CorpusSentence

if TYPE_CHECKING:
    import asyncpg

_MAX_CONTENT_LENGTH = 20000


class CorpusStore:
    """基于 PostgreSQL 的语料查询类。

    提供章节获取、句子范围查询、关键词搜索等功能。

    Attributes:
        _pool: asyncpg 连接池实例。
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        """初始化语料存储实例。

        Args:
            pool: asyncpg 连接池。
        """
        self._pool = pool

    async def query(
        self,
        book: str,
        chapter_id: int | None = None,
        chapter_ids: list[int] | None = None,
        keywords: list[str] | None = None,
        context_size: int = 1,
        sentence_range: list[int] | None = None,
    ) -> CorpusResult:
        """根据提供的参数分发语料查询。

        参数组合优先级：
        1. keywords + chapter_ids: 关键词搜索（限定章节范围）
        2. keywords: 关键词搜索（全书范围）
        3. chapter_id + sentence_range: 指定句子范围查询
        4. chapter_id: 完整章节获取

        Args:
            book: 书名。
            chapter_id: 章节 ID。
            chapter_ids: 章节 ID 列表，用于关键词搜索限定范围。
            keywords: 关键词列表。
            context_size: 关键词搜索时的上下文句子数量。
            sentence_range: 句子索引范围 [start, end]。

        Returns:
            CorpusResult 对象，包含查询结果和提示信息。
        """
        if keywords:
            return await self._query_keywords(
                book, chapter_ids or [], keywords, context_size
            )
        if chapter_id is not None and sentence_range is not None:
            return await self._query_sentence_range(
                book, chapter_id, sentence_range
            )
        if chapter_id is not None:
            return await self._query_chapter(book, chapter_id)

        return CorpusResult(hint="请提供 chapter_id 或 keywords 参数", brief="缺少查询参数")

    async def _query_chapter(self, book: str, chapter_id: int) -> CorpusResult:
        """获取完整章节内容。

        Args:
            book: 书名。
            chapter_id: 章节号（从 1 开始）。

        Returns:
            CorpusResult 对象，包含章节内容。内容超过 20000 字符时截断。
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT d.id, d.title, d.text
                FROM documents d
                WHERE d.book = $1 AND d.chapter_number = $2
                """,
                book,
                chapter_id,
            )
            if row is None:
                return CorpusResult(
                    hint=f"未找到章节 {chapter_id}", brief="章节未找到"
                )

            text = row["text"]
            title = row["title"]
            if len(text) > _MAX_CONTENT_LENGTH:
                truncated = text[:_MAX_CONTENT_LENGTH]
                return CorpusResult(
                    text=truncated,
                    title=title,
                    hint=f"内容已截断（共 {len(text)} 字符），可使用 sentence_range 查看特定范围",
                    brief="内容已截断",
                )

            return CorpusResult(
                text=text,
                title=title,
                hint="已返回完整章节内容",
                brief=f"第{chapter_id}章",
            )

    async def _query_sentence_range(
        self,
        book: str,
        chapter_id: int,
        sentence_range: list[int],
    ) -> CorpusResult:
        """根据句子索引范围获取句子列表。

        Args:
            book: 书名。
            chapter_id: 章节号（从 1 开始）。
            sentence_range: 句子索引范围 [start, end]。

        Returns:
            CorpusResult 对象，包含指定范围内的句子列表。
        """
        if len(sentence_range) < 2:
            return CorpusResult(hint="sentence_range 需要 [start, end]", brief="参数错误")

        start_idx, end_idx = sentence_range[0], sentence_range[1]
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT s.sentence_index, s.text
                FROM sentences s
                WHERE s.book = $1 AND s.chapter_number = $2
                  AND s.sentence_index >= $3 AND s.sentence_index <= $4
                ORDER BY s.sentence_index
                """,
                book,
                chapter_id,
                start_idx,
                end_idx,
            )

        sentences = [
            CorpusSentence(sentence_index=r["sentence_index"], text=r["text"])
            for r in rows
        ]
        return CorpusResult(
            sentences=sentences,
            hint=f"已返回句子 {start_idx}-{end_idx}",
            brief=f"{len(sentences)} 条句子",
        )

    async def _query_keywords(
        self,
        book: str,
        chapter_ids: list[int],
        keywords: list[str],
        context_size: int,
    ) -> CorpusResult:
        """关键词搜索句子，可选择附带上下文。

        Args:
            book: 书名。
            chapter_ids: 章节 ID 列表，用于限定搜索范围。
            keywords: 关键词列表。
            context_size: 上下文句子数量，0 表示不带上下文。

        Returns:
            CorpusResult 对象，包含匹配的句子列表。
        """
        if context_size > 0:
            return await self._query_keywords_with_context(
                book, chapter_ids, keywords, context_size
            )

        # 构建 LIKE 条件
        like_clauses = " AND ".join(f"s.text LIKE '%' || ${i + 2} || '%'" for i in range(len(keywords)))
        params: list[str | int] = [book] + keywords

        query_base = """
            SELECT s.sentence_index, s.text, s.document_id, s.chapter_number
            FROM sentences s
            WHERE s.book = $1
        """
        if chapter_ids:
            placeholders = ", ".join(f"${i}" for i in range(len(keywords) + 2, len(keywords) + 2 + len(chapter_ids)))
            query_base += f" AND s.chapter_number IN ({placeholders})"
            params.extend(chapter_ids)

        query = f"{query_base} AND {like_clauses} ORDER BY s.document_id, s.sentence_index LIMIT 500"

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)

        sentences = [
            CorpusSentence(
                sentence_index=r["sentence_index"],
                text=r["text"],
                document_id=r["document_id"],
                title=f"章节 {r['chapter_number']}",
            )
            for r in rows
        ]

        if not sentences:
            return CorpusResult(hint="未找到匹配的原文", brief="未找到匹配")

        return CorpusResult(
            sentences=sentences,
            hint=f"找到 {len(sentences)} 处匹配，可使用 corpus + chapter_id 查看完整章节",
            brief=f"找到 {len(sentences)} 处原文匹配",
        )

    async def _query_keywords_with_context(
        self,
        book: str,
        chapter_ids: list[int],
        keywords: list[str],
        context_size: int,
    ) -> CorpusResult:
        """关键词搜索并返回周围上下文句子。

        Args:
            book: 书名。
            chapter_ids: 章节 ID 列表，用于限定搜索范围。
            keywords: 关键词列表。
            context_size: 上下文句子数量。

        Returns:
            CorpusResult 对象，包含匹配句子及其上下文。
        """
        like_clauses = " AND ".join(
            f"s.text LIKE '%' || ${i + 4} || '%'" for i in range(len(keywords))
        )
        params: list[str | int] = [book, context_size, context_size] + keywords

        extra_conditions = ""
        if chapter_ids:
            placeholders = ", ".join(
                f"${i}" for i in range(len(keywords) + 4, len(keywords) + 4 + len(chapter_ids))
            )
            extra_conditions = f" AND s.chapter_number IN ({placeholders})"
            params.extend(chapter_ids)

        query = f"""
            WITH matches AS (
                SELECT s.document_id, s.sentence_index, s.text
                FROM sentences s
                WHERE s.book = $1 AND ({like_clauses})
                {extra_conditions}
            )
            SELECT DISTINCT ctx.sentence_index, ctx.text, ctx.document_id, ctx.chapter_number
            FROM matches m
            JOIN sentences ctx ON ctx.book = $1
                AND ctx.document_id = m.document_id
                AND ctx.sentence_index >= m.sentence_index - $2
                AND ctx.sentence_index <= m.sentence_index + $3
            ORDER BY ctx.document_id, ctx.sentence_index
            LIMIT 500
        """

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)

        sentences = [
            CorpusSentence(
                sentence_index=r["sentence_index"],
                text=r["text"],
                document_id=r["document_id"],
                title=f"章节 {r['chapter_number']}",
            )
            for r in rows
        ]

        if not sentences:
            return CorpusResult(hint="未找到匹配的原文", brief="未找到匹配")

        return CorpusResult(
            sentences=sentences,
            hint=f"找到匹配（含 {context_size} 句上下文），可使用 corpus + chapter_id 查看完整章节",
            brief=f"找到 {len(sentences)} 条上下文句子",
        )
