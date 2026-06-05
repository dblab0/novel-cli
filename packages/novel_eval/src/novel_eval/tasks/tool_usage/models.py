"""工具使用评估任务的数据模型。

包含配置类和任务专属的数据结构。
"""

from pydantic import BaseModel, Field


class AgentConfig(BaseModel):
    """被评估 Agent 的 LLM 配置。

    Attributes:
        model: 模型名称
        base_url: API 基础 URL（可选）
        api_key_env: 环境变量名称，用于获取 API Key
        api_key: 实际的 API Key，从环境变量加载
        max_tokens: 最大生成 token 数
        temperature: 生成温度
        reasoning_effort: 推理努力程度（如 "high"/"medium"/"low"），思考型模型专用
        extra_body: 额外请求体参数（如 {"thinking": {"type": "enabled"}}）
    """

    model: str = "deepseek-v3"
    base_url: str | None = None
    api_key_env: str = "AGENT_API_KEY"
    api_key: str = ""
    max_tokens: int = 4096
    temperature: float = 0.7
    reasoning_effort: str | None = None
    extra_body: dict | None = None


class RunnerConfig(BaseModel):
    """Runner 子进程配置。

    Attributes:
        work_dir: 工作目录（--work-dir 参数）
        agent_file: Agent 规格文件路径（--agent-file 参数）
        timeout: 子进程超时秒数
        eval_model: 评估时 novel-cli 使用的模型（运行时从配置读取，不写入 YAML 用例）
    """

    work_dir: str = "~/novel_test"
    agent_file: str = "agents/novel/agent.yaml"
    timeout: int = 60
    eval_model: str = ""  # 默认空，由用户配置


class JudgeConfig(BaseModel):
    """Judge LLM 配置。

    Attributes:
        model: Judge 模型名称
        base_url: API 基础 URL（可选）
        api_key_env: 环境变量名称，用于获取 API Key
        api_key: 实际的 API Key，从环境变量加载
        max_tokens: 最大生成 token 数
        temperature: 生成温度
        reasoning_effort: 推理努力程度（如 "high"/"medium"/"low"），思考型模型专用
        extra_body: 额外请求体参数（如 {"thinking": {"type": "enabled"}}）
    """

    model: str = "claude-sonnet-4-20250514"
    base_url: str | None = None
    api_key_env: str = "JUDGE_API_KEY"
    api_key: str = ""
    max_tokens: int = 1024
    temperature: float = 0.3
    reasoning_effort: str | None = None
    extra_body: dict | None = None


class TaskConfig(BaseModel):
    """评估任务配置。

    Attributes:
        books: 书籍列表，每本书包含 name 和 scenarios。
        data_dir: 实体数据根目录（skill_generation 使用），默认 data/input_data。
        min_descriptions: 最小描述条目数阈值（skill_generation 使用），筛选描述丰富的实体。
        min_descriptions_by_type: 按实体类型覆盖 min_descriptions，如 {"技能": 5}。
        samples_per_type: 每种类型随机抽取的实体数量（skill_generation 使用）。
        dimensions: 评估维度配置列表，每项含 key/label/description 等字段。
    """

    books: list[dict] = Field(default_factory=list)
    data_dir: str = "data/input_data"
    min_descriptions: int = 20
    min_descriptions_by_type: dict[str, int] = Field(default_factory=dict)
    samples_per_type: int = 10
    dimensions: list[dict] = Field(default_factory=list)


class EvalConfig(BaseModel):
    """完整评估配置。

    Attributes:
        agent: 被评估 Agent 的 LLM 配置
        runner: Runner 子进程配置
        judge: Judge LLM 配置
        settings_dir: 设定文件目录
        concurrency: 并发数
        tasks: 各任务的配置
    """

    agent: AgentConfig = Field(default_factory=AgentConfig)
    runner: RunnerConfig = Field(default_factory=RunnerConfig)
    judge: JudgeConfig = Field(default_factory=JudgeConfig)
    settings_dir: str = "/github/novel2settings/settings"
    concurrency: int = 2
    tasks: dict[str, TaskConfig] = Field(default_factory=dict)