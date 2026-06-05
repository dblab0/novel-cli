"""Novel CLI Web UI 应用模块。

本模块提供 Web UI 的 FastAPI 应用创建和服务器运行功能。
"""

import os
import secrets
import sys
import webbrowser
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote

import scalar_fastapi
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import MutableHeaders
from starlette.responses import HTMLResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from novel_cli import logger
from novel_cli.utils.server import (
    find_available_port,
    format_url,
    get_network_addresses,
    is_local_host,
)
from novel_cli.web.api import (
    books_router,
    config_router,
    defaults_router,
    open_in_router,
    sessions_router,
    work_dirs_router,
)
from novel_cli.web.auth import (
    DEFAULT_ALLOWED_ORIGIN_REGEX,
    AuthMiddleware,
    is_private_ip,
    normalize_allowed_origins,
)
from novel_cli.web.defaults import WebDefaults
from novel_cli.web.runner.process import NovelCLIRunner

# 根据 LOG_LEVEL 环境变量配置日志级别
_log_level = os.environ.get("LOG_LEVEL", "WARNING").upper()
logger.remove()
logger.enable("novel_cli")
logger.add(sys.stderr, level=_log_level)

# scalar-fastapi 未提供类型存根
get_scalar_api_reference = cast(  # pyright: ignore[reportUnknownMemberType]
    Callable[..., HTMLResponse],
    scalar_fastapi.get_scalar_api_reference,  # pyright: ignore[reportUnknownMemberType]
)

# 常量定义
STATIC_DIR = Path(__file__).parent / "static"
GZIP_MINIMUM_SIZE = 1024
GZIP_COMPRESSION_LEVEL = 6
DEFAULT_PORT = 5494
MAX_PORT_ATTEMPTS = 10
ENV_SESSION_TOKEN = "NOVEL_WEB_SESSION_TOKEN"
ENV_ALLOWED_ORIGINS = "NOVEL_WEB_ALLOWED_ORIGINS"
ENV_ENFORCE_ORIGIN = "NOVEL_WEB_ENFORCE_ORIGIN"
ENV_RESTRICT_SENSITIVE_APIS = "NOVEL_WEB_RESTRICT_SENSITIVE_APIS"
ENV_MAX_PUBLIC_PATH_DEPTH = "NOVEL_WEB_MAX_PUBLIC_PATH_DEPTH"

# 缓存时长配置
_IMMUTABLE_MAX_AGE = 365 * 24 * 3600  # 内容哈希资源缓存 1 年


class _StaticCacheHeadersMiddleware:
    """为 Starlette 提供的静态资源注入 Cache-Control 响应头的中间件。

    缓存策略：
    * ``index.html``（及任何非哈希 HTML 文件）→ ``no-cache``，确保浏览器始终重新验证，
      避免 CLI 升级后出现对重命名 chunk 的过期引用（参见 #1602）。
    * ``/assets/`` 下的哈希资源 → 长效 ``immutable`` 缓存，因为文件名中的内容哈希已保证唯一性。

    Attributes:
        app: ASGI 应用实例。
    """

    def __init__(self, app: ASGIApp) -> None:
        """初始化中间件。

        Args:
            app: 要包装的 ASGI 应用实例。
        """
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path: str = scope.get("path", "")

        async def _send_with_cache_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                if path.startswith("/assets/"):
                    headers["cache-control"] = f"public, max-age={_IMMUTABLE_MAX_AGE}, immutable"
                elif path == "/" or path.endswith(".html"):
                    headers["cache-control"] = "no-cache, no-store, must-revalidate"
            await send(message)

        await self.app(scope, receive, _send_with_cache_headers)


def _get_private_addresses(addresses: list[str]) -> list[str]:
    """筛选地址列表，仅保留私有 IP 地址。

    Args:
        addresses: 待筛选的地址列表。

    Returns:
        仅包含私有 IP 地址的列表。
    """
    return [ip for ip in addresses if is_private_ip(ip)]


def _load_env_flag(key: str) -> bool:
    """从环境变量加载布尔标志。

    Args:
        key: 环境变量名称。

    Returns:
        如果环境变量的值为 "1"、"true"、"yes" 或 "on"（不区分大小写）则返回 True，
        否则返回 False。
    """
    return os.environ.get(key, "").strip().lower() in {"1", "true", "yes", "on"}


ENV_LAN_ONLY = "NOVEL_WEB_LAN_ONLY"
ENV_AGENT_FILE = "NOVEL_WEB_AGENT_FILE"
ENV_BOOK_NAME = "NOVEL_WEB_BOOK_NAME"
ENV_WORK_DIR = "NOVEL_WEB_WORK_DIR"


def create_app(
    session_token: str | None = None,
    allowed_origins: list[str] | None = None,
    enforce_origin: bool | None = None,
    restrict_sensitive_apis: bool | None = None,
    max_public_path_depth: int | None = None,
    lan_only: bool | None = None,
) -> FastAPI:
    """创建 Novel CLI Web UI 的 FastAPI 应用。

    Args:
        session_token: 会话认证令牌。如果为 None，将从环境变量读取。
        allowed_origins: 允许的跨域来源列表。如果为 None，将使用默认正则匹配。
        enforce_origin: 是否强制验证请求来源。如果为 None，将从环境变量读取。
        restrict_sensitive_apis: 是否限制敏感 API 访问。如果为 None，将从环境变量读取。
        max_public_path_depth: 公共路径的最大深度。如果为 None，将从环境变量读取。
        lan_only: 是否仅允许局域网访问。如果为 None，将从环境变量读取。

    Returns:
        配置完成的 FastAPI 应用实例。
    """

    env_token = os.environ.get(ENV_SESSION_TOKEN) or None
    env_origins = normalize_allowed_origins(os.environ.get(ENV_ALLOWED_ORIGINS))
    env_enforce_origin = _load_env_flag(ENV_ENFORCE_ORIGIN)
    env_restrict_sensitive = _load_env_flag(ENV_RESTRICT_SENSITIVE_APIS)
    env_max_depth_str = os.environ.get(ENV_MAX_PUBLIC_PATH_DEPTH)
    env_max_depth = (
        int(env_max_depth_str) if env_max_depth_str and env_max_depth_str.isdigit() else None
    )
    env_lan_only = _load_env_flag(ENV_LAN_ONLY)

    session_token = session_token if session_token is not None else env_token
    allowed_origins = allowed_origins if allowed_origins is not None else env_origins
    enforce_origin = enforce_origin if enforce_origin is not None else env_enforce_origin
    restrict_sensitive_apis = (
        restrict_sensitive_apis if restrict_sensitive_apis is not None else env_restrict_sensitive
    )
    max_public_path_depth = (
        max_public_path_depth if max_public_path_depth is not None else env_max_depth
    )
    lan_only = lan_only if lan_only is not None else env_lan_only

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.startup_dir = os.getcwd()
        app.state.session_token = session_token
        app.state.allowed_origins = allowed_origins
        app.state.enforce_origin = enforce_origin
        app.state.restrict_sensitive_apis = restrict_sensitive_apis
        app.state.max_public_path_depth = max_public_path_depth
        app.state.lan_only = lan_only

        # 从环境变量读取 CLI 启动默认参数
        app.state.defaults = WebDefaults(
            agent_file=os.environ.get(ENV_AGENT_FILE) or None,
            book_name=os.environ.get(ENV_BOOK_NAME) or None,
            work_dir=os.environ.get(ENV_WORK_DIR) or os.getcwd(),
        )

        # 启动 NovelCLI 运行器
        runner = NovelCLIRunner()
        app.state.runner = runner
        runner.start()

        try:
            yield
        finally:
            await runner.stop()

    application = FastAPI(
        title="Novel CLI Web Interface",
        docs_url=None,
        lifespan=lifespan,
        separate_input_output_schemas=False,
    )

    application.add_middleware(
        cast(Any, GZipMiddleware),
        minimum_size=GZIP_MINIMUM_SIZE,
        compresslevel=GZIP_COMPRESSION_LEVEL,
    )

    application.add_middleware(cast(Any, _StaticCacheHeadersMiddleware))

    application.add_middleware(
        cast(Any, AuthMiddleware),
        session_token=session_token,
        allowed_origins=allowed_origins,
        enforce_origin=enforce_origin,
        lan_only=lan_only,
    )

    cors_kwargs: dict[str, Any] = {
        "allow_credentials": True,
        "allow_methods": ["*"],
        "allow_headers": ["*"],
    }
    if allowed_origins:
        cors_kwargs["allow_origins"] = allowed_origins
    else:
        cors_kwargs["allow_origin_regex"] = DEFAULT_ALLOWED_ORIGIN_REGEX.pattern

    # CORS 中间件，用于本地开发
    application.add_middleware(cast(Any, CORSMiddleware), **cors_kwargs)

    application.include_router(config_router)
    application.include_router(sessions_router)
    application.include_router(work_dirs_router)
    application.include_router(defaults_router)
    application.include_router(books_router)
    if not restrict_sensitive_apis:
        application.include_router(open_in_router)

    @application.get("/scalar", include_in_schema=False)
    @application.get("/docs", include_in_schema=False)
    async def scalar_html() -> HTMLResponse:  # pyright: ignore[reportUnusedFunction]
        return get_scalar_api_reference(
            openapi_url=application.openapi_url or "",
            title=application.title,
        )

    @application.get("/healthz")
    async def health_probe() -> dict[str, Any]:  # pyright: ignore[reportUnusedFunction]
        """健康检查端点。"""
        return {"status": "ok"}

    # 挂载静态文件作为兜底（必须放在最后）
    if STATIC_DIR.exists():
        application.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

    return application


def run_web_server(
    host: str = "127.0.0.1",
    port: int = DEFAULT_PORT,
    reload: bool = False,
    open_browser: bool = True,
    auth_token: str | None = None,
    allowed_origins: str | None = None,
    dangerously_omit_auth: bool = False,
    restrict_sensitive_apis: bool | None = None,
    lan_only: bool = True,
    agent_file: str | None = None,
    book_name: str | None = None,
    work_dir: str | None = None,
) -> None:
    """运行 Web 服务器。

    Args:
        host: 服务器监听地址，默认为 "127.0.0.1"。
        port: 服务器监听端口，默认为 5494。
        reload: 是否启用热重载模式，默认为 False。
        open_browser: 是否自动打开浏览器，默认为 True。
        auth_token: 认证令牌。如果为 None，在公共模式下会自动生成。
        allowed_origins: 允许的跨域来源，多个来源用逗号分隔。
        dangerously_omit_auth: 是否禁用认证（危险选项），默认为 False。
        restrict_sensitive_apis: 是否限制敏感 API 访问。
        lan_only: 是否仅允许局域网访问，默认为 True。
        agent_file: 默认 agent 规格文件绝对路径。
        book_name: 默认书籍名称。
        work_dir: 默认工作目录绝对路径。

    Raises:
        RuntimeError: 在非交互模式下禁用认证时，或用户取消操作时抛出。
    """
    import sys
    import threading

    import uvicorn

    from novel_cli.utils.server import print_banner

    public_mode = not is_local_host(host)
    parsed_allowed_origins = normalize_allowed_origins(allowed_origins)
    auto_populate_origins = public_mode and not parsed_allowed_origins

    if restrict_sensitive_apis is None:
        # 仅在公共模式（非局域网模式）下限制敏感 API
        restrict_sensitive_apis = public_mode and not lan_only

    if public_mode and dangerously_omit_auth:
        warning_lines = [
            "SECURITY WARNING",
            "",
            "Authentication is DISABLED while running on a public host.",
            "Anyone on the network can access your sessions and files.",
            "",
            "Type 'I UNDERSTAND THE RISKS' to continue:",
        ]
        print_banner(warning_lines)
        if not sys.stdin.isatty():
            raise RuntimeError("Refusing to start without auth in non-interactive mode.")
        response = input("> ").strip()
        if response != "I UNDERSTAND THE RISKS":
            raise RuntimeError("Aborted by user.")

    if dangerously_omit_auth:
        session_token = None
    elif auth_token:
        session_token = auth_token
    elif public_mode:
        session_token = secrets.token_urlsafe(32)
    else:
        session_token = None

    if session_token:
        os.environ[ENV_SESSION_TOKEN] = session_token
    else:
        os.environ.pop(ENV_SESSION_TOKEN, None)

    # 首先查找可用端口（自动填充来源时需要）
    actual_port = find_available_port(host, port)
    if actual_port != port:
        print(f"Port {port} is in use, using port {actual_port} instead")

    # 自动填充允许的来源（使用检测到的网络地址 + 端口）
    if auto_populate_origins:
        auto_origins = [
            f"http://localhost:{actual_port}",
            f"http://127.0.0.1:{actual_port}",
        ]
        if host == "0.0.0.0":
            # 绑定所有接口：添加所有网络地址
            network_addrs = get_network_addresses()
            for addr in network_addrs:
                auto_origins.append(format_url(addr, actual_port))
        else:
            # 指定明确的主机：仅添加该主机
            auto_origins.append(format_url(host, actual_port))
        parsed_allowed_origins = auto_origins

    if parsed_allowed_origins:
        os.environ[ENV_ALLOWED_ORIGINS] = ",".join(parsed_allowed_origins)
    else:
        os.environ.pop(ENV_ALLOWED_ORIGINS, None)

    os.environ[ENV_ENFORCE_ORIGIN] = "1" if (public_mode and not lan_only) else "0"
    os.environ[ENV_RESTRICT_SENSITIVE_APIS] = "1" if restrict_sensitive_apis else "0"
    os.environ[ENV_LAN_ONLY] = "1" if lan_only else "0"

    # 设置 CLI 启动默认参数环境变量
    if agent_file:
        os.environ[ENV_AGENT_FILE] = agent_file
    else:
        os.environ.pop(ENV_AGENT_FILE, None)
    if book_name:
        os.environ[ENV_BOOK_NAME] = book_name
    else:
        os.environ.pop(ENV_BOOK_NAME, None)
    if work_dir:
        os.environ[ENV_WORK_DIR] = work_dir
    else:
        os.environ.pop(ENV_WORK_DIR, None)

    # 确定显示的 URL
    display_hosts: list[tuple[str, str]] = []
    if host == "0.0.0.0":
        # 展示 localhost 为"Local"及网络接口
        display_hosts.append(("Local", "localhost"))
        network_addrs = get_network_addresses()

        # 在 lan_only 模式下，仅显示私有 IP
        if lan_only:
            network_addrs = _get_private_addresses(network_addrs)

        for addr in network_addrs:
            display_hosts.append(("Network", addr))
    else:
        # 展示指定的主机
        label = "Local" if is_local_host(host) else "Network"
        display_hosts.append((label, host))

    # 构建带有令牌的 URL（如需要）
    def make_url(host_addr: str) -> tuple[str, str]:
        """生成 URL 和带令牌的浏览器 URL。

        Args:
            host_addr: 主机地址。

        Returns:
            (url, browser_url) 元组，browser_url 包含认证令牌（如有）。
        """
        url = format_url(host_addr, actual_port)
        browser_url = f"{url}/?token={quote(session_token)}" if session_token else url
        return url, browser_url

    # 打开浏览器时优先使用 localhost，其次使用第一个网络地址
    browser_host = "localhost" if host == "0.0.0.0" else host
    _, browser_url = make_url(browser_host)

    if open_browser:

        def open_browser_after_delay():
            """延迟后打开浏览器。"""
            import time

            time.sleep(1.5)
            webbrowser.open(browser_url)

        # 在守护线程中启动浏览器打开器
        thread = threading.Thread(target=open_browser_after_delay, daemon=True)
        thread.start()

    banner_lines = [
        "<center>███╗   ██╗ ██████╗ ██╗   ██╗███████╗██╗          ██████╗██╗     ██╗",
        "<center>████╗  ██║██╔═══██╗██║   ██║██╔════╝██║         ██╔════╝██║     ██║",
        "<center>██╔██╗ ██║██║   ██║██║   ██║█████╗  ██║         ██║     ██║     ██║",
        "<center>██║╚██╗██║██║   ██║╚██╗ ██╔╝██╔══╝  ██║         ██║     ██║     ██║",
        "<center>██║ ╚████║╚██████╔╝ ╚████╔╝ ███████╗███████╗    ╚██████╗███████╗██║",
        "<center>╚═╝  ╚═══╝ ╚═════╝   ╚═══╝  ╚══════╝╚══════╝     ╚═════╝╚══════╝╚═╝",
        "",
        "<center>WEB UI (Technical Preview)",
        "",
        "<hr>",
        "",
    ]

    # 为每个主机添加 URL（nowrap 保持单行以便复制）
    for label, host_addr in display_hosts:
        url, url_with_token = make_url(host_addr)
        if session_token:
            banner_lines.append(f"<nowrap>  ➜  {label:8} {url_with_token}")
        else:
            banner_lines.append(f"<nowrap>  ➜  {label:8} {url}")

    # 认证令牌或警告
    if session_token:
        banner_lines.extend(
            [
                "",
                f"<nowrap>  Token:   {session_token}",
            ]
        )
    elif public_mode:
        banner_lines.extend(
            [
                "",
                "<nowrap>  ⚠ AUTH DISABLED - Anyone on the network can access",
            ]
        )

    if restrict_sensitive_apis:
        banner_lines.append("<nowrap>  ⚠ Sensitive APIs are restricted")

    # 显示网络访问模式和提示
    banner_lines.append("")
    banner_lines.append("<hr>")
    banner_lines.append("")

    if not public_mode:
        # 仅本地模式（127.0.0.1）
        banner_lines.extend(
            [
                "<nowrap>  Tips:",
                "<nowrap>    • Use -n / --network to share on LAN",
                "<nowrap>    • Use --network --public for public access",
            ]
        )
    elif lan_only:
        # 局域网模式（0.0.0.0 且 lan_only）
        banner_lines.extend(
            [
                "<nowrap>  Mode: LAN only (private IPs)",
                "",
                "<nowrap>  Tips:",
                "<nowrap>    • Use --public to allow public access",
                "<nowrap>    • ⚠ Public mode allows access from any IP",
            ]
        )
    else:
        # 公共模式（0.0.0.0 且非 lan_only）
        banner_lines.extend(
            [
                "<nowrap>  ⚠ Mode: PUBLIC (all networks)",
                "<nowrap>    Anyone with the URL can access this instance",
                "",
                "<nowrap>  Security tips:",
                "<nowrap>    • Keep your auth token secure",
                "<nowrap>    • Consider using firewall or VPN",
            ]
        )

    banner_lines.append("")

    print_banner(banner_lines)
    # print(f"API docs available at {url}/docs")

    uvicorn.run(
        "novel_cli.web.app:create_app",
        factory=True,
        host=host,
        port=actual_port,
        reload=reload,
        log_level="info",
        timeout_graceful_shutdown=3,
    )


__all__ = ["create_app", "run_web_server"]
