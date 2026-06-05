"""LLM Judge 评分器：基于工具调用记录进行 4 维度评分。

该模块负责：
1. 接收用例执行结果（包含工具调用记录和最终回答）
2. 通过 PromptLoader 渲染 Jinja2 模板构建 system/user 双消息，调用 LLM 进行评分
3. 使用 JSON 优先多层解析策略解析评分结果，返回结构化的 EvalScore

评分维度（每维度 1-5 分）：
- tool_selection: 工具选择是否正确
- param_quality: 参数构造是否合理
- call_efficiency: 调用链是否高效
- result_utilization: 结果利用是否充分
"""

import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from novel_eval.models import CaseResult, EvalScore, ToolCallRecord
from novel_eval.prompts import PromptLoader
from novel_eval.tasks.tool_usage.models import JudgeConfig

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class Judge:
    """LLM Judge 评分器。

    基于 agent 的工具调用记录，使用 LLM 进行多维度评分。

    Attributes:
        config: Judge LLM 配置。
    """

    def __init__(self, config: JudgeConfig) -> None:
        """初始化 Judge 评分器。

        Args:
            config: Judge LLM 配置，包含 model 和 api_key。
        """
        self.config = config

    def evaluate(self, case_result: CaseResult, *, dimensions: list[str] | None = None, meta: dict | None = None) -> EvalScore:
        """评估单个用例结果。

        Args:
            case_result: 用例执行结果。
            dimensions: 自定义评分维度键名列表。为 None 时使用默认 _SCORE_DIMENSIONS。
            meta: 用例元数据，包含 expected_tool_types / involved_entities 等信息。

        Returns:
            EvalScore 评分结果。
        """
        return judge_case(case_result, self.config.model_dump(), dimensions=dimensions, meta=meta)


# 搜索工具描述文件目录（相对于本文件向上 6 层到仓库根目录）
_TOOL_DESC_DIR = (
    Path(__file__).parents[6]
    / "src" / "novel_cli" / "tools" / "novel"
)

# 四个搜索工具的描述文件路径及对应工具名
_TOOL_DESC_ITEMS = [
    ("SearchEntity", _TOOL_DESC_DIR / "entity.md"),
    ("SearchGraph", _TOOL_DESC_DIR / "graph.md"),
    ("SearchCorpus", _TOOL_DESC_DIR / "corpus.md"),
    ("ReadChapter", _TOOL_DESC_DIR / "chapter.md"),
]

# 从 sessions 提炼的黄金调用路径模板（按 Level 分类）
GOLDEN_TRAJECTORIES = {
    "1": (
        "L1 理想路径: Entity → Corpus（2-3 次，简单事实可跳过 Graph）\n"
        "示例: 搜'如意金箍棒' → Corpus 搜'重量' → 回答"
    ),
    "2": (
        "L2 理想路径: Entity → Graph概览 → Graph(rel) → Corpus（4-5 次）\n"
        "示例: 搜'红孩儿' → Graph概览 → Graph(对立/克制) → Corpus 验证"
    ),
    "3": (
        "L3 理想路径: Entity → Graph概览 → Graph(rel) → Corpus × 重试 → ReadChapter 兜底（6-8 次）\n"
        "示例: 搜'铁扇公主' → Graph概览 → Graph(对立) → Corpus搜'芭蕉扇+定风丹' → ReadChapter 精确验证"
    ),
    "4": (
        "L4 理想路径: 多实体 Entity 并行 → Graph × N → Corpus × N → ReadChapter 兜底（10-14 次）\n"
        "示例: 分别搜'金刚琢''人种袋' → Graph各查关系 → Corpus对比原文"
    ),
}


def _load_tool_description() -> str:
    """加载搜索工具描述文本。

    从 entity.md、graph.md、corpus.md、chapter.md 四个文件读取工具的能力说明、
    参数定义和典型调用链路，拼接后用于注入 Judge Prompt，
    让 Judge 了解工具的正确使用方式。

    每个工具描述前添加 `## 工具名` 标题，便于 Judge 精确对应每步调用。

    Returns:
        拼接后的工具描述文本。文件不存在时返回提示信息。
    """
    parts = []
    missing = []
    for name, path in _TOOL_DESC_ITEMS:
        try:
            content = path.read_text(encoding="utf-8")
            parts.append(f"## {name}\n{content}")
        except FileNotFoundError:
            missing.append(str(path))
    if missing:
        return f"（工具描述文件未找到：{', '.join(missing)}）"
    return "\n\n".join(parts)


# 模块加载时缓存工具描述，避免每次评分都读取文件
_TOOL_DESCRIPTION = _load_tool_description()

# 模块级 PromptLoader 单例，Jinja2 Environment 内部缓存已解析模板
_PROMPT_LOADER = PromptLoader()


def _extract_level(case_id: str) -> str:
    """从 case_id 中提取难度等级。

    支持 "L{level}-{seq}" 格式，如 "L1-001" → "1"。

    Args:
        case_id: 用例 ID。

    Returns:
        难度等级字符串，无法解析时返回空字符串。
    """
    parts = case_id.split("-")
    if parts and parts[0].startswith("L"):
        return parts[0][1:]
    return ""


def judge_case(case_result: CaseResult, judge_config: dict, *, dimensions: list[str] | None = None, meta: dict | None = None) -> EvalScore:
    """评估单个用例结果。

    基于 agent 的工具调用记录，使用 LLM Judge 进行 4 维度评分。

    Args:
        case_result: 用例执行结果，包含问题、工具调用记录和最终回答。
        judge_config: Judge LLM 配置，包含 model 和 api_key_env 等字段。
        dimensions: 自定义评分维度键名列表。为 None 时使用默认 _SCORE_DIMENSIONS。
        meta: 用例元数据，包含 expected_tool_types / involved_entities 等信息。

    Returns:
        EvalScore 评分结果，包含各维度分数、总分和评语。
    """
    # 边界情况：无工具调用
    if not case_result.tool_calls:
        if dimensions is None:
            dimensions = _SCORE_DIMENSIONS
        return EvalScore(
            case_id=case_result.case_id,
            scores={k: 1 for k in dimensions},
            total_score=1.0,
            commentary="未使用工具",
        )

    # 从 case_id 提取 level，映射到 golden trajectory
    level = _extract_level(case_result.case_id)
    golden_trajectory = GOLDEN_TRAJECTORIES.get(level, "无特定理想路径参考")

    # 通过 PromptLoader 渲染 system + user 双消息
    tool_calls_str = _format_tool_calls(case_result.tool_calls)
    messages = _PROMPT_LOADER.render_messages(
        "judge",
        tool_description=_TOOL_DESCRIPTION,
        question=case_result.question,
        tool_calls_str=tool_calls_str,
        final_answer=case_result.final_answer,
        golden_trajectory=golden_trajectory,
        expected_tool_types=meta.get("expected_tool_types", []) if meta else [],
        involved_entities=meta.get("involved_entities", []) if meta else [],
    )

    # 调用 Judge LLM
    try:
        response_text = _call_judge_llm(messages, judge_config)
    except Exception as e:
        logger.error(f"Judge LLM 调用失败: {e}")
        # 调用失败时返回默认分数
        return EvalScore(
            case_id=case_result.case_id,
            scores={k: 3 for k in (dimensions or _SCORE_DIMENSIONS)},
            total_score=3.0,
            commentary=f"Judge LLM 调用失败: {e}",
        )

    # 解析响应
    scores, commentary = _parse_judge_response(response_text, dimensions=dimensions)

    # 计算四个维度的平均值作为总分
    total_score = sum(scores.values()) / len(scores) if scores else 3.0

    return EvalScore(
        case_id=case_result.case_id,
        scores=scores,
        total_score=round(total_score, 2),
        commentary=commentary,
    )


def _call_judge_llm(messages: list[dict[str, str]], judge_config: dict) -> str:
    """调用 Judge LLM API 进行评分。

    使用 httpx 调用 OpenAI 兼容协议的 API，以 system/user 双消息架构
    发送评分请求。

    Args:
        messages: 消息列表，包含 system 和 user 两条消息。
        judge_config: Judge LLM 配置，包含 model、base_url 和 api_key。

    Returns:
        LLM 返回的评分文本。

    Raises:
        ValueError: 配置缺少 base_url 或 api_key。
        httpx.HTTPStatusError: API 请求失败。
    """
    base_url = judge_config.get("base_url", "")
    api_key = judge_config.get("api_key", "")
    model_name = judge_config.get("model", "claude-sonnet-4-20250514")

    if not base_url or not api_key:
        raise ValueError("Judge LLM 配置缺少 base_url 或 api_key")

    # 构建请求 URL
    url = f"{base_url.rstrip('/')}/chat/completions"

    # 构建请求体（使用 system + user 双消息）
    payload = {
        "model": model_name,
        "messages": messages,
        "max_tokens": judge_config.get("max_tokens", 1024),
        "temperature": judge_config.get("temperature", 0.3),
    }
    # 思考型模型参数
    if judge_config.get("reasoning_effort"):
        payload["reasoning_effort"] = judge_config["reasoning_effort"]
    if judge_config.get("extra_body"):
        payload["extra_body"] = judge_config["extra_body"]

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
        raise ValueError("Judge LLM 响应格式异常：缺少 choices")

    content = data["choices"][0].get("message", {}).get("content", "")
    return content


def _format_tool_calls(tool_calls: list[ToolCallRecord], max_calls: int = 30) -> str:
    """格式化工具调用记录。

    将工具调用记录列表格式化为可读的字符串，用于 Judge prompt。
    超过 max_calls 时截断并标注，避免 80+ 次调用导致 prompt 过长。

    Args:
        tool_calls: 工具调用记录列表。
        max_calls: 最大显示调用次数，默认 30。

    Returns:
        格式化后的工具调用记录字符串。
    """
    if not tool_calls:
        return "（无工具调用）"

    if len(tool_calls) > max_calls:
        shown = tool_calls[:max_calls]
        lines = []
        for tc in shown:
            lines.append(f"步骤 {tc.step}: tool={tc.tool_name}, params={tc.params}")
            if tc.result_summary:
                lines.append(f"  结果摘要: {tc.result_summary}")
        lines.append(f"... 省略 {len(tool_calls) - max_calls} 次调用 ...")
        return "\n".join(lines)

    lines = []
    for tc in tool_calls:
        lines.append(f"步骤 {tc.step}: tool={tc.tool_name}, params={tc.params}")
        if tc.result_summary:
            lines.append(f"  结果摘要: {tc.result_summary}")
    return "\n".join(lines)


# 评分维度键名列表
_SCORE_DIMENSIONS = ["tool_selection", "param_quality", "call_efficiency", "result_utilization"]


def _parse_judge_response(response: str, *, dimensions: list[str] | None = None) -> tuple[dict[str, int], str]:
    """解析 Judge 返回的评分。

    采用 JSON 优先的多层解析策略：
    1. 直接 json.loads() 解析完整响应
    2. 提取 ```json``` 代码块后解析
    3. 正则提取第一个 {...} JSON 对象后解析
    4. 兜底：返回所有维度 3 分 + "无法解析评分" 评语

    Args:
        response: LLM Judge 的响应文本。
        dimensions: 自定义评分维度键名列表。为 None 时使用默认 _SCORE_DIMENSIONS。

    Returns:
        元组 (scores, commentary)，包含各维度分数字典和评语文本。
    """
    # 层级 1：直接 JSON 解析
    parsed = _try_parse_json(response)
    if parsed is not None:
        return _extract_scores_from_json(parsed, dimensions=dimensions)

    # 层级 2：提取 ```json``` 代码块
    code_block_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", response, re.DOTALL)
    if code_block_match:
        parsed = _try_parse_json(code_block_match.group(1).strip())
        if parsed is not None:
            return _extract_scores_from_json(parsed, dimensions=dimensions)

    # 层级 3：正则提取第一个 {...} JSON 对象
    json_obj_match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", response, re.DOTALL)
    if json_obj_match:
        parsed = _try_parse_json(json_obj_match.group(0))
        if parsed is not None:
            return _extract_scores_from_json(parsed, dimensions=dimensions)

    # 层级 4：兜底默认分数，保留原始响应便于排查
    logger.warning(f"无法解析 Judge 响应为 JSON: {response[:200]}...")
    default_scores = {k: 3 for k in (dimensions or _SCORE_DIMENSIONS)}
    return default_scores, f"无法解析评分，原始响应:\n{response}"


def _try_parse_json(text: str) -> dict | None:
    """尝试将文本解析为 JSON 字典。

    Args:
        text: 待解析的文本。

    Returns:
        解析成功的字典，失败返回 None。
    """
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, ValueError):
        pass
    return None


def _extract_scores_from_json(data: dict, *, dimensions: list[str] | None = None) -> tuple[dict[str, int], str]:
    """从 JSON 字典中提取评分和评语。

    Args:
        data: 解析后的 JSON 字典。
        dimensions: 自定义评分维度键名列表。为 None 时使用默认 _SCORE_DIMENSIONS。

    Returns:
        元组 (scores, commentary)。
    """
    scores = {}
    for key in (dimensions or _SCORE_DIMENSIONS):
        if key in data:
            score = int(data[key])
            # 约束分数在 1-5 范围内
            scores[key] = max(1, min(5, score))
        else:
            scores[key] = 3

    commentary = data.get("commentary", "无法解析评分")
    if not isinstance(commentary, str):
        commentary = str(commentary)

    return scores, commentary
