"""技能生成评估任务的常量映射表。

包含实体类型、skill_type、输出目录、ID 缩写等映射关系。
"""


# 实体类型 → (skill_type, label) 映射
ENTITY_TYPE_MAP: dict[str, tuple[str, str]] = {
    "人物": ("generate-character", "角色"),
    "物品": ("generate-item", "物品"),
    "地点": ("generate-location", "地点"),
    "组织": ("generate-organization", "组织"),
    "技能": ("generate-skill", "功法"),
}

# skill_type → 输出子目录映射
SKILL_OUTPUT_DIR_MAP: dict[str, str] = {
    "generate-character": "characters",
    "generate-item": "items",
    "generate-location": "locations",
    "generate-organization": "organizations",
    "generate-skill": "skills",
}

# 实体类型 → ID 缩写映射
TYPE_ABBR_MAP: dict[str, str] = {
    "人物": "CHAR",
    "物品": "ITEM",
    "地点": "LOC",
    "组织": "ORG",
    "技能": "SKILL",
}
