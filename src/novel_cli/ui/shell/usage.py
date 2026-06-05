"""API 使用量和配额信息显示模块。

提供 /usage 和 /status 命令，用于显示当前 API 的使用量、配额限制及重置时间等信息。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

import aiohttp
from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.progress_bar import ProgressBar
from rich.table import Table
from rich.text import Text

from novel_cli.auth import NOVEL_CODE_PLATFORM_ID
from novel_cli.auth.platforms import get_platform_by_id, parse_managed_provider_key
from novel_cli.config import LLMModel
from novel_cli.soul.novelsoul import NovelSoul
from novel_cli.ui.shell.console import console
from novel_cli.ui.shell.slash import registry
from novel_cli.utils.aiohttp import new_client_session
from novel_cli.utils.datetime import format_duration

if TYPE_CHECKING:
    from novel_cli.ui.shell import Shell


@dataclass(slots=True, frozen=True)
class UsageRow:
    """使用量数据行。

    Attributes:
        label: 数据行标签。
        used: 已使用量。
        limit: 配额上限。
        reset_hint: 重置提示信息（可选）。
    """
    label: str
    used: int
    limit: int
    reset_hint: str | None = None


@registry.command(aliases=["/status"])
async def usage(app: Shell, args: str):
    """显示 API 使用量和配额信息。

    从 Novel Code 平台获取当前 API 的使用量数据，并以可视化面板形式展示。

    Args:
        app: Shell 应用实例。
        args: 命令参数（未使用）。
    """
    assert isinstance(app.soul, NovelSoul)
    if app.soul.runtime.llm is None:
        console.print("[red]LLM not set. Please run 'novel setup' to configure.[/red]")
        return

    provider = app.soul.runtime.llm.provider_config
    if provider is None:
        console.print("[red]LLM provider configuration not found.[/red]")
        return

    usage_url = _usage_url(app.soul.runtime.llm.model_config)
    if usage_url is None:
        console.print("[yellow]Usage is available on Novel Code platform only.[/yellow]")
        return

    with console.status("[cyan]Fetching usage...[/cyan]"):
        api_key = provider.api_key.get_secret_value()
        try:
            payload = await _fetch_usage(usage_url, api_key)
        except aiohttp.ClientResponseError as e:
            message = "Failed to fetch usage."
            if e.status == 401:
                message = "Authorization failed. Please check your API key."
            elif e.status == 404:
                message = "Usage endpoint not available. Try Novel For Coding."
            console.print(f"[red]{message}[/red]")
            return
        except TimeoutError:
            console.print("[red]Failed to fetch usage: request timed out.[/red]")
            return
        except aiohttp.ClientError as e:
            console.print(f"[red]Failed to fetch usage: {e}[/red]")
            return

    summary, limits = _parse_usage_payload(payload)
    if summary is None and not limits:
        console.print("[yellow]No usage data available.[/yellow]")
        return

    console.print(_build_usage_panel(summary, limits))


def _usage_url(model: LLMModel | None) -> str | None:
    """构建使用量查询 URL。

    Args:
        model: LLM 模型配置。

    Returns:
        使用量查询 URL，若不支持则返回 None。
    """
    if model is None:
        return None
    platform_id = parse_managed_provider_key(model.provider)
    if platform_id is None:
        return None
    platform = get_platform_by_id(platform_id)
    if platform is None or platform.id != NOVEL_CODE_PLATFORM_ID:
        return None
    base_url = platform.base_url.rstrip("/")
    return f"{base_url}/usages"


async def _fetch_usage(url: str, api_key: str) -> Mapping[str, Any]:
    """异步获取使用量数据。

    Args:
        url: 使用量查询 URL。
        api_key: API 密钥。

    Returns:
        使用量数据的 JSON 映射。

    Raises:
        aiohttp.ClientResponseError: HTTP 响应错误。
        TimeoutError: 请求超时。
        aiohttp.ClientError: 其他客户端错误。
    """
    async with (
        new_client_session() as session,
        session.get(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            raise_for_status=True,
        ) as resp,
    ):
        return await resp.json()


def _parse_usage_payload(
    payload: Mapping[str, Any],
) -> tuple[UsageRow | None, list[UsageRow]]:
    """解析使用量响应数据。

    Args:
        payload: 响应 JSON 数据。

    Returns:
        元组，包含摘要行和限制列表。
    """
    summary: UsageRow | None = None
    limits: list[UsageRow] = []

    usage = payload.get("usage")
    if isinstance(usage, Mapping):
        usage_map: Mapping[str, Any] = cast(Mapping[str, Any], usage)
        summary = _to_usage_row(usage_map, default_label="Weekly limit")

    raw_limits_obj = payload.get("limits")
    if isinstance(raw_limits_obj, Sequence):
        limits_seq: Sequence[Any] = cast(Sequence[Any], raw_limits_obj)
        for idx, item in enumerate(limits_seq):
            if not isinstance(item, Mapping):
                continue
            item_map: Mapping[str, Any] = cast(Mapping[str, Any], item)
            detail_raw = item_map.get("detail")
            detail: Mapping[str, Any] = (
                cast(Mapping[str, Any], detail_raw) if isinstance(detail_raw, Mapping) else item_map
            )
            # window 可能包含 duration/timeUnit
            window_raw = item_map.get("window")
            window: Mapping[str, Any] = (
                cast(Mapping[str, Any], window_raw) if isinstance(window_raw, Mapping) else {}
            )
            label = _limit_label(item_map, detail, window, idx)
            row = _to_usage_row(detail, default_label=label)
            if row:
                limits.append(row)

    return summary, limits


def _to_usage_row(data: Mapping[str, Any], *, default_label: str) -> UsageRow | None:
    """将数据映射转换为 UsageRow。

    Args:
        data: 数据映射。
        default_label: 默认标签。

    Returns:
        UsageRow 实例，若数据无效则返回 None。
    """
    limit = _to_int(data.get("limit"))
    # 支持两种格式："used" 和 "remaining"（used = limit - remaining）
    used = _to_int(data.get("used"))
    if used is None:
        remaining = _to_int(data.get("remaining"))
        if remaining is not None and limit is not None:
            used = limit - remaining
    if used is None and limit is None:
        return None
    return UsageRow(
        label=str(data.get("name") or data.get("title") or default_label),
        used=used or 0,
        limit=limit or 0,
        reset_hint=_reset_hint(data),
    )


def _limit_label(
    item: Mapping[str, Any],
    detail: Mapping[str, Any],
    window: Mapping[str, Any],
    idx: int,
) -> str:
    """生成限制项的可读标签。

    Args:
        item: 限制项数据。
        detail: 详细信息数据。
        window: 时间窗口数据。
        idx: 限制项索引。

    Returns:
        可读的限制标签字符串。
    """
    # 尝试提取可读标签
    for key in ("name", "title", "scope"):
        if val := (item.get(key) or detail.get(key)):
            return str(val)

    # 将 duration 转换为可读格式（如 300 minutes -> "5h quota")
    # 先检查 window，然后是 item，最后是 detail
    duration = _to_int(window.get("duration") or item.get("duration") or detail.get("duration"))
    time_unit = window.get("timeUnit") or item.get("timeUnit") or detail.get("timeUnit") or ""
    if duration:
        if "MINUTE" in time_unit:
            if duration >= 60 and duration % 60 == 0:
                return f"{duration // 60}h limit"
            return f"{duration}m limit"
        if "HOUR" in time_unit:
            return f"{duration}h limit"
        if "DAY" in time_unit:
            return f"{duration}d limit"
        return f"{duration}s limit"

    return f"Limit #{idx + 1}"


def _reset_hint(data: Mapping[str, Any]) -> str | None:
    """生成重置提示信息。

    Args:
        data: 数据映射。

    Returns:
        重置提示字符串，若无重置信息则返回 None。
    """
    for key in ("reset_at", "resetAt", "reset_time", "resetTime"):
        if val := data.get(key):
            return _format_reset_time(str(val))

    for key in ("reset_in", "resetIn", "ttl", "window"):
        seconds = _to_int(data.get(key))
        if seconds:
            return f"resets in {format_duration(seconds)}"

    return None


def _format_reset_time(val: str) -> str:
    """将 ISO 时间戳格式化为可读的时长描述。

    Args:
        val: ISO 格式时间戳字符串。

    Returns:
        格式化后的重置时间描述。
    """
    from datetime import UTC, datetime

    try:
        # 解析 ISO 格式如 "2025-12-23T05:24:18.443553353Z"
        # 将纳秒截断为微秒以兼容 Python
        if "." in val and val.endswith("Z"):
            base, frac = val[:-1].split(".")
            frac = frac[:6]  # Keep only microseconds
            val = f"{base}.{frac}Z"
        dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
        now = datetime.now(UTC)
        delta = dt - now

        if delta.total_seconds() <= 0:
            return "reset"
        return f"resets in {format_duration(int(delta.total_seconds()))}"
    except (ValueError, TypeError):
        return f"resets at {val}"


def _to_int(value: Any) -> int | None:
    """尝试将值转换为整数。

    Args:
        value: 待转换的值。

    Returns:
        转换后的整数，若转换失败则返回 None。
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _build_usage_panel(summary: UsageRow | None, limits: list[UsageRow]) -> Panel:
    """构建使用量可视化面板。

    Args:
        summary: 摘要行数据。
        limits: 限制项列表。

    Returns:
        Rich Panel 实例。
    """
    rows = ([summary] if summary else []) + limits
    if not rows:
        return Panel(
            Text("No usage data", style="grey50"), title="API Usage", border_style="wheat4"
        )

    # 计算标签宽度以对齐
    label_width = max(len(r.label) for r in rows)
    label_width = max(label_width, 6)  # 最小宽度

    lines: list[RenderableType] = []
    for row in rows:
        lines.append(_format_row(row, label_width))

    return Panel(
        Group(*lines),
        title="API Usage",
        border_style="wheat4",
        padding=(0, 2),
        expand=False,
    )


def _format_row(row: UsageRow, label_width: int) -> RenderableType:
    """格式化单行使用量数据。

    Args:
        row: 使用量数据行。
        label_width: 标签宽度。

    Returns:
        可渲染的表格组件。
    """
    ratio = (row.limit - row.used) / row.limit if row.limit > 0 else 0
    color = _ratio_color(ratio)

    label = Text(f"{row.label:<{label_width}}  ", style="cyan")
    bar = ProgressBar(total=row.limit or 1, completed=row.used, width=20, complete_style=color)

    detail = Text()
    percent = ratio * 100
    detail.append(f"  {percent:.0f}% left", style="bold")
    if row.reset_hint:
        detail.append(f"  ({row.reset_hint})", style="grey50")

    t = Table.grid(padding=0)
    t.add_column(width=label_width + 2)
    t.add_column(width=20)
    t.add_column()
    t.add_row(label, bar, detail)
    return t


def _ratio_color(ratio: float) -> str:
    """根据剩余比例返回对应的颜色。

    Args:
        ratio: 剩余比例（0.0-1.0）。

    Returns:
        Rich 颜色名称。
    """
    if ratio >= 0.9:
        return "red"
    if ratio >= 0.7:
        return "yellow"
    return "green"
