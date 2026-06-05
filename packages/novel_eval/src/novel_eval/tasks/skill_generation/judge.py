"""5 维度 Judge 评估器：基于工具调用记录进行评分。

该模块负责：
1. 加载 SKILL.md 模板作为格式和完整性评估的参考依据
2. 加载工具描述（含 ReadChapter）注入 Judge prompt
3. 使用 LLM 对工具调用轨迹进行 5 维度评分

评分维度（每维度 1-5 分）：
- tool_selection: 工具选择是否合理
- param_quality: 参数构造是否精准
- call_efficiency: 调用链是否高效
- setting_completeness: 设定文档的完整性
- format_compliance: 格式合规性
"""

import json
import logging
import re
from pathlib import Path
from typing import Any

import httpx

from novel_eval.models import CaseResult, EvalScore, ToolCallRecord
from novel_eval.prompts import PromptLoader
from novel_eval.tasks.tool_usage.models import JudgeConfig

logger = logging.getLogger(__name__)

# SKILL.md 文件根目录（向上查找 .git 定位仓库根，再拼接 skills 路径）
def _find_repo_root() -> Path:
    """从当前文件向上遍历，查找包含 .git 的仓库根目录。"""
    current = Path(__file__).resolve().parent
    for parent in current.parents:
        if (parent / ".git").exists():
            return parent
    raise FileNotFoundError("无法定位仓库根目录（未找到 .git）")

_REPO_ROOT = _find_repo_root()
_SKILLS_DIR = _REPO_ROOT / "src" / "novel_cli" / "skills"
_TOOL_DESC_DIR = _REPO_ROOT / "src" / "novel_cli" / "tools" / "novel"

# 搜索工具描述文件路径及对应工具名
_TOOL_DESC_ITEMS = [
    ("SearchEntity", _TOOL_DESC_DIR / "entity.md"),
    ("SearchGraph", _TOOL_DESC_DIR / "graph.md"),
    ("SearchCorpus", _TOOL_DESC_DIR / "corpus.md"),
    ("ReadChapter", _TOOL_DESC_DIR / "chapter.md"),
]

# 5 维度评分键名
_SCORE_DIMENSIONS = [
    "tool_selection",
    "param_quality",
    "call_efficiency",
    "setting_completeness",
    "format_compliance",
]

# 技能生成的黄金调用路径模板（按 skill_type 分类）
GOLDEN_TRAJECTORIES = {
    "character": "角色设定理想路径: Entity → Graph概览 → Graph(rel)×2 → Corpus×5-8 → WriteFile（7-11 次）",
    "item": "物品设定理想路径: Entity → Graph概览 → Graph(rel)×2 → Corpus×4 → WriteFile（6 次）",
    "location": "地点设定理想路径: Entity → Graph概览 → Graph(rel)×2 → Corpus×6 → WriteFile（8 次）",
    "organization": "组织设定理想路径: Entity → Graph概览 → Graph(rel)×3 → Corpus×5 → WriteFile（12 次）",
    "skill": "功法设定理想路径: Entity → Graph概览 → Graph(rel)×3 → Corpus+ReadChapter → WriteFile（12 次）",
}

# 模块级 PromptLoader 单例
_PROMPT_LOADER = PromptLoader()

# SKILL.md 缓存
_SKILL_CACHE: dict[str, str] = {}


class SkillGenJudge:
    """技能生成评估的 Judge 评分器。

    Attributes:
        config: Judge LLM 配置。
    """

    def __init__(self, config: JudgeConfig) -> None:
        """初始化 Judge 评分器。

        Args:
            config: Judge LLM 配置，包含 model 和 api_key。
        """
        self.config = config

    def evaluate(
        self,
        case_result: CaseResult,
        skill_template: str,
        *,
        dimensions: list[str] | None = None,
        meta: dict | None = None,
    ) -> EvalScore:
        """评估单个用例结果。

        Args:
            case_result: 用例执行结果。
            skill_template: SKILL.md 模板内容。
            dimensions: 自定义评分维度键名列表。为 None 时使用默认 _SCORE_DIMENSIONS。
            meta: 用例元数据，包含 skill_type 等信息。

        Returns:
            EvalScore 评分结果。
        """
        return judge_case(
            case_result,
            self.config.model_dump(),
            skill_template=skill_template,
            dimensions=dimensions,
            meta=meta,
        )


def _load_tool_description() -> str:
    """加载搜索工具描述文本。

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


# 模块加载时缓存工具描述
_TOOL_DESCRIPTION = _load_tool_description()


def load_skill_template(skill_type: str) -> str:
    """根据 skill_type 加载对应的 SKILL.md 模板内容。

    Args:
        skill_type: 技能类型，如 "generate-character"。

    Returns:
        SKILL.md 文件内容。文件不存在时返回提示信息。
    """
    if skill_type in _SKILL_CACHE:
        return _SKILL_CACHE[skill_type]

    skill_path = _SKILLS_DIR / skill_type / "SKILL.md"
    try:
        content = skill_path.read_text(encoding="utf-8")
        _SKILL_CACHE[skill_type] = content
        return content
    except FileNotFoundError:
        logger.warning(f"SKILL.md 未找到: {skill_path}")
        _SKILL_CACHE[skill_type] = "（模板文件未找到）"
        return "（模板文件未找到）"


def _extract_skill_type(case_id: str) -> str:
    """从 case_id 中提取技能类型。

    支持 "SG-{type}-{seq}" 格式，如 "SG-character-001" → "character"。

    Args:
        case_id: 用例 ID。

    Returns:
        技能类型字符串，无法解析时返回空字符串。
    """
    parts = case_id.split("-")
    if parts and parts[0] == "SG" and len(parts) >= 2:
        return parts[1]
    return ""


def judge_case(
    case_result: CaseResult,
    judge_config: dict[str, Any],
    skill_template: str = "",
    *,
    dimensions: list[str] | None = None,
    meta: dict | None = None,
) -> EvalScore:
    """评估单个用例结果。

    基于 agent 的工具调用记录，使用 LLM 进行 5 维度评分。

    Args:
        case_result: 用例执行结果。
        judge_config: Judge LLM 配置。
        skill_template: SKILL.md 模板内容。
        dimensions: 自定义评分维度键名列表。为 None 时使用默认 _SCORE_DIMENSIONS。
        meta: 用例元数据，包含 skill_type 等信息。

    Returns:
        EvalScore 评分结果。
    """
    # 边界情况：无工具调用，跳过 LLM 调用节省 API 开销
    if not case_result.tool_calls:
        if dimensions is None:
            dimensions = _SCORE_DIMENSIONS
        return EvalScore(
            case_id=case_result.case_id,
            scores={k: 1 for k in dimensions},
            total_score=1.0,
            commentary="未使用工具",
        )

    # 从 meta 或 case_id 提取 skill_type，映射到 golden trajectory
    skill_type = ""
    if meta:
        # meta 中可能有 skill_type（来自 EvalCase.meta）
        raw_type = meta.get("skill_type", "")
        # "generate-character" → "character"
        if raw_type.startswith("generate-"):
            skill_type = raw_type[len("generate-"):]
        else:
            skill_type = raw_type
    if not skill_type:
        skill_type = _extract_skill_type(case_result.case_id)
    golden_trajectory = GOLDEN_TRAJECTORIES.get(skill_type, "无特定理想路径参考")

    # 通过 PromptLoader 渲染 system + user 双消息
    tool_calls_str = _format_tool_calls(case_result.tool_calls)
    generated_content = _extract_written_content(case_result.tool_calls)
    messages = _PROMPT_LOADER.render_messages(
        "skill_gen_judge",
        skill_template=skill_template,
        tool_description=_TOOL_DESCRIPTION,
        question=case_result.question,
        tool_calls_str=tool_calls_str,
        generated_content=generated_content,
        golden_trajectory=golden_trajectory,
    )

    # 调用 Judge LLM
    try:
        response_text = _call_judge_llm(messages, judge_config)
    except Exception as e:
        logger.error(f"Judge LLM 调用失败: {e}")
        return EvalScore(
            case_id=case_result.case_id,
            scores={k: 3 for k in (dimensions or _SCORE_DIMENSIONS)},
            total_score=3.0,
            commentary=f"Judge LLM 调用失败: {e}",
        )

    # 解析响应
    scores, commentary = _parse_judge_response(response_text, dimensions=dimensions)

    # 计算总分（5 维度平均）
    total_score = sum(scores.values()) / len(scores) if scores else 3.0

    return EvalScore(
        case_id=case_result.case_id,
        scores=scores,
        total_score=round(total_score, 2),
        commentary=commentary,
    )


def _call_judge_llm(messages: list[dict[str, str]], judge_config: dict) -> str:
    """调用 Judge LLM API 进行评分。

    Args:
        messages: 消息列表，包含 system 和 user 两条消息。
        judge_config: Judge LLM 配置。

    Returns:
        LLM 返回的评分文本。

    Raises:
        ValueError: 配置缺少 base_url 或 api_key。
    """
    base_url = judge_config.get("base_url", "")
    api_key = judge_config.get("api_key", "")
    model_name = judge_config.get("model", "claude-sonnet-4-20250514")

    if not base_url or not api_key:
        raise ValueError("Judge LLM 配置缺少 base_url 或 api_key")

    url = f"{base_url.rstrip('/')}/chat/completions"

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

    timeout = httpx.Timeout(120.0, connect=30.0)
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    if "choices" not in data or not data["choices"]:
        raise ValueError("Judge LLM 响应格式异常：缺少 choices")

    return data["choices"][0].get("message", {}).get("content", "")


def _extract_written_content(tool_calls: list[ToolCallRecord]) -> str:
    """从工具调用记录中提取所有 WriteFile 写入的内容。

    遍历 tool_calls，收集所有 WriteFile 调用的 params.content，
    按步骤顺序拼接为完整的生成文档内容。

    Args:
        tool_calls: 工具调用记录列表。

    Returns:
        拼接后的生成文档内容。无 WriteFile 调用时返回空字符串。
    """
    parts: list[str] = []
    for tc in tool_calls:
        if tc.tool_name == "WriteFile" and tc.params.get("content"):
            parts.append(tc.params["content"])
    return "\n\n---\n\n".join(parts)


def _format_tool_calls(tool_calls: list[ToolCallRecord], max_calls: int = 30) -> str:
    """格式化工具调用记录。

    超过 max_calls 时截断并标注，避免 prompt 过长。
    WriteFile 步骤只显示 path，内容通过 <generated_content> 单独展示。

    Args:
        tool_calls: 工具调用记录列表。
        max_calls: 最大显示调用次数，默认 30。

    Returns:
        格式化后的工具调用记录字符串。
    """
    def _format_step(tc: ToolCallRecord) -> list[str]:
        """格式化单个工具调用步骤。"""
        lines = []
        if tc.tool_name == "WriteFile":
            # WriteFile 只显示 path，内容在 <generated_content> 区块
            path = tc.params.get("path", "")
            lines.append(f"步骤 {tc.step}: tool=WriteFile, path='{path}' (内容见下方 <generated_content>)")
        else:
            lines.append(f"步骤 {tc.step}: tool={tc.tool_name}, params={tc.params}")
            if tc.result_summary:
                lines.append(f"  结果摘要: {tc.result_summary}")
        return lines

    if not tool_calls:
        return "（无工具调用）"

    if len(tool_calls) > max_calls:
        shown = tool_calls[:max_calls]
        lines: list[str] = []
        for tc in shown:
            lines.extend(_format_step(tc))
        lines.append(f"... 省略 {len(tool_calls) - max_calls} 次调用 ...")
        return "\n".join(lines)

    lines = []
    for tc in tool_calls:
        lines.extend(_format_step(tc))
    return "\n".join(lines)


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
        元组 (scores, commentary)。
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

    # 层级 4：兜底默认分数
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
            scores[key] = max(1, min(5, score))
        else:
            scores[key] = 3

    commentary = data.get("commentary", "无法解析评分")
    if not isinstance(commentary, str):
        commentary = str(commentary)

    return scores, commentary
