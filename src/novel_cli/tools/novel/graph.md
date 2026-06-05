Query the knowledge graph for entity relationships (e.g. master-disciple, allies, enemies).

This tool requires an `entity_id`. The ID can come from SearchEntity results, or directly from user input when the user provides a known entity_id.

## Usage

**Overview (omit `rel_type`):** Returns entity description + all available relationship types.
```
SearchGraph(entity_id="西游记_人物_孙悟空_0")
```

**Relationship details (pass `rel_type`):** Returns all relationships of that type, with descriptions and chapter_ids.
```
SearchGraph(entity_id="西游记_人物_孙悟空_0", rel_type="亲密")
```

You can also pass comma-separated multiple types to view several relationship groups at once:
```
SearchGraph(entity_id="西游记_人物_孙悟空_0", rel_type="亲属,对立")
```

## Workflow

```
Step 1: SearchGraph(entity_id="...")        → description + available relationship types
Step 2: SearchGraph(entity_id="...", rel_type="亲密") → relationship details + chapter_ids
Step 3: SearchCorpus(keyword="关键词", chapter_ids=[...]) → original text
```

## `rel_type` rules

- The value MUST be copied from Step 1's response (exact match, case-sensitive).
- Different books may use Chinese names (亲属, 对立) or English names (MEMBER_OF) — just copy exactly what the overview shows.
- Supports comma-separated multiple types, e.g. `rel_type="亲属,对立"`.
- If the result is too large, the tool will return an error — reduce the number of types and retry.
