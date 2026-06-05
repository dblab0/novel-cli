"""URL 获取工具实现。

提供 FetchURL 工具，用于从指定 URL 获取内容，
支持通过远程服务和本地 HTTP GET 两种方式获取。
"""

from pathlib import Path
from typing import override

import aiohttp
import trafilatura
from kosong.tooling import CallableTool2, ToolReturnValue
from pydantic import BaseModel, Field

from novel_cli.config import Config
from novel_cli.constant import USER_AGENT
from novel_cli.soul.agent import Runtime
from novel_cli.soul.toolset import get_current_tool_call_or_none
from novel_cli.tools.utils import ToolResultBuilder, load_desc
from novel_cli.utils.aiohttp import new_client_session
from novel_cli.utils.logging import logger


class Params(BaseModel):
    """URL 获取参数。

    Attributes:
        url: 要获取内容的 URL。
    """

    url: str = Field(description="The URL to fetch content from.")


class FetchURL(CallableTool2[Params]):
    """URL 获取工具。

    用于从指定 URL 获取网页内容，支持两种获取方式：
    1. 远程服务获取（如果配置了 moonshot_fetch 服务）
    2. 本地 HTTP GET 获取（作为备用方案）

    Attributes:
        name: 工具名称。
        description: 工具描述。
        params: 参数类型。
    """

    name: str = "FetchURL"
    description: str = load_desc(Path(__file__).parent / "fetch.md", {})
    params: type[Params] = Params

    def __init__(self, config: Config, runtime: Runtime):
        """初始化 URL 获取工具。

        Args:
            config: 配置对象，包含服务配置信息。
            runtime: 运行时环境。
        """
        super().__init__()
        self._runtime = runtime
        self._service_config = config.services.moonshot_fetch

    @override
    async def __call__(self, params: Params) -> ToolReturnValue:
        """执行 URL 获取操作。

        Args:
            params: 获取参数，包含目标 URL。

        Returns:
            操作结果，获取的内容或错误信息。
        """
        if self._service_config:
            ret = await self._fetch_with_service(params)
            if not ret.is_error:
                return ret
            logger.warning("Failed to fetch URL via service: {error}", error=ret.message)
            # 如果服务获取失败，则回退到本地 HTTP GET 获取
        return await self.fetch_with_http_get(params)

    @staticmethod
    async def fetch_with_http_get(params: Params) -> ToolReturnValue:
        """使用 HTTP GET 方式获取 URL 内容。

        Args:
            params: 获取参数，包含目标 URL。

        Returns:
            操作结果，获取的内容或错误信息。
        """
        builder = ToolResultBuilder(max_line_length=None)
        try:
            # 获取大型或慢速网页可能需要较长时间
            fetch_timeout = aiohttp.ClientTimeout(total=180, sock_read=60, sock_connect=15)
            async with (
                new_client_session(timeout=fetch_timeout) as session,
                session.get(
                    params.url,
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                            "(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
                        ),
                    },
                ) as response,
            ):
                if response.status >= 400:
                    return builder.error(
                        (
                            f"Failed to fetch URL. Status: {response.status}. "
                            f"This may indicate the page is not accessible or the server is down."
                        ),
                        brief=f"HTTP {response.status} error",
                    )

                resp_text = await response.text()

                content_type = response.headers.get(aiohttp.hdrs.CONTENT_TYPE, "").lower()
                if content_type.startswith(("text/plain", "text/markdown")):
                    builder.write(resp_text)
                    return builder.ok("The returned content is the full content of the page.")
        except TimeoutError:
            return builder.error(
                "Failed to fetch URL: request timed out. The server may be slow or unreachable.",
                brief="Request timed out",
            )
        except aiohttp.ClientError as e:
            return builder.error(
                (
                    f"Failed to fetch URL due to network error: {e}. "
                    "This may indicate the URL is invalid or the server is unreachable."
                ),
                brief="Network error",
            )

        if not resp_text:
            return builder.ok(
                "The response body is empty.",
                brief="Empty response body",
            )

        extracted_text = trafilatura.extract(
            resp_text,
            include_comments=True,
            include_tables=True,
            include_formatting=False,
            output_format="txt",
            with_metadata=True,
        )

        if not extracted_text:
            return builder.error(
                (
                    "Failed to extract meaningful content from the page. "
                    "This may indicate the page content is not suitable for text extraction, "
                    "or the page requires JavaScript to render its content."
                ),
                brief="No content extracted",
            )

        builder.write(extracted_text)
        return builder.ok("The returned content is the main text content extracted from the page.")

    async def _fetch_with_service(self, params: Params) -> ToolReturnValue:
        """使用远程服务获取 URL 内容。

        Args:
            params: 获取参数，包含目标 URL。

        Returns:
            操作结果，获取的内容或错误信息。
        """
        assert self._service_config is not None

        tool_call = get_current_tool_call_or_none()
        assert tool_call is not None, "Tool call is expected to be set"

        builder = ToolResultBuilder(max_line_length=None)
        api_key = self._service_config.api_key.get_secret_value()
        if not api_key:
            return builder.error(
                "Fetch service is not configured. You may want to try other methods to fetch.",
                brief="Fetch service not configured",
            )
        headers = {
            "User-Agent": USER_AGENT,
            "Authorization": f"Bearer {api_key}",
            "Accept": "text/markdown",
            "X-Msh-Tool-Call-Id": tool_call.id,
            **(self._service_config.custom_headers or {}),
        }

        try:
            async with (
                new_client_session() as session,
                session.post(
                    self._service_config.base_url,
                    headers=headers,
                    json={"url": params.url},
                ) as response,
            ):
                if response.status != 200:
                    return builder.error(
                        f"Failed to fetch URL via service. Status: {response.status}.",
                        brief="Failed to fetch URL via fetch service",
                    )

                content = await response.text()
                builder.write(content)
                return builder.ok(
                    "The returned content is the main content extracted from the page."
                )
        except TimeoutError:
            return builder.error(
                "Failed to fetch URL via service: request timed out.",
                brief="Service request timed out",
            )
        except aiohttp.ClientError as e:
            return builder.error(
                (
                    f"Failed to fetch URL via service due to network error: {e}. "
                    "This may indicate the service is unreachable."
                ),
                brief="Network error when calling fetch service",
            )
