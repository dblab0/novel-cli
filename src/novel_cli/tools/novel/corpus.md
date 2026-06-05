Search the original novel text by keyword.

**定位**：第一步主动搜索工具，按关键词在小说原文中查找相关段落。

| Parameter | Required | Description |
|-----------|----------|-------------|
| `keyword` | Yes | A single search keyword (string) |
| `chapter_ids` | No | List of chapter IDs to scope the search. Recommended to obtain from SearchGraph results |
| `context_size` | No | Context sentences around each match. Default 1, increase to 3-5 for richer context |

**Usage**:
```
SearchCorpus(keyword="噬金虫")
SearchCorpus(keyword="长春功", chapter_ids=[5, 6, 7, 8])
```

**Using chapter_ids**:
When SearchGraph results include chapter references, use `chapter_ids` to scope your search.
This dramatically improves precision and reduces empty results.

**Failure recovery**:
1. 空结果 → 换 keyword（用实体别名、相关人名、上位词）
2. 仍然空 → 增大 `context_size`（如从 1 增到 3-5）
3. 仍然空 → 切 ReadChapter(chapter_id=章节号) 读整章
4. **连续 3 次空结果 → 停止搜索**，用已有信息组织回答。禁止继续更换关键词穷举。
