"""CSV 实体数据抽取的 Case 生成器。

该模块负责：
1. 从 CSV 文件读取实体数据
2. 按描述数量筛选和随机抽取实体
3. 为每个实体生成 query（无需 LLM）
4. 保存/加载 YAML 用例文件
5. 支持追加模式
"""

import ast
import csv
import logging
import random
from pathlib import Path

import yaml

from novel_eval.models import EvalCase
from novel_eval.tasks.skill_generation.models import (
    ENTITY_TYPE_MAP,
    SKILL_OUTPUT_DIR_MAP,
    TYPE_ABBR_MAP,
)

logger = logging.getLogger(__name__)


def generate_cases(
    book: str,
    data_dir: Path,
    min_descriptions: int = 20,
    min_descriptions_by_type: dict[str, int] | None = None,
    samples_per_type: int = 10,
    seed: int | None = None,
) -> list[EvalCase]:
    """从 CSV 实体数据中随机抽取高频实体生成评估用例。

    Args:
        book: 书名，如 "凡人修仙传"。
        data_dir: 实体数据根目录，如 "data/input_data"。
        min_descriptions: 最小描述条目数阈值，筛选描述丰富的实体。
        min_descriptions_by_type: 按实体类型覆盖 min_descriptions，如 {"技能": 5}。
        samples_per_type: 每种类型随机抽取的实体数量。
        seed: 随机种子，用于确保可复现。

    Returns:
        生成的测试用例列表。
    """
    # 构建 CSV 路径
    csv_path = data_dir / book / f"{book}_entities.csv"
    if not csv_path.exists():
        logger.warning(f"CSV 文件不存在: {csv_path}")
        return []

    # 1. 解析 CSV
    entities = _parse_csv(csv_path)
    if not entities:
        logger.warning(f"CSV 解析结果为空: {csv_path}")
        return []

    # 2. 按类型分组、筛选、抽取
    sampled = _sample_entities(entities, min_descriptions, min_descriptions_by_type, samples_per_type, seed)

    # 3. 为每个实体生成 EvalCase
    cases = []
    for entity_type, items in sampled.items():
        type_abbr = TYPE_ABBR_MAP.get(entity_type, entity_type[:4].upper())
        skill_type, label = ENTITY_TYPE_MAP.get(
            entity_type, ("generate-character", entity_type)
        )
        output_dir = SKILL_OUTPUT_DIR_MAP.get(skill_type, "others")

        for idx, item in enumerate(items, 1):
            entity_id = item["id"]
            entity_name = item["name"]
            descriptions = item["descriptions"]
            description_count = item["description_count"]

            query = f"/skill:{skill_type} 检索{entity_id} 相关信息后生成{label}设定"
            case_id = f"SG-{type_abbr}-{idx:03d}"

            case = EvalCase(
                id=case_id,
                book=book,
                question=query,
                meta={
                    "entity_id": entity_id,
                    "entity_name": entity_name,
                    "entity_type": entity_type,
                    "skill_type": skill_type,
                    "output_path": f"{output_dir}/{entity_id}.md",
                    "description_count": description_count,
                    "descriptions": descriptions[:5],
                },
            )
            cases.append(case)

    return cases


def _parse_csv(csv_path: Path) -> list[dict]:
    """解析 CSV 实体数据文件。

    读取 CSV 文件，解析 description 字段（Python 列表格式），
    计算每个实体的描述条目数。

    Args:
        csv_path: CSV 文件路径。

    Returns:
        实体字典列表，每个包含 id、name、type、descriptions、description_count。
    """
    entities = []

    if not csv_path.exists():
        return []

    with open(csv_path, encoding="utf-8") as f:
        # 处理 BOM
        content = f.read()
        if content.startswith("\ufeff"):
            content = content[1:]

    import io

    reader = csv.DictReader(io.StringIO(content))
    for row in reader:
        entity_id = row.get("id", "").strip()
        entity_name = row.get("name", "").strip()
        entity_type = row.get("type", "").strip()
        desc_str = row.get("description", "").strip()

        if not entity_id or not entity_name:
            continue

        # 解析 description 字段（Python 列表格式）
        try:
            descriptions = ast.literal_eval(desc_str)
            if not isinstance(descriptions, list):
                descriptions = []
        except (ValueError, SyntaxError):
            # 解析失败时跳过该实体
            logger.debug(f"解析 description 失败，跳过实体: {entity_id}")
            continue

        entities.append({
            "id": entity_id,
            "name": entity_name,
            "type": entity_type,
            "descriptions": descriptions,
            "description_count": len(descriptions),
        })

    return entities


def _sample_entities(
    entities: list[dict],
    min_descriptions: int,
    min_descriptions_by_type: dict[str, int] | None,
    samples_per_type: int,
    seed: int | None,
) -> dict[str, list[dict]]:
    """按类型分组、筛选、随机抽取实体。

    Args:
        entities: 实体字典列表。
        min_descriptions: 默认最小描述条目数阈值。
        min_descriptions_by_type: 按类型覆盖阈值，如 {"技能": 5}。
        samples_per_type: 每种类型抽取数量。
        seed: 随机种子。

    Returns:
        按类型分组的抽样结果字典。
    """
    # 按类型分组
    by_type: dict[str, list[dict]] = {}
    for e in entities:
        t = e["type"]
        by_type.setdefault(t, []).append(e)

    # 筛选描述 > min_descriptions 的实体，然后随机抽取
    rng = random.Random(seed)
    result: dict[str, list[dict]] = {}
    type_overrides = min_descriptions_by_type or {}

    for entity_type, items in by_type.items():
        # 只在支持的类型中抽取
        if entity_type not in ENTITY_TYPE_MAP:
            continue

        # 按类型获取阈值，无覆盖时使用默认值
        threshold = type_overrides.get(entity_type, min_descriptions)

        # 筛选描述丰富的实体
        filtered = [e for e in items if e["description_count"] > threshold]

        # 随机抽取（可用不足时取全部）
        count = min(samples_per_type, len(filtered))
        if count > 0:
            sampled = rng.sample(filtered, count)
            result[entity_type] = sampled

    return result


def save_cases_yaml(
    base_dir: Path,
    cases: list[EvalCase],
    book: str,
) -> list[Path]:
    """按 skill 类型保存为独立 YAML 文件。

    每种 skill_type 生成一个独立的 YAML 文件，如 skill_gen_character.yaml。

    Args:
        base_dir: eval_cases 目录路径。
        cases: 测试用例列表。
        book: 书名。

    Returns:
        保存的 YAML 文件路径列表。
    """
    book_dir = base_dir / book
    book_dir.mkdir(parents=True, exist_ok=True)

    # 按 skill_type 分组
    by_skill: dict[str, list[EvalCase]] = {}
    for case in cases:
        skill_type = case.meta.get("skill_type", "unknown")
        by_skill.setdefault(skill_type, []).append(case)

    # skill_type → 文件名映射
    skill_file_map = {
        "generate-character": "skill_gen_character.yaml",
        "generate-item": "skill_gen_item.yaml",
        "generate-location": "skill_gen_location.yaml",
        "generate-organization": "skill_gen_organization.yaml",
        "generate-skill": "skill_gen_skill.yaml",
    }

    saved_paths = []
    for skill_type, group in by_skill.items():
        filename = skill_file_map.get(skill_type, f"skill_gen_{skill_type}.yaml")
        path = book_dir / filename

        _, label = ENTITY_TYPE_MAP.get(
            _get_entity_type_from_skill(skill_type), (skill_type, skill_type)
        )

        data = {
            "book": book,
            "skill_type": skill_type,
            "description": f"{label}设定生成评估用例",
            "cases": [
                {
                    "id": c.id,
                    "question": c.question,
                    "meta": c.meta,
                }
                for c in group
            ],
        }

        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

        saved_paths.append(path)
        logger.info(f"保存 {len(group)} 个用例到: {path}")

    return saved_paths


def save_cases_yaml_append(
    base_dir: Path,
    cases: list[EvalCase],
    book: str,
) -> list[Path]:
    """追加模式保存：加载已有 YAML、排除已有 entity_id、合并后重排 ID。

    Args:
        base_dir: eval_cases 目录路径。
        cases: 新生成的测试用例列表。
        book: 书名。

    Returns:
        保存的 YAML 文件路径列表。
    """
    book_dir = base_dir / book
    book_dir.mkdir(parents=True, exist_ok=True)

    # 按 skill_type 分组
    by_skill: dict[str, list[EvalCase]] = {}
    for case in cases:
        skill_type = case.meta.get("skill_type", "unknown")
        by_skill.setdefault(skill_type, []).append(case)

    # skill_type → 文件名映射
    skill_file_map = {
        "generate-character": "skill_gen_character.yaml",
        "generate-item": "skill_gen_item.yaml",
        "generate-location": "skill_gen_location.yaml",
        "generate-organization": "skill_gen_organization.yaml",
        "generate-skill": "skill_gen_skill.yaml",
    }

    saved_paths = []
    for skill_type, new_cases in by_skill.items():
        filename = skill_file_map.get(skill_type, f"skill_gen_{skill_type}.yaml")
        path = book_dir / filename

        # 加载已有用例
        existing_cases = []
        if path.exists():
            existing_cases = load_cases_yaml(path)

        # 排除已有的 entity_id
        existing_ids = {c.meta.get("entity_id") for c in existing_cases}
        fresh_cases = [
            c for c in new_cases if c.meta.get("entity_id") not in existing_ids
        ]

        # 合并
        all_cases = existing_cases + fresh_cases

        # 重排 ID
        entity_type = _get_entity_type_from_skill(skill_type)
        type_abbr = TYPE_ABBR_MAP.get(entity_type, entity_type[:4].upper())
        _, label = ENTITY_TYPE_MAP.get(entity_type, (skill_type, skill_type))

        for i, case in enumerate(all_cases, 1):
            case.id = f"SG-{type_abbr}-{i:03d}"

        data = {
            "book": book,
            "skill_type": skill_type,
            "description": f"{label}设定生成评估用例",
            "cases": [
                {
                    "id": c.id,
                    "question": c.question,
                    "meta": c.meta,
                }
                for c in all_cases
            ],
        }

        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

        saved_paths.append(path)
        logger.info(f"追加保存 {len(fresh_cases)} 个新用例，文件现有 {len(all_cases)} 个: {path}")

    return saved_paths


def load_cases_yaml(path: Path) -> list[EvalCase]:
    """加载 YAML 用例文件。

    Args:
        path: YAML 文件路径。

    Returns:
        EvalCase 列表。
    """
    if not path.exists():
        return []

    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not data:
        return []

    cases = []
    for c in data.get("cases", []):
        cases.append(
            EvalCase(
                id=c["id"],
                question=c["question"],
                book=data.get("book", ""),
                meta=c.get("meta", {}),
            )
        )
    return cases


def _get_entity_type_from_skill(skill_type: str) -> str:
    """从 skill_type 反查实体类型。

    Args:
        skill_type: 技能类型，如 "generate-character"。

    Returns:
        实体类型，如 "人物"。
    """
    for entity_type, (st, _) in ENTITY_TYPE_MAP.items():
        if st == skill_type:
            return entity_type
    return skill_type
