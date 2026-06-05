"""Test configuration and fixtures."""

from __future__ import annotations

import os
import platform
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

import pytest
from kaos import get_current_kaos, reset_current_kaos, set_current_kaos
from kaos.local import LocalKaos
from kaos.path import KaosPath
from kosong.chat_provider.mock import MockChatProvider
from pydantic import SecretStr

from novel_cli.background import BackgroundTaskManager
from novel_cli.config import Config, MoonshotSearchConfig, NovelDBConfig, get_default_config
from novel_cli.llm import ALL_MODEL_CAPABILITIES, LLM
from novel_cli.metadata import WorkDirMeta
from novel_cli.notifications import NotificationManager
from novel_cli.session import Session
from novel_cli.session_state import SessionState
from novel_cli.soul.agent import BuiltinSystemPromptArgs, LaborMarket, Runtime
from novel_cli.soul.approval import Approval
from novel_cli.soul.denwarenji import DenwaRenji
from novel_cli.soul.toolset import NovelToolset
from novel_cli.subagents import AgentTypeDefinition, ToolPolicy
from novel_cli.tools.agent import Agent as AgentTool
from novel_cli.tools.background import (
    TaskList,
    TaskOutput,
    TaskStop,
)
from novel_cli.tools.dmail import SendDMail
from novel_cli.tools.file.glob import Glob
from novel_cli.tools.file.grep_local import Grep
from novel_cli.tools.file.read import ReadFile
from novel_cli.tools.file.read_media import ReadMediaFile
from novel_cli.tools.file.replace import StrReplaceFile
from novel_cli.tools.file.write import WriteFile
from novel_cli.tools.shell import Shell
from novel_cli.tools.think import Think
from novel_cli.tools.todo import SetTodoList
from novel_cli.tools.web.fetch import FetchURL
from novel_cli.tools.web.search import SearchWeb
from novel_cli.utils.environment import Environment
from novel_cli.wire.file import WireFile
from novel_cli.store import NovelStore
from novel_cli.store.models import (
    CorpusResult,
    CorpusSentence,
    Entity,
    GraphRelation,
    GraphResult,
    RelType,
)
from novel_cli.tools.novel import SearchEntity, SearchEntityParams, SearchGraph, SearchGraphParams, SearchCorpus, SearchCorpusParams
from unittest.mock import AsyncMock, MagicMock


# === Data model fixtures (shared across all test modules) ===


@pytest.fixture
def sample_entities() -> list[Entity]:
    """Construct test entity list."""
    return [
        Entity(
            id="凡人修仙传_人物_韩立_0",
            name="韩立",
            type="人物",
            book="凡人修仙传",
            description="韩立是凡人修仙传的主角，性格谨慎，擅长隐匿",
            score=0.85,
            match_type="vector",
        ),
        Entity(
            id="凡人修仙传_法宝_掌天瓶_0",
            name="掌天瓶",
            type="法宝",
            book="凡人修仙传",
            description="掌天瓶是韩立的核心法宝，可催熟灵药",
            score=0.72,
            match_type="vector",
        ),
    ]


@pytest.fixture
def sample_rel_types() -> list[RelType]:
    """Construct test relationship types."""
    return [
        RelType(type="POSSESS", description="拥有关系", category="拥有"),
        RelType(type="USE", description="使用关系", category="使用"),
        RelType(type="MENTOR", description="师徒关系", category="师徒"),
    ]


@pytest.fixture
def sample_relations() -> list[GraphRelation]:
    """Construct test relationship data."""
    return [
        GraphRelation(
            relationship_type="POSSESS",
            other_node_id="凡人修仙传_法宝_掌天瓶_0",
            description="韩立拥有掌天瓶，这是他的核心法宝",
        ),
        GraphRelation(
            relationship_type="USE",
            other_node_id="凡人修仙传_功法_青元剑诀_0",
            description="韩立修炼青元剑诀作为本命功法",
        ),
    ]


@pytest.fixture
def sample_sentences() -> list[CorpusSentence]:
    """Construct test sentence list."""
    return [
        CorpusSentence(sentence_index=10, text="韩立从储物袋中取出一件物品"),
        CorpusSentence(sentence_index=11, text="掌天瓶在月光下散发出淡淡的光芒"),
        CorpusSentence(sentence_index=12, text="他小心翼翼地将灵力注入其中"),
    ]


@pytest.fixture
def sample_corpus_result() -> CorpusResult:
    """Construct test corpus result."""
    return CorpusResult(
        sentences=[],
        text="第173章内容：韩立从储物袋中取出掌天瓶...",
        title="第173章",
        hint="已返回完整章节内容",
        brief="第173章",
    )


@pytest.fixture
def config() -> Config:
    """Create a Config instance."""
    conf = get_default_config()
    conf.services.moonshot_search = MoonshotSearchConfig(
        base_url="https://api.kimi.com/coding/v1/search",
        api_key=SecretStr("test-api-key"),
    )
    return conf


@pytest.fixture
def llm() -> LLM:
    """Create a LLM instance."""
    return LLM(
        chat_provider=MockChatProvider([]),
        max_context_size=100_000,
        capabilities=ALL_MODEL_CAPABILITIES,
    )


@pytest.fixture
def temp_work_dir() -> Generator[KaosPath]:
    """Create a temporary working directory for tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        original_cwd = Path.cwd()
        p = Path(tmpdir).resolve()
        os.chdir(p)
        token = set_current_kaos(LocalKaos())
        try:
            yield KaosPath.unsafe_from_local_path(p)
        finally:
            reset_current_kaos(token)
            os.chdir(original_cwd)


@pytest.fixture
def temp_share_dir() -> Generator[Path]:
    """Create a temporary shared directory for tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def builtin_args(temp_work_dir: KaosPath) -> BuiltinSystemPromptArgs:
    """Create builtin arguments with temporary work directory."""
    return BuiltinSystemPromptArgs(
        NOVEL_NOW="1970-01-01T00:00:00+00:00",
        NOVEL_WORK_DIR=temp_work_dir,
        NOVEL_WORK_DIR_LS="Test ls content",
        NOVEL_AGENTS_MD="Test agents content",
        NOVEL_SKILLS="No skills found.",
        NOVEL_ADDITIONAL_DIRS_INFO="",
        NOVEL_OS="macOS",
        NOVEL_SHELL="bash (`/bin/bash`)",
    )


@pytest.fixture
def denwa_renji() -> DenwaRenji:
    """Create a DenwaRenji instance."""
    return DenwaRenji()


@pytest.fixture
def session(temp_work_dir: KaosPath, temp_share_dir: Path) -> Session:
    """Create a Session instance."""
    return Session(
        id="test",
        work_dir=temp_work_dir,
        work_dir_meta=WorkDirMeta(path=str(temp_work_dir), kaos=get_current_kaos().name),
        context_file=temp_share_dir / "context.jsonl",
        wire_file=WireFile(path=temp_share_dir / "wire.jsonl"),
        state=SessionState(),
        title="Test Session",
        updated_at=0.0,
    )


@pytest.fixture
def approval() -> Approval:
    """Create a Approval instance."""
    return Approval(yolo=True)


@pytest.fixture
def labor_market() -> LaborMarket:
    """Create a LaborMarket instance."""
    return LaborMarket()


@pytest.fixture
def environment() -> Environment:
    """Create an Environment instance."""
    if platform.system() == "Windows":
        return Environment(
            os_kind="Windows",
            os_arch="x86_64",
            os_version="1.0",
            shell_name="Windows PowerShell",
            shell_path=KaosPath("powershell.exe"),
        )
    else:
        return Environment(
            os_kind="Unix",
            os_arch="aarch64",
            os_version="1.0",
            shell_name="bash",
            shell_path=KaosPath("/bin/bash"),
        )


@pytest.fixture
def runtime(
    config: Config,
    llm: LLM,
    builtin_args: BuiltinSystemPromptArgs,
    denwa_renji: DenwaRenji,
    session: Session,
    approval: Approval,
    labor_market: LaborMarket,
    environment: Environment,
) -> Runtime:
    """Create a Runtime instance."""
    notifications = NotificationManager(
        session.context_file.parent / "notifications", config.notifications
    )
    rt = Runtime(
        config=config,
        llm=llm,
        builtin_args=builtin_args,
        denwa_renji=denwa_renji,
        session=session,
        approval=approval,
        labor_market=labor_market,
        environment=environment,
        notifications=notifications,
        background_tasks=BackgroundTaskManager(
            session,
            config.background,
            notifications=notifications,
        ),
        skills={},
        additional_dirs=[],
        skills_dirs=[],
        role="root",
    )
    rt.labor_market.add_builtin_type(
        AgentTypeDefinition(
            name="mocker",
            description="The mock agent for testing purposes.",
            agent_file=Path("/tmp/mocker-agent.yaml"),
            tool_policy=ToolPolicy(mode="inherit"),
        )
    )
    return rt


@pytest.fixture
def toolset() -> NovelToolset:
    return NovelToolset()


@contextmanager
def tool_call_context(tool_name: str) -> Generator[None]:
    """Create a tool call context."""
    from novel_cli.soul.toolset import current_tool_call
    from novel_cli.wire.types import ToolCall

    token = current_tool_call.set(
        ToolCall(id="test", function=ToolCall.FunctionBody(name=tool_name, arguments=None))
    )
    try:
        yield
    finally:
        current_tool_call.reset(token)


@pytest.fixture
def agent_tool(runtime: Runtime) -> AgentTool:
    """Create an Agent tool instance."""
    return AgentTool(runtime)


@pytest.fixture
def send_dmail_tool(denwa_renji: DenwaRenji) -> SendDMail:
    """Create a SendDMail tool instance."""
    return SendDMail(denwa_renji)


@pytest.fixture
def think_tool() -> Think:
    """Create a Think tool instance."""
    return Think()


@pytest.fixture
def set_todo_list_tool(runtime: Runtime) -> SetTodoList:
    """Create a SetTodoList tool instance."""
    return SetTodoList(runtime)


@pytest.fixture
def shell_tool(approval: Approval, environment: Environment, runtime: Runtime) -> Generator[Shell]:
    """Create a Shell tool instance."""
    with tool_call_context("Shell"):
        yield Shell(approval, environment, runtime)


@pytest.fixture
def task_list_tool(runtime: Runtime) -> Generator[TaskList]:
    with tool_call_context("TaskList"):
        yield TaskList(runtime)


@pytest.fixture
def task_output_tool(runtime: Runtime) -> TaskOutput:
    with tool_call_context("TaskOutput"):
        return TaskOutput(runtime)


@pytest.fixture
def task_stop_tool(runtime: Runtime, approval: Approval) -> Generator[TaskStop]:
    with tool_call_context("TaskStop"):
        yield TaskStop(runtime, approval)


@pytest.fixture
def read_file_tool(runtime: Runtime) -> ReadFile:
    """Create a ReadFile tool instance."""
    return ReadFile(runtime)


@pytest.fixture
def read_media_file_tool(runtime: Runtime) -> ReadMediaFile:
    """Create a ReadMediaFile tool instance."""
    return ReadMediaFile(runtime)


@pytest.fixture
def glob_tool(runtime: Runtime) -> Glob:
    """Create a Glob tool instance."""
    return Glob(runtime)


@pytest.fixture
def grep_tool() -> Grep:
    """Create a Grep tool instance."""
    return Grep()


@pytest.fixture
def write_file_tool(runtime: Runtime, approval: Approval) -> Generator[WriteFile]:
    """Create a WriteFile tool instance."""
    with tool_call_context("WriteFile"):
        yield WriteFile(runtime, approval)


@pytest.fixture
def str_replace_file_tool(runtime: Runtime, approval: Approval) -> Generator[StrReplaceFile]:
    """Create a StrReplaceFile tool instance."""
    with tool_call_context("StrReplaceFile"):
        yield StrReplaceFile(runtime, approval)


@pytest.fixture
def search_web_tool(config: Config, runtime: Runtime) -> SearchWeb:
    """Create a SearchWeb tool instance."""
    return SearchWeb(config, runtime)


@pytest.fixture
def fetch_url_tool(config: Config, runtime: Runtime) -> FetchURL:
    """Create a FetchURL tool instance."""
    return FetchURL(config, runtime)


# misc fixtures


@pytest.fixture
def outside_file() -> Generator[Path]:
    """Return a path to a file outside the working directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir) / "outside_file.txt"


# === Novel store fixtures ===


@pytest.fixture
def mock_store(
    sample_entities: list,
    sample_rel_types: list,
    sample_relations: list,
    sample_corpus_result,
) -> AsyncMock:
    """Create Mock NovelStore with preset return values."""
    store = AsyncMock(spec=NovelStore)

    store.search_entities.return_value = sample_entities
    store.search_entities_by_name.return_value = sample_entities
    store.query_graph.return_value = MagicMock(
        types=sample_rel_types,
        description="韩立是凡人修仙传的主角",
        related=sample_relations,
        rels=sample_relations,
    )
    store.query_corpus.return_value = sample_corpus_result

    return store


@pytest.fixture
def search_entity_tool(config: Config, mock_store: AsyncMock) -> SearchEntity:
    """创建使用 mock store 的 SearchEntity 工具实例。

    构造一个带有有效 pg_password 的 Config 对象，
    然后将内部 _store 替换为 mock_store 以避免真实数据库连接。
    """
    # 设置 novel_db 配置，确保 pg_password 非空以跳过 SkipThisTool 检查
    config.services.novel_db = NovelDBConfig(pg_password=SecretStr("test-password"))
    tool = SearchEntity(config)
    # 替换内部 store 为 mock，避免真实数据库连接
    tool._store = mock_store
    return tool


@pytest.fixture
def search_graph_tool(config: Config, mock_store: AsyncMock) -> SearchGraph:
    """创建使用 mock store 的 SearchGraph 工具实例。

    构造一个带有有效 pg_password 的 Config 对象，
    然后将内部 _store 替换为 mock_store 以避免真实数据库连接。
    """
    # 设置 novel_db 配置，确保 pg_password 非空以跳过 SkipThisTool 检查
    config.services.novel_db = NovelDBConfig(pg_password=SecretStr("test-password"))
    tool = SearchGraph(config)
    # 替换内部 store 为 mock，避免真实数据库连接
    tool._store = mock_store
    return tool


@pytest.fixture
def search_corpus_tool(config: Config, mock_store: AsyncMock) -> SearchCorpus:
    """创建使用 mock store 的 SearchCorpus 工具实例。

    构造一个带有有效 pg_password 的 Config 对象，
    然后将内部 _store 替换为 mock_store 以避免真实数据库连接。
    """
    # 设置 novel_db 配置，确保 pg_password 非空以跳过 SkipThisTool 检查
    config.services.novel_db = NovelDBConfig(pg_password=SecretStr("test-password"))
    tool = SearchCorpus(config)
    # 替换内部 store 为 mock，避免真实数据库连接
    tool._store = mock_store
    return tool
