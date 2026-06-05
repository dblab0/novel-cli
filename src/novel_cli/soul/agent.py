"""智能体加载与运行时管理模块。

本模块提供智能体的加载、运行时初始化、系统提示渲染等功能。
支持从配置文件加载智能体、管理 MCP 工具、子智能体调度等特性。

主要类:
    Runtime: 智能体运行时环境，包含配置、会话、工具集等组件。
    Agent: 已加载的智能体实例，包含系统提示和工具集。
    BuiltinSystemPromptArgs: 内置系统提示参数数据类。

关键函数:
    load_agent: 从配置文件加载智能体。
    load_agents_md: 加载并合并 AGENTS.md 文件。
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import pydantic
from jinja2 import Environment as JinjaEnvironment
from jinja2 import FileSystemLoader, StrictUndefined, TemplateError, UndefinedError
from kaos.path import KaosPath

from novel_cli.agentspec import load_agent_spec
from novel_cli.approval_runtime import ApprovalRuntime
from novel_cli.background import BackgroundTaskManager
from novel_cli.config import Config
from novel_cli.exception import MCPConfigError, SystemPromptTemplateError
from novel_cli.llm import LLM
from novel_cli.notifications import NotificationManager
from novel_cli.session import Session
from novel_cli.skill import (
    Skill,
    discover_skills_from_roots,
    index_skills,
    resolve_skills_roots,
)
from novel_cli.soul.approval import Approval, ApprovalState
from novel_cli.soul.denwarenji import DenwaRenji
from novel_cli.soul.toolset import NovelToolset
from novel_cli.subagents.models import AgentTypeDefinition, ToolPolicy
from novel_cli.subagents.registry import LaborMarket
from novel_cli.subagents.store import SubagentStore
from novel_cli.utils.environment import Environment
from novel_cli.utils.logging import logger
from novel_cli.utils.path import is_within_directory, list_directory
from novel_cli.wire.root_hub import RootWireHub

if TYPE_CHECKING:
    from fastmcp.mcp_config import MCPConfig


@dataclass(frozen=True, slots=True, kw_only=True)
class BuiltinSystemPromptArgs:
    """内置系统提示参数数据类。

    用于模板渲染的系统提示参数集合。

    Attributes:
        NOVEL_NOW: 当前日期时间。
        NOVEL_WORK_DIR: 当前工作目录的绝对路径。
        NOVEL_WORK_DIR_LS: 当前工作目录的文件列表。
        NOVEL_AGENTS_MD: 合并后的 AGENTS.md 文件内容（从项目根目录到工作目录）。
        NOVEL_SKILLS: 可用技能的格式化信息。
        NOVEL_ADDITIONAL_DIRS_INFO: 工作空间中额外目录的格式化信息。
        NOVEL_OS: 操作系统类型，如 'Windows'、'macOS'、'Linux'。
        NOVEL_SHELL: Shell 工具使用的 shell 可执行文件，如 'bash (`/bin/bash`)'。
    """

    NOVEL_NOW: str
    """当前日期时间。"""
    NOVEL_WORK_DIR: KaosPath
    """当前工作目录的绝对路径。"""
    NOVEL_WORK_DIR_LS: str
    """当前工作目录的文件列表。"""
    NOVEL_AGENTS_MD: str  # TODO: 从系统提示移动到首条消息
    """合并后的 AGENTS.md 文件内容（从项目根目录到工作目录）。"""
    NOVEL_SKILLS: str
    """可用技能的格式化信息。"""
    NOVEL_ADDITIONAL_DIRS_INFO: str
    """工作空间中额外目录的格式化信息。"""
    NOVEL_OS: str
    """操作系统类型，如 'Windows'、'macOS'、'Linux'。"""
    NOVEL_SHELL: str
    """Shell 工具使用的 shell 可执行文件，如 'bash (`/bin/bash`)'。"""


_AGENTS_MD_MAX_BYTES = 32 * 1024  # 32 KiB，AGENTS.md 最大字节数限制


async def _find_project_root(work_dir: KaosPath) -> KaosPath:
    """从工作目录向上查找最近的包含 ``.git`` 的目录。

    若未找到 ``.git`` 标记则返回工作目录本身。

    Args:
        work_dir: 起始工作目录路径。

    Returns:
        项目根目录路径，若未找到则返回工作目录本身。
    """
    current = work_dir
    while True:
        if await (current / ".git").exists():
            return current
        parent = current.parent
        if parent == current:  # 文件系统根目录
            return work_dir
        current = parent


async def _dirs_root_to_leaf(work_dir: KaosPath, project_root: KaosPath) -> list[KaosPath]:
    """返回从项目根目录到工作目录（包含两端）的目录列表。

    Args:
        work_dir: 工作目录路径。
        project_root: 项目根目录路径。

    Returns:
        目录路径列表，顺序为根目录到叶节点。
    """
    dirs: list[KaosPath] = []
    current = work_dir
    while True:
        dirs.append(current)
        if current == project_root:
            break
        parent = current.parent
        if parent == current:
            break
        current = parent
    dirs.reverse()  # 根目录 → 叶节点
    return dirs


async def load_agents_md(work_dir: KaosPath) -> str | None:
    """发现并合并从项目根目录到工作目录的 ``AGENTS.md`` 文件。

    对于路径上的每个目录，按以下顺序检查候选文件：

    1. ``.novel/AGENTS.md``  — 项目本地 novel 配置（最高优先级）
    2. ``AGENTS.md``        — 标准位置
    3. ``agents.md``        — 小写变体（与选项 2 互斥）

    在单个目录内，``.novel/AGENTS.md`` 和 ``AGENTS.md``/``agents.md``
    **同时**加载（先加载 ``.novel/``），但 ``AGENTS.md`` 和 ``agents.md``
    互斥（大写优先）。

    所有发现的文件按根目录→叶节点顺序拼接，以 ``\\n\\n`` 分隔，
    并附带来源注释。总大小上限为 :data:`_AGENTS_MD_MAX_BYTES`。
    预算按叶节点优先分配，确保更深（更具体）的文件不会被截断。

    Args:
        work_dir: 工作目录路径。

    Returns:
        合并后的 AGENTS.md 内容，若无文件则返回 None。
    """
    project_root = await _find_project_root(work_dir)
    dirs = await _dirs_root_to_leaf(work_dir, project_root)

    # 第一阶段：收集所有候选文件（根目录 → 叶节点顺序）
    discovered: list[tuple[KaosPath, str]] = []  # (路径, 内容)
    for d in dirs:
        # .novel/AGENTS.md 独立检查（可与根级文件共存）
        novel_path = d / ".novel" / "AGENTS.md"
        # AGENTS.md 和 agents.md 互斥（大写优先）
        root_candidates = [d / "AGENTS.md", d / "agents.md"]

        candidates: list[KaosPath] = []
        if await novel_path.is_file():
            candidates.append(novel_path)
        for rc in root_candidates:
            if await rc.is_file():
                candidates.append(rc)
                break

        for path in candidates:
            content = (await path.read_text()).strip()
            if content:
                discovered.append((path, content))
                logger.info("已加载 agents.md: {path}", path=path)

    if not discovered:
        logger.info(
            "从 {root} 到 {cwd} 未找到 AGENTS.md",
            root=project_root,
            cwd=work_dir,
        )
        return None

    # 第二阶段：按叶节点优先分配预算，确保更深的文件不会被截断
    # 注解开销（<!-- From: ... -->\n 和 \n\n 分隔符）计入预算，
    # 确保最终输出不超过限制。
    remaining = _AGENTS_MD_MAX_BYTES
    budgeted: list[tuple[KaosPath, str]] = [None] * len(discovered)  # type: ignore[list-item]
    for i in reversed(range(len(discovered))):
        path, content = discovered[i]
        annotation = f"<!-- From: {path} -->\n"
        # 为注解和各部分之间的 \n\n 分隔符预留空间
        separator_cost = len(b"\n\n") if i < len(discovered) - 1 else 0
        overhead = len(annotation.encode()) + separator_cost
        remaining -= overhead
        if remaining <= 0:
            budgeted[i] = (path, "")
            remaining = 0
            continue
        encoded = content.encode()
        if len(encoded) > remaining:
            content = encoded[:remaining].decode(errors="ignore").strip()
            logger.warning("AGENTS.md 因大小限制被截断: {path}", path=path)
        remaining -= len(content.encode())
        budgeted[i] = (path, content)

    # 第三阶段：按根目录 → 叶节点顺序组装，跳过因截断而清空的条目
    parts: list[str] = []
    for path, content in budgeted:
        if content:
            parts.append(f"<!-- From: {path} -->\n{content}")

    return "\n\n".join(parts) if parts else None


@dataclass(slots=True, kw_only=True)
class Runtime:
    """智能体运行时环境。

    包含智能体运行所需的所有组件和配置。

    Attributes:
        config: 配置对象。
        llm: LLM 实例，可被动态更改。
        session: 会话对象。
        builtin_args: 内置系统提示参数。
        denwa_renji: 电话应答机实例。
        approval: 审批管理器。
        labor_market: 劳务市场（子智能体调度）。
        environment: 环境信息。
        notifications: 通知管理器。
        background_tasks: 后台任务管理器。
        skills: 技能名称到技能对象的映射。
        additional_dirs: 额外目录列表。
        skills_dirs: 技能目录列表。
        subagent_store: 子智能体存储实例。
        approval_runtime: 审批运行时实例。
        root_wire_hub: 根 Wire 中心实例。
        subagent_id: 子智能体 ID。
        subagent_type: 子智能体类型。
        role: 角色，可选值为 "root" 或 "subagent"。
        hook_engine: 钩子引擎实例，由 NovelCLI 在 soul 创建后设置。
    """

    config: Config
    llm: LLM | None  # 不冻结 Runtime 数据类，因为 LLM 可以被更改
    session: Session
    builtin_args: BuiltinSystemPromptArgs
    denwa_renji: DenwaRenji
    approval: Approval
    labor_market: LaborMarket
    environment: Environment
    notifications: NotificationManager
    background_tasks: BackgroundTaskManager
    skills: dict[str, Skill]
    additional_dirs: list[KaosPath]
    skills_dirs: list[KaosPath]
    subagent_store: SubagentStore | None = None
    approval_runtime: ApprovalRuntime | None = None
    root_wire_hub: RootWireHub | None = None
    subagent_id: str | None = None
    subagent_type: str | None = None
    role: Literal["root", "subagent"] = "root"
    hook_engine: Any = None
    """HookEngine 实例，由 NovelCLI 在 soul 创建后设置。"""

    def __post_init__(self) -> None:
        """初始化后处理，设置默认值和绑定关系。"""
        if self.subagent_store is None:
            self.subagent_store = SubagentStore(self.session)
        if self.root_wire_hub is None:
            self.root_wire_hub = RootWireHub()
        if self.approval_runtime is None:
            self.approval_runtime = ApprovalRuntime()
        self.approval_runtime.bind_root_wire_hub(self.root_wire_hub)
        self.approval.set_runtime(self.approval_runtime)
        self.background_tasks.bind_runtime(self)

    @staticmethod
    async def create(
        config: Config,
        llm: LLM | None,
        session: Session,
        yolo: bool,
        skills_dirs: list[KaosPath] | None = None,
    ) -> Runtime:
        """创建并初始化运行时实例。

        Args:
            config: 配置对象。
            llm: LLM 实例。
            session: 会话对象。
            yolo: 是否启用 yolo 模式（自动审批）。
            skills_dirs: 自定义技能目录列表。

        Returns:
            初始化完成的运行时实例。
        """
        ls_output, agents_md, environment = await asyncio.gather(
            list_directory(session.work_dir),
            load_agents_md(session.work_dir),
            Environment.detect(),
        )

        # 发现并格式化技能
        skills_roots = await resolve_skills_roots(
            session.work_dir,
            skills_dirs=skills_dirs,
            merge_brands=config.merge_all_available_skills,
        )
        # 规范化路径，使符号链接的技能目录匹配解析后的路径
        skills_roots_canonical = [r.canonical() for r in skills_roots]
        skills = await discover_skills_from_roots(skills_roots)
        skills_by_name = index_skills(skills)
        logger.info("已发现 {count} 个技能", count=len(skills))
        skills_formatted = "\n".join(
            (
                f"- {skill.name}\n"
                f"  - Path: {skill.skill_md_file}\n"
                f"  - Description: {skill.description}"
            )
            for skill in skills
        )

        # 从会话状态恢复额外目录，清理无效条目
        additional_dirs: list[KaosPath] = []
        pruned = False
        valid_dir_strs: list[str] = []
        for dir_str in session.state.additional_dirs:
            d = KaosPath(dir_str).canonical()
            if await d.is_dir():
                additional_dirs.append(d)
                valid_dir_strs.append(dir_str)
            else:
                logger.warning(
                    "额外目录已不存在，从状态中移除: {dir}",
                    dir=dir_str,
                )
                pruned = True
        if pruned:
            session.state.additional_dirs = valid_dir_strs
            session.save_state()

        # 格式化额外目录信息用于系统提示
        additional_dirs_info = ""
        if additional_dirs:
            parts: list[str] = []
            for d in additional_dirs:
                try:
                    dir_ls = await list_directory(d)
                except OSError:
                    logger.warning(
                        "无法列出额外目录，跳过列表: {dir}", dir=d
                    )
                    dir_ls = "[目录不可读]"
                parts.append(f"### `{d}`\n\n```\n{dir_ls}\n```")
            additional_dirs_info = "\n\n".join(parts)

        # 合并 CLI 标志与会话持久化状态
        effective_yolo = yolo or session.state.approval.yolo
        saved_actions = set(session.state.approval.auto_approve_actions)

        def _on_approval_change() -> None:
            """审批状态变更回调函数。"""
            session.state.approval.yolo = approval_state.yolo
            session.state.approval.auto_approve_actions = set(approval_state.auto_approve_actions)
            session.save_state()

        approval_state = ApprovalState(
            yolo=effective_yolo,
            auto_approve_actions=saved_actions,
            on_change=_on_approval_change,
        )
        notifications = NotificationManager(
            session.context_file.parent / "notifications",
            config.notifications,
        )

        return Runtime(
            config=config,
            llm=llm,
            session=session,
            builtin_args=BuiltinSystemPromptArgs(
                NOVEL_NOW=datetime.now().astimezone().isoformat(),
                NOVEL_WORK_DIR=session.work_dir,
                NOVEL_WORK_DIR_LS=ls_output,
                NOVEL_AGENTS_MD=agents_md or "",
                NOVEL_SKILLS=skills_formatted or "No skills found.",
                NOVEL_ADDITIONAL_DIRS_INFO=additional_dirs_info,
                NOVEL_OS=environment.os_kind,
                NOVEL_SHELL=f"{environment.shell_name} (`{environment.shell_path}`)",
            ),
            denwa_renji=DenwaRenji(),
            approval=Approval(state=approval_state),
            labor_market=LaborMarket(),
            environment=environment,
            notifications=notifications,
            background_tasks=BackgroundTaskManager(
                session,
                config.background,
                notifications=notifications,
            ),
            skills=skills_by_name,
            additional_dirs=additional_dirs,
            # 仅暴露工作空间外的技能根目录供 Glob 访问；
            # 项目级根目录已在 work_dir 内。
            skills_dirs=[
                r for r in skills_roots_canonical if not is_within_directory(r, session.work_dir)
            ],
            subagent_store=SubagentStore(session),
            approval_runtime=ApprovalRuntime(),
            root_wire_hub=RootWireHub(),
            role="root",
        )

    def copy_for_subagent(
        self,
        *,
        agent_id: str,
        subagent_type: str,
        llm_override: LLM | None = None,
    ) -> Runtime:
        """为子智能体克隆运行时实例。

        Args:
            agent_id: 子智能体 ID。
            subagent_type: 子智能体类型。
            llm_override: 可选的 LLM 覆盖实例。

        Returns:
            克隆的运行时实例，配置为子智能体角色。
        """
        return Runtime(
            config=self.config,
            llm=llm_override if llm_override is not None else self.llm,
            session=self.session,
            builtin_args=self.builtin_args,
            denwa_renji=DenwaRenji(),  # 子智能体必须有自己的 DenwaRenji
            approval=self.approval.share(),
            labor_market=self.labor_market,
            environment=self.environment,
            notifications=self.notifications,
            background_tasks=self.background_tasks.copy_for_role("subagent"),
            skills=self.skills,
            # 共享同一列表引用，使 /add-dir 变更传播到所有智能体
            additional_dirs=self.additional_dirs,
            skills_dirs=self.skills_dirs,
            subagent_store=self.subagent_store,
            approval_runtime=self.approval_runtime,
            root_wire_hub=self.root_wire_hub,
            subagent_id=agent_id,
            subagent_type=subagent_type,
            role="subagent",
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class Agent:
    """已加载的智能体实例。

    Attributes:
        name: 智能体名称。
        system_prompt: 系统提示内容。
        toolset: 工具集实例。
        runtime: 运行时实例，每个智能体有自己的运行时，应从主智能体派生。
    """

    name: str
    system_prompt: str
    toolset: NovelToolset
    runtime: Runtime
    """每个智能体有自己的运行时，应从主智能体派生。"""


async def load_agent(
    agent_file: Path,
    runtime: Runtime,
    *,
    mcp_configs: list[MCPConfig] | list[dict[str, Any]],
    start_mcp_loading: bool = True,
) -> Agent:
    """从配置文件加载智能体。

    Args:
        agent_file: 智能体配置文件路径。
        runtime: 运行时实例。
        mcp_configs: MCP 配置列表。
        start_mcp_loading: 是否立即启动 MCP 加载，默认为 True。

    Returns:
        加载完成的智能体实例。

    Raises:
        FileNotFoundError: 智能体文件未找到。
        AgentSpecError: 智能体配置无效。
        SystemPromptTemplateError: 系统提示模板无效。
        InvalidToolError: 工具无法加载。
        MCPConfigError: MCP 配置无效。
        MCPRuntimeError: MCP 服务器无法连接。
    """
    logger.info("正在加载智能体: {agent_file}", agent_file=agent_file)
    agent_spec = load_agent_spec(agent_file)

    system_prompt = _load_system_prompt(
        agent_spec.system_prompt_path,
        agent_spec.system_prompt_args,
        runtime.builtin_args,
    )

    # 在加载工具前注册内置子智能体类型，因为某些工具在初始化时
    # 会从劳务市场渲染描述。
    for subagent_name, subagent_spec in agent_spec.subagents.items():
        logger.debug(
            "正在注册内置子智能体类型: {subagent_name}", subagent_name=subagent_name
        )
        builtin_spec = load_agent_spec(subagent_spec.path)
        tool_policy = (
            ToolPolicy(mode="allowlist", tools=tuple(builtin_spec.allowed_tools))
            if builtin_spec.allowed_tools is not None
            else ToolPolicy(mode="inherit")
        )
        runtime.labor_market.add_builtin_type(
            AgentTypeDefinition(
                name=subagent_name,
                description=subagent_spec.description,
                agent_file=subagent_spec.path,
                when_to_use=builtin_spec.when_to_use,
                default_model=builtin_spec.model,
                tool_policy=tool_policy,
            )
        )

    toolset = NovelToolset(
        get_current_book=lambda: runtime.session.state.current_book,
        tool_validators=agent_spec.tool_validators,
        loop_threshold=agent_spec.loop_detection.threshold if agent_spec.loop_detection else 5,
    )
    tool_deps = {
        NovelToolset: toolset,
        Runtime: runtime,
        # TODO: 移除以下所有依赖，改用 Runtime
        Config: runtime.config,
        BuiltinSystemPromptArgs: runtime.builtin_args,
        Session: runtime.session,
        DenwaRenji: runtime.denwa_renji,
        Approval: runtime.approval,
        LaborMarket: runtime.labor_market,
        Environment: runtime.environment,
    }
    tools = agent_spec.allowed_tools if agent_spec.allowed_tools is not None else agent_spec.tools
    if agent_spec.exclude_tools:
        logger.debug("排除工具: {tools}", tools=agent_spec.exclude_tools)
        tools = [tool for tool in tools if tool not in agent_spec.exclude_tools]

    toolset.load_tools(tools, tool_deps)

    # 加载插件工具
    from novel_cli.plugin.manager import get_plugins_dir
    from novel_cli.plugin.tool import load_plugin_tools

    plugin_tools = load_plugin_tools(get_plugins_dir(), runtime.config, approval=runtime.approval)
    for plugin_tool in plugin_tools:
        if toolset.find(plugin_tool.name) is not None:
            logger.warning(
                "插件工具 '{name}' 与现有工具冲突，跳过",
                name=plugin_tool.name,
            )
            continue
        toolset.add(plugin_tool)

    if mcp_configs:
        validated_mcp_configs: list[MCPConfig] = []
        if mcp_configs:
            from fastmcp.mcp_config import MCPConfig

            for mcp_config in mcp_configs:
                try:
                    validated_mcp_configs.append(
                        mcp_config
                        if isinstance(mcp_config, MCPConfig)
                        else MCPConfig.model_validate(mcp_config)
                    )
                except pydantic.ValidationError as e:
                    raise MCPConfigError(f"无效 MCP 配置: {e}") from e
        if start_mcp_loading:
            await toolset.load_mcp_tools(validated_mcp_configs, runtime, in_background=True)
        else:
            toolset.defer_mcp_tool_loading(validated_mcp_configs, runtime)

    return Agent(
        name=agent_spec.name,
        system_prompt=system_prompt,
        toolset=toolset,
        runtime=runtime,
    )


def _load_system_prompt(
    path: Path, args: dict[str, str], builtin_args: BuiltinSystemPromptArgs
) -> str:
    """加载并渲染系统提示模板。

    Args:
        path: 系统提示模板文件路径。
        args: 用户自定义参数字典。
        builtin_args: 内置系统提示参数。

    Returns:
        渲染后的系统提示内容。

    Raises:
        SystemPromptTemplateError: 模板缺少参数或模板无效。
    """
    logger.info("正在加载系统提示: {path}", path=path)
    system_prompt = path.read_text(encoding="utf-8").strip()
    logger.debug(
        "使用内置参数和配置参数替换系统提示: {builtin_args}, {spec_args}",
        builtin_args=builtin_args,
        spec_args=args,
    )
    env = JinjaEnvironment(
        loader=FileSystemLoader(path.parent),
        keep_trailing_newline=True,
        lstrip_blocks=True,
        trim_blocks=True,
        variable_start_string="${",
        variable_end_string="}",
        undefined=StrictUndefined,
    )
    try:
        template = env.from_string(system_prompt)
        return template.render(asdict(builtin_args), **args)
    except UndefinedError as exc:
        raise SystemPromptTemplateError(f"系统提示缺少参数 {path}: {exc}") from exc
    except TemplateError as exc:
        raise SystemPromptTemplateError(f"系统提示模板无效 {path}: {exc}") from exc
