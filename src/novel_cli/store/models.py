"""小说知识存储的数据模型定义，包含实体、关系和语料相关的 Pydantic 模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Entity(BaseModel):
    """小说实体模型，表示人物、物品、地点等实体。

    Attributes:
        id: 实体的唯一标识符。
        name: 实体名称。
        type: 实体类型（如人物、物品、地点等）。
        book: 所属书名。
        description: 实体描述文本。
        score: 向量搜索的相似度分数，None 表示名称精确匹配。
        match_type: 匹配类型，"vector" 表示向量匹配，"name" 表示名称匹配。
        vector_rank: 向量搜索结果的排名位置。
        name_rank: 名称搜索结果的排名位置。
        alias: 实体别名。
    """

    id: str
    name: str
    type: str
    book: str
    description: str = ""
    score: float | None = None
    match_type: str = "vector"
    vector_rank: int | None = None
    name_rank: int | None = None
    alias: str = ""


class RelType(BaseModel):
    """关系类型模型，包含关系的类型元数据。

    Attributes:
        type: 关系类型名称。
        description: 关系类型的描述。
        category: 关系类型的分类。
    """

    type: str
    description: str
    category: str


class GraphRelation(BaseModel):
    """图关系模型，表示实体间的关联关系。

    Attributes:
        relationship_type: 关系类型名称。
        other_node_id: 关联的另一实体 ID。
        description: 关系描述文本。
    """

    relationship_type: str
    other_node_id: str
    description: str = ""


class GraphResult(BaseModel):
    """图查询结果模型。

    Attributes:
        types: 关系类型列表。
        description: 实体描述文本。
        alias: 实体别名。
        rels: 关系详情列表（含描述）。
    """

    types: list[RelType] = Field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    description: str = ""
    alias: str = ""
    rels: list[GraphRelation] = Field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]


class CorpusSentence(BaseModel):
    """语料句子模型，表示语料库中的单个句子。

    Attributes:
        sentence_index: 句子索引位置。
        text: 句子文本内容。
        document_id: 所属文档/章节 ID。
        title: 所属章节标题。
        source_name: 来源名称。
    """

    sentence_index: int
    text: str
    document_id: int | None = None
    title: str | None = None
    source_name: str | None = None


class CorpusResult(BaseModel):
    """语料查询结果模型。

    Attributes:
        sentences: 句子列表。
        text: 完整文本内容。
        title: 章节标题。
        hint: 提示信息。
        brief: 简要描述。
    """

    sentences: list[CorpusSentence] = Field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    text: str = ""
    title: str | None = None
    hint: str = ""
    brief: str = ""

    def to_text(self) -> str:
        """返回完整文本表示。

        如果设置了 text 字段则直接返回；否则将 sentences 列表格式化为
        "索引→ 句子文本" 格式的字符串。当句子跨越多个章节时，在章节切换处
        插入分隔行标注章节标题。

        Returns:
            完整文本内容的字符串表示。
        """
        if self.text:
            return self.text
        if not self.sentences:
            return ""
        lines: list[str] = []
        prev_doc_id: int | None = None
        for s in self.sentences:
            # 章节切换时插入分隔标识
            if s.document_id is not None and s.document_id != prev_doc_id:
                if prev_doc_id is not None:
                    lines.append("")
                title = s.title or f"章节 {s.document_id}"
                lines.append(f"--- {title} ---")
                prev_doc_id = s.document_id
            lines.append(f"{s.sentence_index}\u2192 {s.text}")
        return "\n".join(lines)