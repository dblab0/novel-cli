Read a full chapter or a specific passage by sentence range.

**定位**：兜底/补全工具。搜索空结果时读整章，或搜索结果间有信息空白时按范围补全。

| Parameter | Required | Description |
|-----------|----------|-------------|
| `chapter_id` | Yes | Chapter ID to read |
| `start` | No | Start sentence index (inclusive). Omit to read from the beginning |
| `end` | No | End sentence index (exclusive). Omit to read to the end of chapter |

**Usage**:
```
ReadChapter(chapter_id=173)                                    → Full chapter
ReadChapter(chapter_id=173, start=10, end=20)                  → Specific passage
```

**When to use**:
1. SearchCorpus returned empty → Read the whole chapter with `chapter_id`
2. Search results have information gaps → Read a specific range with `start` and `end`

**Token cost control**:
- Full chapter reading is expensive. Prefer `start` and `end` when you know the approximate location.
- When the full chapter is returned, it includes total sentence count — use it for subsequent precise retrieval.
