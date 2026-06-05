"""rich 列布局扩展模块。

提供带项目符号的列布局组件，用于终端富文本展示。
"""

from __future__ import annotations

from rich.columns import Columns
from rich.console import Console, ConsoleOptions, RenderableType, RenderResult
from rich.measure import Measurement
from rich.segment import Segment
from rich.text import Text


class _ShrinkToWidth:
    """宽度约束渲染器。

    将可渲染对象约束到指定最大宽度。

    Attributes:
        _renderable: 待约束的可渲染对象。
        _max_width: 最大宽度。
    """

    def __init__(self, renderable: RenderableType, max_width: int) -> None:
        """初始化宽度约束渲染器。

        Args:
            renderable: 待约束的可渲染对象。
            max_width: 最大宽度，最小为 1。
        """
        self._renderable = renderable
        self._max_width = max(max_width, 1)

    def __rich_measure__(self, console: Console, options: ConsoleOptions) -> Measurement:
        """计算渲染对象的测量尺寸。

        Args:
            console: 控制台实例。
            options: 控制台选项。

        Returns:
            Measurement 对象，表示渲染尺寸范围。
        """
        width = self._resolve_width(options)
        return Measurement(0, width)

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        """渲染对象。

        Args:
            console: 控制台实例。
            options: 控制台选项。

        Yields:
            渲染后的 Segment 对象。
        """
        width = self._resolve_width(options)
        child_options = options.update(width=width)
        yield from console.render(self._renderable, child_options)

    def _resolve_width(self, options: ConsoleOptions) -> int:
        """解析实际渲染宽度。

        Args:
            options: 控制台选项。

        Returns:
            实际渲染宽度，在最大宽度和可用宽度之间取较小值，最小为 1。
        """
        return max(1, min(self._max_width, options.max_width))


def _strip_trailing_spaces(segments: list[Segment]) -> list[Segment]:
    """移除每行末尾的空格。

    Args:
        segments: Segment 列表。

    Returns:
        处理后的 Segment 列表，每行末尾无空格。
    """
    lines = list(Segment.split_lines(segments))
    trimmed: list[Segment] = []
    n_lines = len(lines)
    for index, line in enumerate(lines):
        line_segments = list(line)
        while line_segments:
            segment = line_segments[-1]
            if segment.control is not None:
                break
            trimmed_text = segment.text.rstrip(" ")
            if trimmed_text != segment.text:
                if trimmed_text:
                    line_segments[-1] = Segment(trimmed_text, segment.style, segment.control)
                    break
                line_segments.pop()
                continue
            break
        trimmed.extend(line_segments)
        if index != n_lines - 1:
            trimmed.append(Segment.line())
    if trimmed:
        trimmed.append(Segment.line())
    return trimmed


class BulletColumns:
    """带项目符号的列布局组件。

    在可渲染内容前添加项目符号，支持自动宽度调整。

    Attributes:
        _renderable: 内容可渲染对象。
        _bullet: 项目符号可渲染对象。
        _bullet_style: 项目符号样式。
        _padding: 符号与内容之间的间距。
    """

    def __init__(
        self,
        renderable: RenderableType,
        *,
        bullet_style: str | None = None,
        bullet: RenderableType | None = None,
        padding: int = 1,
    ) -> None:
        """初始化带项目符号的列布局。

        Args:
            renderable: 内容可渲染对象。
            bullet_style: 项目符号样式字符串。
            bullet: 自定义项目符号可渲染对象。
            padding: 符号与内容之间的间距，默认为 1。
        """
        self._renderable = renderable
        self._bullet = bullet
        self._bullet_style = bullet_style
        self._padding = padding

    def _bullet_renderable(self) -> RenderableType:
        """获取项目符号的可渲染对象。

        Returns:
            项目符号的可渲染对象，默认为样式的圆点符号。
        """
        if self._bullet is not None:
            return self._bullet
        return Text("•", style=self._bullet_style or "")

    def _available_width(self, console: Console, options: ConsoleOptions, bullet_width: int) -> int:
        """计算内容可用的宽度。

        Args:
            console: 控制台实例。
            options: 控制台选项。
            bullet_width: 项目符号占用的宽度。

        Returns:
            内容可用的宽度，最小为 1。
        """
        max_width = options.max_width or console.width or (bullet_width + self._padding + 1)
        available = max_width - bullet_width - self._padding
        return max(available, 1)

    def __rich_measure__(self, console: Console, options: ConsoleOptions) -> Measurement:
        """计算渲染对象的测量尺寸。

        Args:
            console: 控制台实例。
            options: 控制台选项。

        Returns:
            Measurement 对象，表示渲染尺寸范围。
        """
        bullet = self._bullet_renderable()
        bullet_measure = Measurement.get(console, options, bullet)
        bullet_width = max(bullet_measure.maximum, 1)
        available = self._available_width(console, options, bullet_width)
        constrained = _ShrinkToWidth(self._renderable, available)
        columns = Columns([bullet, constrained], expand=False, padding=(0, self._padding))
        return Measurement.get(console, options, columns)

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        """渲染组件。

        Args:
            console: 控制台实例。
            options: 控制台选项。

        Yields:
            渲染后的 Segment 对象。
        """
        bullet = self._bullet_renderable()
        bullet_measure = Measurement.get(console, options, bullet)
        bullet_width = max(bullet_measure.maximum, 1)
        available = self._available_width(console, options, bullet_width)
        columns = Columns(
            [bullet, _ShrinkToWidth(self._renderable, available)],
            expand=False,
            padding=(0, self._padding),
        )
        segments = list(console.render(columns, options))
        trimmed = _strip_trailing_spaces(segments)
        yield from trimmed