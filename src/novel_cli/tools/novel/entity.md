Search for entities (characters, artifacts, sects, locations, etc.) in the novel knowledge base.
Returns matched entity names, types, descriptions, and `entity_id` for use with SearchGraph and SearchCorpus.

**Choosing `search_mode`**:

| Mode | When to use | Query requirement |
|------|-------------|-------------------|
| `name` | You know the exact entity name | A single name (e.g. "韩立", "青竹蜂云剑") |
| `vector` | You only know characteristics or description | A single core keyword (e.g. "隐匿术") |

**CRITICAL — One keyword per query**:
Regardless of search_mode, always use exactly **one** core word per query call.
Multiple keywords dilute search signal in both modes.

GOOD:
- SearchEntity(query="韩立", search_mode="name")
- Multiple entities needed → parallel calls with one name each

BAD:
- SearchEntity(query="韩立 师傅 功法")  ← multiple names in name mode
- SearchEntity(query="虚天殿 冰焰")     ← two concepts in vector mode

**Failure recovery** (MUST follow strictly, do NOT repeat name mode):
1. name 模式无结果 → **MUST** immediately switch to vector mode with the same keyword
2. Reduce query to a single core keyword
3. Fall back to SearchCorpus with `keyword` for direct text search
