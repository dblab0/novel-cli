"""小说知识库搜索工具包。

提供四个独立的搜索工具，用于在小说知识库中进行查询：
- SearchEntity：实体搜索（人物、法宝、门派等）
- SearchGraph：关系图查询（关系类型、描述、相关实体、关系详情）
- SearchCorpus：关键词搜索（按关键词在原文中查找相关段落）
- ReadChapter：章节读取（读取完整章节或指定句子范围）

所有工具均采用懒连接模式，首次查询时才建立数据库连接。
"""

from novel_cli.tools.novel.chapter import ReadChapter, ReadChapterParams
from novel_cli.tools.novel.corpus import SearchCorpus, SearchCorpusParams
from novel_cli.tools.novel.entity import SearchEntity, SearchEntityParams
from novel_cli.tools.novel.graph import SearchGraph, SearchGraphParams

__all__ = [
    "ReadChapter",
    "ReadChapterParams",
    "SearchCorpus",
    "SearchCorpusParams",
    "SearchEntity",
    "SearchEntityParams",
    "SearchGraph",
    "SearchGraphParams",
]
