"""Web 搜索工具实现。

提供 SearchWeb 工具，用于通过远程搜索服务在 web 上搜索内容。
"""

from pathlib import Path
from typing import override

import aiohttp
from kosong.tooling import CallableTool2, ToolReturnValue
from pydantic import BaseModel, Field, ValidationError

from novel_cli.config import Config
from novel_cli.constant import USER_AGENT
from novel_cli.soul.agent import Runtime
from novel_cli.soul.toolset import get_current_tool_call_or_none
from novel_cli.tools import SkipThisTool
from novel_cli.tools.utils import ToolResultBuilder, load_desc
from novel_cli.utils.aiohttp import new_client_session


class Params(BaseModel):
    """Web 搜索参数。

    Attributes:
        query: 搜索查询文本。
        limit: 返回结果数量，默认为 5。
        include_content: 是否包含网页内容，开启后可能消耗大量 token。
    """

    query: str = Field(description="The query text to search for.")
    limit: int = Field(
        description=(
            "The number of results to return. "
            "Typically you do not need to set this value. "
            "When the results do not contain what you need, "
            "you probably want to give a more concrete query."
        ),
        default=5,
        ge=1,
        le=20,
    )
    include_content: bool = Field(
        description=(
            "Whether to include the content of the web pages in the results. "
            "It can consume a large amount of tokens when this is set to True. "
            "You should avoid enabling this when `limit` is set to a large value."
        ),
        default=False,
    )


class SearchWeb(CallableTool2[Params]):
    """Web 搜索工具。

    用于通过远程搜索服务在 web 上搜索内容，
    支持返回搜索结果摘要和完整网页内容。

    Attributes:
        name: 工具名称。
        description: 工具描述。
        params: 参数类型。
    """

    name: str = "SearchWeb"
    description: str = load_desc(Path(__file__).parent / "search.md", {})
    params: type[Params] = Params

    def __init__(self, config: Config, runtime: Runtime):
        """初始化 Web 搜索工具。

        Args:
            config: 配置对象，包含搜索服务配置信息。
            runtime: 运行时环境。

        Raises:
            SkipThisTool: 如果搜索服务未配置，则跳过此工具。
        """
        super().__init__()
        if config.services.moonshot_search is None:
            raise SkipThisTool()
        self._base_url = config.services.moonshot_search.base_url
        self._api_key = config.services.moonshot_search.api_key
        self._custom_headers = config.services.moonshot_search.custom_headers or {}

    @override
    async def __call__(self, params: Params) -> ToolReturnValue:
        """执行 Web 搜索操作。

        Args:
            params: 搜索参数，包含查询文本和其他选项。

        Returns:
            操作结果，搜索结果列表或错误信息。
        """
        builder = ToolResultBuilder(max_line_length=None)

        api_key = self._api_key.get_secret_value()
        if not self._base_url or not api_key:
            return builder.error(
                "Search service is not configured. You may want to try other methods to search.",
                brief="Search service not configured",
            )

        tool_call = get_current_tool_call_or_none()
        assert tool_call is not None, "Tool call is expected to be set"

        try:
            # 服务端超时为 30 秒，但网页爬取可能需要更长时间
            search_timeout = aiohttp.ClientTimeout(total=180, sock_read=90, sock_connect=15)
            async with (
                new_client_session(timeout=search_timeout) as session,
                session.post(
                    self._base_url,
                    headers={
                        "User-Agent": USER_AGENT,
                        "Authorization": f"Bearer {api_key}",
                        "X-Msh-Tool-Call-Id": tool_call.id,
                        **self._custom_headers,
                    },
                    json={
                        "text_query": params.query,
                        "limit": params.limit,
                        "enable_page_crawling": params.include_content,
                        "timeout_seconds": 30,
                    },
                ) as response,
            ):
                if response.status != 200:
                    return builder.error(
                        (
                            f"Failed to search. Status: {response.status}. "
                            "This may indicate that the search service is currently unavailable."
                        ),
                        brief="Failed to search",
                    )

                try:
                    results = Response(**await response.json()).search_results
                except ValidationError as e:
                    return builder.error(
                        (
                            f"Failed to parse search results. Error: {e}. "
                            "This may indicate that the search service is currently unavailable."
                        ),
                        brief="Failed to parse search results",
                    )
        except TimeoutError:
            return builder.error(
                "Search request timed out. The search service may be slow or unavailable.",
                brief="Search request timed out",
            )
        except aiohttp.ClientError as e:
            return builder.error(
                f"Search request failed: {e}. The search service may be unavailable.",
                brief="Search request failed",
            )

        for i, result in enumerate(results):
            if i > 0:
                builder.write("---\n\n")
            builder.write(
                f"Title: {result.title}\nDate: {result.date}\n"
                f"URL: {result.url}\nSummary: {result.snippet}\n\n"
            )
            if result.content:
                builder.write(f"{result.content}\n\n")

        return builder.ok()


class SearchResult(BaseModel):
    """搜索结果项。

    Attributes:
        site_name: 网站名称。
        title: 页面标题。
        url: 页面 URL。
        snippet: 页面摘要。
        content: 页面内容（可选）。
        date: 发布日期（可选）。
        icon: 网站图标（可选）。
        mime: MIME 类型（可选）。
    """

    site_name: str
    title: str
    url: str
    snippet: str
    content: str = ""
    date: str = ""
    icon: str = ""
    mime: str = ""


class Response(BaseModel):
    """搜索响应。

    Attributes:
        search_results: 搜索结果列表。
    """

    search_results: list[SearchResult]
