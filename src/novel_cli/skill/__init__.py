"""Skill 规范发现与加载模块。

提供 Skill 的目录发现、文件解析和 Flow 图解析功能。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Sequence
from pathlib import Path
from typing import Literal

from kaos import get_current_kaos
from kaos.local import local_kaos
from kaos.path import KaosPath
from pydantic import BaseModel, ConfigDict

from novel_cli import logger
from novel_cli.skill.flow import Flow, FlowError
from novel_cli.skill.flow.d2 import parse_d2_flowchart
from novel_cli.skill.flow.mermaid import parse_mermaid_flowchart
from novel_cli.utils.frontmatter import parse_frontmatter

# Skill 类型枚举
SkillType = Literal["standard", "flow"]


def get_builtin_skills_dir() -> Path:
    """获取内置 Skills 目录路径。

    Returns:
        内置 Skills 目录路径对象。
    """
    return Path(__file__).parent.parent / "skills"


def _get_user_generic_skills_dir_candidates() -> tuple[KaosPath, ...]:
    """获取用户级通用 Skills 目录候选列表（按优先级排序）。

    通用组：``~/.config/agents/skills`` > ``~/.agents/skills``

    Returns:
        KaosPath 目录候选元组。
    """
    return (
        KaosPath.home() / ".config" / "agents" / "skills",
        KaosPath.home() / ".agents" / "skills",
    )


def _get_user_brand_skills_dir_candidates() -> tuple[KaosPath, ...]:
    """获取用户级品牌 Skills 目录候选列表（按优先级排序）。

    品牌组：``~/.novel/skills`` > ``~/.claude/skills`` > ``~/.codex/skills``

    Returns:
        KaosPath 目录候选元组。
    """
    return (
        KaosPath.home() / ".novel" / "skills",
        KaosPath.home() / ".claude" / "skills",
        KaosPath.home() / ".codex" / "skills",
    )


def _get_project_generic_skills_dir_candidates(work_dir: KaosPath) -> tuple[KaosPath, ...]:
    """获取项目级通用 Skills 目录候选列表。

    通用组：``.agents/skills``

    Args:
        work_dir: 项目工作目录。

    Returns:
        KaosPath 目录候选元组。
    """
    return (work_dir / ".agents" / "skills",)


def _get_project_brand_skills_dir_candidates(work_dir: KaosPath) -> tuple[KaosPath, ...]:
    """获取项目级品牌 Skills 目录候选列表（按优先级排序）。

    品牌组：``.novel/skills`` > ``.claude/skills`` > ``.codex/skills``

    Args:
        work_dir: 项目工作目录。

    Returns:
        KaosPath 目录候选元组。
    """
    return (
        work_dir / ".novel" / "skills",
        work_dir / ".claude" / "skills",
        work_dir / ".codex" / "skills",
    )


def _supports_builtin_skills() -> bool:
    """判断当前 KAOS backend 是否支持内置 Skills。

    Returns:
        支持时返回 True，否则返回 False。
    """
    current_name = get_current_kaos().name
    return current_name in (local_kaos.name, "acp")


async def find_first_existing_dir(candidates: Iterable[KaosPath]) -> KaosPath | None:
    """从候选列表中返回首个存在的目录。

    Args:
        candidates: 目录候选列表。

    Returns:
        首个存在的目录路径，无匹配时返回 None。
    """
    for candidate in candidates:
        if await candidate.is_dir():
            return candidate
    return None


async def find_user_skills_dirs(
    *,
    merge_brands: bool = False,
) -> list[KaosPath]:
    """返回用户级 Skills 目录列表（包含品牌和通用组）。

    品牌组优先，因为品牌特定目录具有更高的特异性。
    当 *merge_brands* 为 ``False``（默认）时，仅使用首个存在的品牌目录。
    当 ``True`` 时，包含所有存在的品牌目录（优先级：novel > claude > codex）。

    Args:
        merge_brands: 是否合并所有品牌目录。

    Returns:
        用户级 Skills 目录列表。
    """
    dirs: list[KaosPath] = []
    if merge_brands:
        for candidate in _get_user_brand_skills_dir_candidates():
            if await candidate.is_dir():
                dirs.append(candidate)
    else:
        if brand := await find_first_existing_dir(
            _get_user_brand_skills_dir_candidates(),
        ):
            dirs.append(brand)
    if generic := await find_first_existing_dir(
        _get_user_generic_skills_dir_candidates(),
    ):
        dirs.append(generic)
    return dirs


async def find_project_skills_dirs(
    work_dir: KaosPath,
    *,
    merge_brands: bool = False,
) -> list[KaosPath]:
    """返回项目级 Skills 目录列表（包含品牌和通用组）。

    品牌组优先，因为品牌特定目录具有更高的特异性。
    当 *merge_brands* 为 ``False``（默认）时，仅使用首个存在的品牌目录。
    当 ``True`` 时，包含所有存在的品牌目录（优先级：novel > claude > codex）。

    Args:
        work_dir: 项目工作目录。
        merge_brands: 是否合并所有品牌目录。

    Returns:
        项目级 Skills 目录列表。
    """
    dirs: list[KaosPath] = []
    brand_candidates = _get_project_brand_skills_dir_candidates(work_dir)
    if merge_brands:
        for candidate in brand_candidates:
            if await candidate.is_dir():
                dirs.append(candidate)
    else:
        if brand := await find_first_existing_dir(brand_candidates):
            dirs.append(brand)
    generic_candidates = _get_project_generic_skills_dir_candidates(work_dir)
    if generic := await find_first_existing_dir(generic_candidates):
        dirs.append(generic)
    return dirs


async def resolve_skills_roots(
    work_dir: KaosPath,
    *,
    skills_dirs: Sequence[KaosPath] | None = None,
    merge_brands: bool = False,
) -> list[KaosPath]:
    """解析分层 Skill 根目录（按优先级排序）。

    当 KAOS backend 支持时，内置 Skills 优先加载。
    当通过 ``--skills-dir`` 提供自定义目录时，将覆盖用户/项目发现。
    否则，用户级和项目级目录从两个独立组（品牌和通用）发现并合并——
    品牌目录优先，其 Skills 具有更高优先级。
    当 *merge_brands* 为 ``True`` 时，加载所有存在的品牌目录而非仅首个。
    Plugins 始终可被发现。

    Args:
        work_dir: 项目工作目录。
        skills_dirs: 自定义 Skills 目录列表（覆盖默认发现）。
        merge_brands: 是否合并所有品牌目录。

    Returns:
        Skills 根目录列表（按优先级排序）。
    """
    from novel_cli.plugin.manager import get_plugins_dir

    roots: list[KaosPath] = []
    if _supports_builtin_skills():
        roots.append(KaosPath.unsafe_from_local_path(get_builtin_skills_dir()))
    if skills_dirs:
        roots.extend(skills_dirs)
    else:
        roots.extend(await find_user_skills_dirs(merge_brands=merge_brands))
        roots.extend(
            await find_project_skills_dirs(work_dir, merge_brands=merge_brands),
        )
    # Plugins 始终可被发现
    plugins_path = get_plugins_dir()
    if plugins_path.is_dir():
        roots.append(KaosPath.unsafe_from_local_path(plugins_path))
    return roots


def normalize_skill_name(name: str) -> str:
    """规范化 Skill 名称用于查找。

    Args:
        name: 原始 Skill 名称。

    Returns:
        规范化后的 Skill 名称（小写）。
    """
    return name.casefold()


def index_skills(skills: Iterable[Skill]) -> dict[str, Skill]:
    """构建 Skill 名称查找表。

    Args:
        skills: Skill 集合。

    Returns:
        以规范化名称为键的 Skill 查找字典。
    """
    return {normalize_skill_name(skill.name): skill for skill in skills}


async def discover_skills_from_roots(skills_dirs: Iterable[KaosPath]) -> list[Skill]:
    """从多个根目录发现 Skills。

    Args:
        skills_dirs: Skills 根目录列表。

    Returns:
        按名称排序的 Skill 列表。
    """
    skills_by_name: dict[str, Skill] = {}
    for skills_dir in skills_dirs:
        for skill in await discover_skills(skills_dir):
            skills_by_name.setdefault(normalize_skill_name(skill.name), skill)
    return sorted(skills_by_name.values(), key=lambda s: s.name)


async def read_skill_text(skill: Skill) -> str | None:
    """读取 Skill 的 SKILL.md 内容。

    Args:
        skill: Skill 对象。

    Returns:
        SKILL.md 文件内容，读取失败时返回 None。
    """
    try:
        return (await skill.skill_md_file.read_text(encoding="utf-8")).strip()
    except OSError as exc:
        logger.warning(
            "Failed to read skill file {path}: {error}",
            path=skill.skill_md_file,
            error=exc,
        )
        return None


class Skill(BaseModel):
    """Skill 信息模型。

    表示单个 Skill 的元数据和路径信息。

    Attributes:
        name: Skill 名称。
        description: Skill 描述。
        type: Skill 类型（standard 或 flow）。
        dir: Skill 目录路径。
        flow: Flow 图结构（仅 flow 类型）。
    """

    model_config = ConfigDict(extra="ignore", arbitrary_types_allowed=True)

    name: str
    description: str
    type: SkillType = "standard"
    dir: KaosPath
    flow: Flow | None = None

    @property
    def skill_md_file(self) -> KaosPath:
        """SKILL.md 文件路径。

        Returns:
            SKILL.md 文件路径对象。
        """
        return self.dir / "SKILL.md"


async def discover_skills(skills_dir: KaosPath) -> list[Skill]:
    """发现指定目录中的所有 Skills。

    Args:
        skills_dir: Skills 目录路径。

    Returns:
        按名称排序的 Skill 列表。
    """
    if not await skills_dir.is_dir():
        return []

    skills: list[Skill] = []

    async for skill_dir in skills_dir.iterdir():
        if not await skill_dir.is_dir():
            continue

        skill_md = skill_dir / "SKILL.md"
        if not await skill_md.is_file():
            continue

        try:
            content = await skill_md.read_text(encoding="utf-8")
            skills.append(parse_skill_text(content, dir_path=skill_dir))
        except Exception as exc:
            logger.info("Skipping invalid skill at {}: {}", skill_md, exc)
            continue

    return sorted(skills, key=lambda s: s.name)


def parse_skill_text(content: str, *, dir_path: KaosPath) -> Skill:
    """解析 SKILL.md 内容提取名称和描述。

    Args:
        content: SKILL.md 文件内容。
        dir_path: Skill 目录路径。

    Returns:
        解析后的 Skill 对象。

    Raises:
        ValueError: 当 skill_type 无效时抛出。
    """
    frontmatter = parse_frontmatter(content) or {}

    name = frontmatter.get("name") or dir_path.name
    description = frontmatter.get("description") or "No description provided."
    skill_type = frontmatter.get("type") or "standard"
    if skill_type not in ("standard", "flow"):
        raise ValueError(f'Invalid skill type "{skill_type}"')
    flow = None
    if skill_type == "flow":
        try:
            flow = _parse_flow_from_skill(content)
        except ValueError as exc:
            logger.error("Failed to parse flow skill {name}: {error}", name=name, error=exc)
            skill_type = "standard"
            flow = None

    return Skill(
        name=name,
        description=description,
        type=skill_type,
        dir=dir_path,
        flow=flow,
    )


def _parse_flow_from_skill(content: str) -> Flow:
    """从 Skill 内容解析 Flow 图。

    Args:
        content: SKILL.md 文件内容。

    Returns:
        解析后的 Flow 对象。

    Raises:
        ValueError: 当内容中缺少 mermaid 或 d2 代码块时抛出。
    """
    for lang, code in _iter_fenced_codeblocks(content):
        if lang == "mermaid":
            return _parse_flow_block(parse_mermaid_flowchart, code)
        if lang == "d2":
            return _parse_flow_block(parse_d2_flowchart, code)
    raise ValueError("Flow skills require a mermaid or d2 code block in SKILL.md.")


def _parse_flow_block(parser: Callable[[str], Flow], code: str) -> Flow:
    """解析 Flow 代码块。

    Args:
        parser: Flow 解析函数。
        code: 代码块内容。

    Returns:
        解析后的 Flow 对象。

    Raises:
        ValueError: 当 Flow 图无效时抛出。
    """
    try:
        return parser(code)
    except FlowError as exc:
        raise ValueError(f"Invalid flow diagram: {exc}") from exc


def _iter_fenced_codeblocks(content: str) -> Iterator[tuple[str, str]]:
    """遍历内容中的所有围栏代码块。

    Args:
        content: 文本内容。

    Yields:
        (语言标识, 代码内容) 元组。
    """
    fence = ""
    fence_char = ""
    lang = ""
    buf: list[str] = []
    in_block = False

    for line in content.splitlines():
        stripped = line.lstrip()
        if not in_block:
            if match := _parse_fence_open(stripped):
                fence, fence_char, info = match
                lang = _normalize_code_lang(info)
                in_block = True
                buf = []
            continue

        if _is_fence_close(stripped, fence_char, len(fence)):
            yield lang, "\n".join(buf).strip("\n")
            in_block = False
            fence = ""
            fence_char = ""
            lang = ""
            buf = []
            continue

        buf.append(line)


def _normalize_code_lang(info: str) -> str:
    """规范化代码块语言标识。

    Args:
        info: 语言信息字符串。

    Returns:
        规范化后的语言标识。
    """
    if not info:
        return ""
    lang = info.split()[0].strip().lower()
    if lang.startswith("{") and lang.endswith("}"):
        lang = lang[1:-1].strip()
    return lang


def _parse_fence_open(line: str) -> tuple[str, str, str] | None:
    """解析围栏代码块开始标记。

    Args:
        line: 行内容。

    Returns:
        (围栏串, 围栏字符, 信息串) 元组，无匹配时返回 None。
    """
    if not line or line[0] not in ("`", "~"):
        return None
    fence_char = line[0]
    count = 0
    for ch in line:
        if ch == fence_char:
            count += 1
        else:
            break
    if count < 3:
        return None
    fence = fence_char * count
    info = line[count:].strip()
    return fence, fence_char, info


def _is_fence_close(line: str, fence_char: str, fence_len: int) -> bool:
    """判断是否为围栏代码块结束标记。

    Args:
        line: 行内容。
        fence_char: 围栏字符。
        fence_len: 围栏长度。

    Returns:
        是结束标记时返回 True，否则返回 False。
    """
    if not fence_char or not line or line[0] != fence_char:
        return False
    count = 0
    for ch in line:
        if ch == fence_char:
            count += 1
        else:
            break
    if count < fence_len:
        return False
    return not line[count:].strip()
