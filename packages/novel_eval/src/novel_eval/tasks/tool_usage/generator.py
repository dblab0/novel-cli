"""问题生成器：LLM 读取设定文件，按难度层级生成测试问题。

该模块负责：
1. 读取设定文件，按难度层级生成测试问题
2. 对生成的问题进行实体验证，填充 _validation 字段
3. 支持 YAML 格式的用例集读写

目录结构：
    eval_cases/
    └── <书名>/
        ├── level_1_entity.yaml
        ├── level_2_chain.yaml
        ├── level_3_multi_step.yaml
        └── level_4_complex.yaml
"""

import asyncio
import json
import logging
import re
from pathlib import Path

import httpx
import yaml

from novel_eval.models import EvalCase
from novel_eval.prompts import PromptLoader

logger = logging.getLogger(__name__)

# 难度等级定义
LEVEL_DEFINITIONS = {
    1: "Level 1 - 单次调用（基础工具选择）：测试 agent 能否根据问题选择正确的 action。"
    "例如：'韩立是谁？' -> 应触发 action='entity'。",
    2: "Level 2 - 两步链式调用（参数传递）：测试 agent 能否正确链接两个工具调用。"
    "例如：'韩立的师父是谁？' -> entity -> graph(related/rels)。"
    "注意：Level 2 不涉及 corpus，保持 entity → graph 的简单链式结构。",
    3: "Level 3 - 多步链式调用（关系验证）：测试 agent 能否在多步调用中验证关系细节。"
    "例如：'韩立与玄骨上人合作的具体场景原文是怎样的？' -> entity -> graph -> corpus。"
    "corpus 用于验证知识图谱中的关系描述是否与原文一致。",
    4: "Level 4 - 综合推理（信息整合）：测试 agent 的综合查询和原文整合能力。"
    "例如：'比较韩立在血色禁地与虚天殿两次探险中的战斗手段' -> 多轮 entity/graph + corpus 整合原文描写。"
    "corpus 用于从原文中提取细节进行对比、分析、归纳。",
}

# 各 level 允许的工具类型组合
ALLOWED_TOOL_COMBINATIONS = {
    1: [["entity"]],
    2: [["entity"], ["entity", "graph"]],
    3: [["entity"], ["entity", "graph"], ["entity", "graph", "corpus"]],
    4: [["entity"], ["entity", "graph"], ["entity", "graph", "corpus"]],
}

# 各 level 的示例问题（供 LLM 参考）
LEVEL_EXAMPLES = {
    1: [
        {"question": "韩立修炼的主功法是什么？", "expected_tool_types": ["entity"]},
        {"question": "虚天鼎属于什么级别的法宝？", "expected_tool_types": ["entity"]},
    ],
    2: [
        {"question": "韩立的师父李化元是哪个门派的？", "expected_tool_types": ["entity", "graph"]},
        {"question": "虚天殿内殿中取出虚天鼎的上古灵虫是什么？", "expected_tool_types": ["entity", "graph"]},
    ],
    3: [
        {
            "question": "韩立与玄骨上人在虚天殿合作对抗极阴祖师的具体场景原文是怎样的？",
            "expected_tool_types": ["entity", "graph", "corpus"],
        },
        {
            "question": "血色禁地中韩立击杀墨蛟的战斗描写是怎样的？",
            "expected_tool_types": ["entity", "graph", "corpus"],
        },
    ],
    4: [
        {
            "question": "比较韩立在血色禁地与虚天殿两次探险中的战斗手段，至少各举三例原文描写。",
            "expected_tool_types": ["entity", "graph", "corpus"],
        },
        {
            "question": "韩立从炼气期到结丹期经历了哪些关键突破？请找出相关原文描述。",
            "expected_tool_types": ["entity", "graph", "corpus"],
        },
    ],
}

# 设定文件名称列表（按优先级排序）
# 注意：history-final.md 文件过大且对测试问题生成帮助不大，已排除
SETTING_FILES = [
    "world-final.md",
    "power_system-final.md",
    "currency-final.md",
]

# 模块级 PromptLoader 单例
_PROMPT_LOADER = PromptLoader()


# 场景名 → 难度等级映射
SCENARIO_LEVEL_MAP = {
    "level_1_entity": 1,
    "level_2_chain": 2,
    "level_3_multi_step": 3,
    "level_4_complex": 4,
}

# 场景名 → YAML 文件名映射
SCENARIO_FILENAMES = {
    "level_1_entity": "level_1_entity.yaml",
    "level_2_chain": "level_2_chain.yaml",
    "level_3_multi_step": "level_3_multi_step.yaml",
    "level_4_complex": "level_4_complex.yaml",
}


def get_scenario_filename(scenario: str) -> str:
    """根据场景名获取对应的 YAML 文件名。

    Args:
        scenario: 场景名称，如 "level_1_entity"。

    Returns:
        对应的文件名字符串，如 "level_1_entity.yaml"。
    """
    return SCENARIO_FILENAMES.get(scenario, f"{scenario}.yaml")


def generate_cases(
    book: str,
    settings_dir: Path,
    scenario: str = "level_1_entity",
    llm_config: dict | None = None,
    llm_model: str | None = None,
    instruction: str | None = None,
    id_offset: int = 0,
) -> list[EvalCase]:
    """通过 LLM 读取设定文件，生成指定场景的测试问题。

    Args:
        book: 书名，如 "凡人修仙传"。
        settings_dir: 设定文件目录，默认 /github/novel2settings/settings/。
        scenario: 场景名称，如 "level_1_entity"。
        llm_config: LLM 配置，包含 model、base_url 和 api_key（用于生成问题）。
        llm_model: 调用 LLM 时使用的模型名称（可选，覆盖 llm_config 中的 model）。
        instruction: 用户自定义指令，用于引导问题生成方向（如"集中在人物关系"）。
        id_offset: ID 序号偏移量，用于追加模式下避免 ID 冲突。默认 0。

    Returns:
        生成的测试用例列表。
    """
    # 场景名 → 难度等级
    level = SCENARIO_LEVEL_MAP.get(scenario)
    if level is None:
        logger.warning(f"无效的场景名称 {scenario}，使用默认场景 level_1_entity")
        scenario = "level_1_entity"
        level = 1

    # 确定书籍设定目录
    book_settings_dir = settings_dir / book
    if not book_settings_dir.exists():
        logger.warning(f"书籍设定目录不存在: {book_settings_dir}")
        # 尝试直接使用 settings_dir（某些书籍可能直接在 settings 目录下）
        book_settings_dir = settings_dir

    # 读取设定文件内容
    settings_content = _read_settings_files(book_settings_dir)
    if not settings_content:
        logger.warning(f"未找到设定文件，目录: {book_settings_dir}")
        return []

    # 通过 PromptLoader 渲染 system + user 双消息
    level_definition = LEVEL_DEFINITIONS[level]
    level_examples = _format_level_examples(level)
    messages = _PROMPT_LOADER.render_messages(
        "generator",
        level=level,
        level_definition=level_definition,
        settings_content=settings_content,
        level_examples=level_examples,
        instruction=instruction,
    )

    # 调用 LLM 生成问题
    if llm_config is None:
        logger.warning("未提供 LLM 配置，无法生成问题")
        return []

    try:
        # llm_model 用于调用 LLM API
        response_text = _call_llm(messages, llm_config, llm_model)
        if not response_text:
            logger.warning("LLM 返回空响应")
            return []
    except Exception as e:
        logger.error(f"LLM 调用失败: {e}")
        return []

    # 解析 LLM 返回的 JSON
    raw_cases = _parse_llm_response(response_text, level)
    if not raw_cases:
        logger.warning("无法解析 LLM 返回的问题列表")
        return []

    # 校验并修正 expected_tool_types
    raw_cases = _validate_tool_types(raw_cases, level)

    # 构建 EvalCase 列表
    # id_offset 用于追加模式下保证新 ID 不与已有用例冲突
    cases = []
    for i, raw in enumerate(raw_cases):
        # 使用偏移量生成唯一 ID，忽略 LLM 自行生成的 ID
        case_id = f"L{level}-{i + 1 + id_offset:03d}"

        case = EvalCase(
            id=case_id,
            book=book,
            question=raw.get("question", ""),
            meta={
                "involved_entities": raw.get("involved_entities", []),
                "expected_tool_types": raw.get("expected_tool_types", ["entity"]),
            },
            validation=None,  # 后续通过 _run_entity_validation 填充
        )
        cases.append(case)

    logger.info(f"生成 {len(cases)} 个 Level {level} 测试用例")
    return cases


def _read_settings_files(settings_dir: Path) -> str:
    """读取设定文件内容。

    按优先级顺序读取设定文件，合并内容返回。

    Args:
        settings_dir: 设定文件目录路径。

    Returns:
        合并后的设定文件内容字符串。
    """
    contents = []

    for filename in SETTING_FILES:
        filepath = settings_dir / filename
        if filepath.exists():
            try:
                content = filepath.read_text(encoding="utf-8")
                # 添加文件标识
                contents.append(f"\n### {filename}\n{content}")
                logger.debug(f"读取设定文件: {filepath}")
            except Exception as e:
                logger.warning(f"读取设定文件失败 {filepath}: {e}")

    # 合并内容（不做截断）
    combined = "\n".join(contents)

    return combined.strip()


def _call_llm(
    messages: list[dict[str, str]], llm_config: dict, llm_model: str | None = None
) -> str:
    """调用 LLM API 生成内容。

    支持 OpenAI 兼容协议的 API 调用，以 system/user 双消息架构发送请求。

    Args:
        messages: 消息列表，包含 system 和 user 两条消息。
        llm_config: LLM 配置，包含 base_url、api_key 和 model。
        llm_model: 模型名称（可选，覆盖 llm_config 中的 model）。

    Returns:
        LLM 返回的文本内容。

    Raises:
        httpx.HTTPStatusError: API 请求失败。
    """
    base_url = llm_config.get("base_url", "")
    api_key = llm_config.get("api_key", "")
    model_name = llm_model or llm_config.get("model", "deepseek-v3")

    if not base_url or not api_key:
        raise ValueError("LLM 配置缺少 base_url 或 api_key")

    # 构建请求 URL
    url = f"{base_url.rstrip('/')}/chat/completions"

    # 构建请求体（使用 system + user 双消息）
    payload = {
        "model": model_name,
        "messages": messages,
        "max_tokens": llm_config.get("max_tokens", 4096),
        "temperature": llm_config.get("temperature", 0.7),
    }
    # 思考型模型参数
    if llm_config.get("reasoning_effort"):
        payload["reasoning_effort"] = llm_config["reasoning_effort"]
    if llm_config.get("extra_body"):
        payload["extra_body"] = llm_config["extra_body"]

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    # 发送请求
    timeout = httpx.Timeout(120.0, connect=30.0)
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    # 解析响应
    if "choices" not in data or not data["choices"]:
        raise ValueError("LLM 响应格式异常：缺少 choices")

    content = data["choices"][0].get("message", {}).get("content", "")
    return content


def _parse_llm_response(response: str, level: int) -> list[dict]:
    """解析 LLM 返回的问题列表 JSON。

    Args:
        response: LLM 返回的文本内容。
        level: 难度等级，用于日志记录。

    Returns:
        解析后的问题字典列表。
    """
    logger.debug(f"解析 Level {level} 的 LLM 响应...")

    # 尝试直接解析 JSON
    try:
        data = json.loads(response)
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        pass

    # 尝试从文本中提取 JSON 数组
    # 匹配 [...] 格式
    json_pattern = r"\[[\s\S]*\]"
    match = re.search(json_pattern, response)
    if match:
        try:
            data = json.loads(match.group())
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass

    # 尝试逐个解析 JSON 对象
    # 匹配 {...} 格式
    obj_pattern = r"\{[\s\S]*?\}"
    objects = re.findall(obj_pattern, response)
    if objects:
        cases = []
        for obj_str in objects:
            try:
                obj = json.loads(obj_str)
                if "question" in obj:
                    cases.append(obj)
            except json.JSONDecodeError:
                continue
        return cases

    logger.warning(f"无法解析 LLM 响应为 JSON: {response[:200]}...")
    return []


def _validate_tool_types(raw_cases: list[dict], level: int) -> list[dict]:
    """校验并修正 expected_tool_types。

    确保生成的工具类型组合符合该 level 的规范。

    Args:
        raw_cases: LLM 返回的原始问题列表。
        level: 难度等级。

    Returns:
        校验后的问题列表。
    """
    allowed = ALLOWED_TOOL_COMBINATIONS.get(level, [["entity"]])

    for raw in raw_cases:
        tool_types = raw.get("expected_tool_types", ["entity"])

        # 如果不在允许列表中，选择最接近的有效组合
        if tool_types not in allowed:
            # 降级策略：按 entity -> graph -> corpus 顺序截断
            valid_types = []
            for t in ["entity", "graph", "corpus"]:
                if t in tool_types:
                    valid_types.append(t)
                if valid_types in allowed:
                    break
            raw["expected_tool_types"] = valid_types if valid_types else ["entity"]
            logger.debug(f"修正工具类型: {tool_types} -> {raw['expected_tool_types']}")

    return raw_cases


def _format_level_examples(level: int) -> str:
    """格式化示例问题为 prompt 中的文本。

    Args:
        level: 难度等级。

    Returns:
        格式化后的示例文本。
    """
    examples = LEVEL_EXAMPLES.get(level, [])
    if not examples:
        return "无"

    lines = []
    for ex in examples:
        tool_types = ", ".join(ex["expected_tool_types"])
        lines.append(f"- {ex['question']} (工具类型: {tool_types})")
    return "\n".join(lines)


def _run_entity_validation(cases: list[EvalCase], book: str = "") -> list[EvalCase]:
    """对 involved_entities 调用 SearchNovel 底层函数做实体验证。

    通过 NovelStore.search_entities_by_name() 对每个用例的 involved_entities
    逐个进行名称搜索，将命中数和 top 结果写入 validation 字段。

    内部使用 asyncio.run() 包裹异步实现，以便在同步函数 generate_cases 中调用。

    Args:
        cases: 待验证的测试用例列表。
        book: 书名，用于搜索时过滤书籍范围。

    Returns:
        填充了 validation 字段的测试用例列表。
    """
    return asyncio.run(_run_entity_validation_async(cases, book))


async def _run_entity_validation_async(cases: list[EvalCase], book: str = "") -> list[EvalCase]:
    """实体验证的异步实现。

    创建 NovelStore 实例，对每个用例的 involved_entities 逐个调用
    search_entities_by_name() 进行名称搜索，收集结果后关闭连接。

    Args:
        cases: 待验证的测试用例列表。
        book: 书名，用于搜索时过滤书籍范围。

    Returns:
        填充了 validation 字段的测试用例列表。
    """
    from novel_cli.config import NovelDBConfig
    from novel_cli.store import NovelStore

    db_config = NovelDBConfig()
    store = NovelStore(db_config)

    try:
        await store.connect()

        for case in cases:
            entities = case.meta.get("involved_entities", [])
            if not entities:
                case.validation = {
                    "entity_hits": 0,
                    "top_hit": "",
                    "status": "无涉及实体",
                }
                continue

            # 对每个实体名称进行搜索
            total_hits = 0
            top_hit = ""
            for entity_name in entities:
                try:
                    results = await store.search_entities_by_name(
                        name=entity_name,
                        book=book or case.book,
                    )
                    total_hits += len(results)
                    # 取第一个结果作为 top_hit
                    if results and not top_hit:
                        first = results[0]
                        desc_preview = (first.description or "")[:100]
                        top_hit = f"{first.name}: {desc_preview}"
                except Exception as e:
                    logger.warning(f"实体验证搜索失败 '{entity_name}': {e}")

            case.validation = {
                "entity_hits": total_hits,
                "top_hit": top_hit,
                "status": "已验证" if total_hits > 0 else "未命中",
                "entities_to_check": entities,
            }
    except Exception as e:
        logger.error(f"实体验证连接失败: {e}")
        # 连接失败时，所有用例标记为验证失败
        for case in cases:
            entities = case.meta.get("involved_entities", [])
            case.validation = {
                "entity_hits": -1,
                "top_hit": "",
                "status": f"验证失败: {e}",
                "entities_to_check": entities,
            }
    finally:
        try:
            await store.close()
        except Exception:
            pass

    return cases


def save_cases_yaml(
    base_dir: Path,
    cases: list[EvalCase],
    book: str,
    scenario: str,
    existing_cases: list[EvalCase] | None = None,
) -> None:
    """保存 YAML 用例集。

    将测试用例列表保存为 YAML 格式，按书名/场景分层组织目录结构。
    支持追加模式：当 existing_cases 不为 None 时，将其与 cases 合并后保存。

    Args:
        base_dir: eval_cases 目录路径。
        cases: 新生成的用例列表。
        book: 书名。
        scenario: 场景名称，如 "level_1_entity"。
        existing_cases: 已有用例列表。不为 None 时合并 existing_cases + cases 后保存。
    """
    # 场景名 → 难度等级（用于 YAML 数据）
    level = SCENARIO_LEVEL_MAP.get(scenario, 1)

    # 目录结构：eval_cases/<书名>/<scenario>.yaml
    book_dir = base_dir / book
    book_dir.mkdir(parents=True, exist_ok=True)

    # 根据 scenario 确定文件名
    filename = get_scenario_filename(scenario)
    path = book_dir / filename

    # 合并已有用例（追加模式）
    all_cases = (existing_cases or []) + cases

    # YAML 结构
    data = {
        "book": book,
        "level": level,
        "description": f"Level {level} 测试用例",
        "cases": [
            {
                "id": c.id,
                "question": c.question,
                "meta": c.meta,
                "_validation": c.validation,
            }
            for c in all_cases
        ],
    }

    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    logger.info(f"保存用例集到: {path}")


def load_cases_yaml(path: Path) -> list[EvalCase]:
    """加载 YAML 用例集。

    从 YAML 文件加载测试用例列表。

    Args:
        path: YAML 文件路径。

    Returns:
        EvalCase 列表。
    """
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    cases = []
    for c in data.get("cases", []):
        cases.append(
            EvalCase(
                id=c["id"],
                question=c["question"],
                book=data["book"],
                meta=c.get("meta", {}),
                validation=c.get("_validation"),
            )
        )
    return cases


def generate_all_scenarios(
    book: str,
    settings_dir: Path,
    llm_config: dict,
    scenarios: list[str] | None = None,
    llm_model: str | None = None,
    run_validation: bool = False,
    instruction: str | None = None,
) -> dict[str, list[EvalCase]]:
    """生成所有场景的测试用例。

    Args:
        book: 书名。
        settings_dir: 设定文件目录。
        llm_config: LLM 配置（用于生成问题）。
        scenarios: 要生成的场景列表，默认全部场景。
        llm_model: 调用 LLM 时使用的模型名称。
        run_validation: 是否运行实体验证。
        instruction: 用户自定义指令，用于引导问题生成方向。

    Returns:
        字典，键为场景名，值为用例列表。
    """
    if scenarios is None:
        scenarios = list(SCENARIO_LEVEL_MAP.keys())

    results = {}
    for scenario in scenarios:
        cases = generate_cases(
            book=book,
            settings_dir=settings_dir,
            scenario=scenario,
            llm_config=llm_config,
            llm_model=llm_model,
            instruction=instruction,
        )

        if run_validation:
            cases = _run_entity_validation(cases, book=book)

        results[scenario] = cases

    return results