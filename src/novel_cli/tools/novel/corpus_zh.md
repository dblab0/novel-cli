按关键词搜索小说原文。

**定位**：第一步主动搜索工具，按关键词在小说原文中查找相关段落。

| 参数 | 必填 | 说明 |
|------|------|------|
| `keyword` | 是 | 搜索关键词（字符串） |
| `chapter_ids` | 否 | 章节 ID 列表，限定搜索范围。推荐从 SearchGraph 结果获取 |
| `context_size` | 否 | 匹配周围的上下文句子数。默认 1，可增大至 3-5 获取更丰富上下文 |

**使用示例**：
```
SearchCorpus(keyword="噬金虫")
SearchCorpus(keyword="长春功", chapter_ids=[5, 6, 7, 8])
```

**使用 chapter_ids**：
当 SearchGraph 结果中包含章节引用时，使用 `chapter_ids` 来限定搜索范围。
这能显著提高精确度并减少空结果。

**失败恢复**：
1. 空结果 → 换 keyword（用实体别名、相关人名、上位词）
2. 仍然空 → 增大 `context_size`（如从 1 增到 3-5）
3. 仍然空 → 切 ReadChapter(chapter_id=章节号) 读整章
4. **连续 3 次空结果 → 停止搜索**，用已有信息组织回答。禁止继续更换关键词穷举。
