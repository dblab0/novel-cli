"""配置 API 路由。

提供全局配置的读取、更新以及 config.toml 文件的访问功能。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from novel_cli import logger
from novel_cli.config import LLMModel, get_config_file, load_config, save_config
from novel_cli.llm import ProviderType, derive_model_capabilities
from novel_cli.web.runner.process import NovelCLIRunner

router = APIRouter(prefix="/api/config", tags=["config"])


class ConfigModel(LLMModel):
    """前端使用的模型配置。

    继承自 LLMModel，额外提供名称和提供商类型字段。

    Attributes:
        name: 模型在配置中的键名。
        provider_type: 提供商类型。
    """

    name: str = Field(description="Model key in novel-cli config (Config.models)")
    provider_type: ProviderType = Field(description="Provider type (LLMProvider.type)")


class GlobalConfig(BaseModel):
    """前端使用的全局配置快照。

    Attributes:
        default_model: 当前默认模型键名。
        default_thinking: 当前默认思考模式。
        models: 所有已配置的模型列表。
    """

    default_model: str = Field(description="Current default model key")
    default_thinking: bool = Field(description="Current default thinking mode")
    models: list[ConfigModel] = Field(description="All configured models")


class UpdateGlobalConfigRequest(BaseModel):
    """更新全局配置的请求模型。

    Attributes:
        default_model: 新的默认模型键名。
        default_thinking: 新的默认思考模式。
        restart_running_sessions: 是否重启正在运行的会话。
        force_restart_busy_sessions: 是否强制重启繁忙的会话。
    """

    default_model: str | None = Field(default=None, description="New default model key")
    default_thinking: bool | None = Field(default=None, description="New default thinking mode")
    restart_running_sessions: bool | None = Field(
        default=None, description="Whether to restart running sessions"
    )
    force_restart_busy_sessions: bool | None = Field(
        default=None, description="Whether to force restart busy sessions"
    )


class UpdateGlobalConfigResponse(BaseModel):
    """更新全局配置后的响应模型。

    Attributes:
        config: 更新后的配置快照。
        restarted_session_ids: 已重启的会话 ID 列表。
        skipped_busy_session_ids: 跳过的繁忙会话 ID 列表。
    """

    config: GlobalConfig = Field(description="Updated config snapshot")
    restarted_session_ids: list[str] | None = Field(
        default=None, description="IDs of restarted sessions"
    )
    skipped_busy_session_ids: list[str] | None = Field(
        default=None, description="IDs of busy sessions that were skipped"
    )


class ConfigToml(BaseModel):
    """原始 config.toml 内容模型。

    Attributes:
        content: 原始 TOML 内容。
        path: 配置文件路径。
    """

    content: str = Field(description="Raw TOML content")
    path: str = Field(description="Path to config file")


class UpdateConfigTomlRequest(BaseModel):
    """更新 config.toml 的请求模型。

    Attributes:
        content: 新的 TOML 内容。
    """

    content: str = Field(description="New TOML content")


class UpdateConfigTomlResponse(BaseModel):
    """更新 config.toml 后的响应模型。

    Attributes:
        success: 更新是否成功。
        error: 失败时的错误信息。
    """

    success: bool = Field(description="Whether the update was successful")
    error: str | None = Field(default=None, description="Error message if failed")


def _build_global_config() -> GlobalConfig:
    """从 novel-cli 配置构建全局配置对象。

    Returns:
        构建好的 GlobalConfig 实例。
    """
    config = load_config()

    models: list[ConfigModel] = []
    for model_name, model in config.models.items():
        provider = config.providers.get(model.provider)
        if provider is None:
            continue

        # 推导能力
        derived_caps = derive_model_capabilities(model)
        capabilities = derived_caps or None

        models.append(
            ConfigModel(
                name=model_name,
                model=model.model,
                provider=model.provider,
                provider_type=provider.type,
                max_context_size=model.max_context_size,
                capabilities=capabilities,
            )
        )

    return GlobalConfig(
        default_model=config.default_model,
        default_thinking=config.default_thinking,
        models=models,
    )


def _get_runner(req: Request) -> NovelCLIRunner:
    """从 FastAPI 应用状态获取运行器实例。

    Args:
        req: FastAPI 请求对象。

    Returns:
        NovelCLIRunner 实例。
    """
    return req.app.state.runner


def _ensure_sensitive_apis_allowed(request: Request) -> None:
    """确保敏感配置 API 未被禁用。

    Args:
        request: FastAPI 请求对象。

    Raises:
        HTTPException: 敏感 API 被禁用时抛出 403 错误。
    """
    if getattr(request.app.state, "restrict_sensitive_apis", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Sensitive config APIs are disabled in this mode.",
        )


@router.get("/", summary="获取全局配置快照")
async def get_global_config() -> GlobalConfig:
    """获取全局配置快照。

    Returns:
        当前的全局配置对象。
    """
    return _build_global_config()


@router.patch("/", summary="更新全局默认模型/思考模式")
async def update_global_config(
    request: UpdateGlobalConfigRequest,
    http_request: Request,
    runner: NovelCLIRunner = Depends(_get_runner),
) -> UpdateGlobalConfigResponse:
    """更新全局默认模型和思考模式。

    Args:
        request: 更新请求对象。
        http_request: HTTP 请求对象。
        runner: NovelCLI 运行器实例。

    Returns:
        更新结果，包含新的配置快照和重启的会话信息。

    Raises:
        HTTPException: 模型不存在或敏感 API 被禁用时抛出相应错误。
    """
    _ensure_sensitive_apis_allowed(http_request)
    config = load_config()

    # 验证并更新 default_model
    if request.default_model is not None:
        if request.default_model not in config.models:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Model '{request.default_model}' not found in config",
            )
        config.default_model = request.default_model

    # 更新 default_thinking
    if request.default_thinking is not None:
        config.default_thinking = request.default_thinking

    # 保存配置
    save_config(config)

    # 重启运行中的工作进程以应用配置变更
    restarted: list[str] = []
    skipped_busy: list[str] = []

    restart_running = request.restart_running_sessions
    if restart_running is None:
        restart_running = True  # 默认重启会话

    if restart_running:
        summary = await runner.restart_running_workers(
            reason="config_update",
            force=request.force_restart_busy_sessions or False,
        )
        restarted = [str(sid) for sid in summary.restarted_session_ids]
        skipped_busy = [str(sid) for sid in summary.skipped_busy_session_ids]

    return UpdateGlobalConfigResponse(
        config=_build_global_config(),
        restarted_session_ids=restarted if restarted else None,
        skipped_busy_session_ids=skipped_busy if skipped_busy else None,
    )


@router.get("/toml", summary="获取 config.toml 内容")
async def get_config_toml(http_request: Request) -> ConfigToml:
    """获取 config.toml 文件内容。

    Args:
        http_request: HTTP 请求对象。

    Returns:
        包含 TOML 内容和文件路径的响应对象。
    """
    _ensure_sensitive_apis_allowed(http_request)
    config_file = get_config_file()
    if not config_file.exists():
        return ConfigToml(content="", path=str(config_file))
    return ConfigToml(content=config_file.read_text(encoding="utf-8"), path=str(config_file))


@router.put("/toml", summary="更新 config.toml 内容")
async def update_config_toml(
    request: UpdateConfigTomlRequest,
    http_request: Request,
) -> UpdateConfigTomlResponse:
    """更新 config.toml 文件内容。

    Args:
        request: 包含新 TOML 内容的请求对象。
        http_request: HTTP 请求对象。

    Returns:
        更新结果，包含成功标志和可能的错误信息。
    """
    from novel_cli.config import load_config_from_string

    _ensure_sensitive_apis_allowed(http_request)
    try:
        # 先验证配置格式
        load_config_from_string(request.content)

        # 写入文件
        config_file = get_config_file()
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_file.write_text(request.content, encoding="utf-8")

        return UpdateConfigTomlResponse(success=True)
    except Exception as e:
        logger.warning(f"Failed to update config.toml: {e}")
        return UpdateConfigTomlResponse(success=False, error=str(e))
