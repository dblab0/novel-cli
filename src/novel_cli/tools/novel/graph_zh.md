查询知识图谱中的实体关系（如师徒、同盟、敌对等）。

此工具需要来自 SearchEntity 的 `entity_id`。在查询具体关系之前，务必先使用 `sub_action="types"` 发现可用的关系类型。

## 子操作

| 子操作 | 用途 | 需要 `rel_types` | 返回内容 |
|---|---|---|---|
| `types` | 列出可用关系类型（必须首先调用） | 否 | 类型名称列表 |
| `rels` | 获取关系详情（含描述 + chapter_ids） | **是** | 关联实体 + 描述文本 |
| `related` | 仅列出关联实体 ID（无描述） | **是** | 关联实体 ID 列表 |
| `desc` | 获取实体**完整**描述（SearchEntity 为截断预览） | 否 | 完整描述文本 |

**`rels` 与 `related` 的区别**：优先使用 `rels`。`rels` 返回的描述中包含 chapter_ids，可用于后续 SearchCorpus 缩小搜索范围。`related` 只返回实体 ID，不含描述。

**关于 `desc`**：`desc` 返回实体的**完整**描述（SearchEntity 只返回截断预览）。如果需要完整实体信息，应调用 `desc`。

## 工作流：types → rels（严格两步）

**第 1 步：types — 发现可用关系类型**
```
SearchGraph(entity_id="凡人修仙传_人物_韩立_0", sub_action="types")
```
返回格式：
```
【分类】TYPE_NAME —
【组织关系】MEMBER_OF —
【师承关系】MASTER_DISCIPLE —
【拥有关系】POSSESS —
```
其中 `TYPE_NAME`（如 MEMBER_OF、MASTER_DISCIPLE）就是 `rel_types` 参数可用的值。

**第 2 步：rels — 用 types 返回的类型名查询关系详情**
```
SearchGraph(entity_id="凡人修仙传_人物_韩立_0", sub_action="rels", rel_types=["MEMBER_OF", "MASTER_DISCIPLE"])
```
返回关联实体 + 描述（描述中含 chapter_ids，供 SearchCorpus 使用）。

## rel_types 参数规则

`rel_types` 的值**必须**从第 1 步 `types` 返回的 TYPE_NAME 中选取。

正确示例：
```
// 第 1 步返回: 【组织关系】MEMBER_OF, 【师承关系】MASTER_DISCIPLE
rel_types=["MEMBER_OF", "MASTER_DISCIPLE"]  ← 从 types 结果中选取
```

错误示例：
```
rel_types=null                              ← 必填参数缺失，将返回错误
rel_types=["member_of"]                     ← 大小写错误，必须与 types 返回值完全一致
rel_types=["师徒关系"]                       ← 必须用 TYPE_NAME，不是中文分类名
```
