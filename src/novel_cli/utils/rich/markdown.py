"""Markdown 渲染模块。

本模块基于 Rich 库的 markdown.py 进行修改，提供 Markdown 文本到控制台的渲染功能。
支持标题、代码块、引用块、列表、表格等常见 Markdown 元素的渲染，并集成了语法高亮功能。

源文件参考：https://github.com/Textualize/rich/blob/4d6d631a3d2deddf8405522d4b8c976a6d35726c/rich/markdown.py
"""

# This file is modified from https://github.com/Textualize/rich/blob/4d6d631a3d2deddf8405522d4b8c976a6d35726c/rich/markdown.py
# pyright: standard

from __future__ import annotations

import sys
from collections.abc import Iterable, Mapping
from typing import ClassVar, get_args

from markdown_it import MarkdownIt
from markdown_it.token import Token
from rich import box
from rich._loop import loop_first
from rich._stack import Stack
from rich.console import Console, ConsoleOptions, JustifyMethod, RenderResult
from rich.containers import Renderables
from rich.jupyter import JupyterMixin
from rich.rule import Rule
from rich.segment import Segment
from rich.style import Style, StyleStack
from rich.syntax import Syntax, SyntaxTheme
from rich.table import Table
from rich.text import Text, TextType

from novel_cli.utils.rich.syntax import NOVEL_ANSI_THEME_NAME, resolve_code_theme

LIST_INDENT_WIDTH = 2

_FALLBACK_STYLES: Mapping[str, Style] = {
    "markdown.paragraph": Style(),
    "markdown.h1": Style(color="bright_white", bold=True),
    "markdown.h1.underline": Style(color="bright_white", bold=True),
    "markdown.h2": Style(color="white", bold=True, underline=True),
    "markdown.h3": Style(bold=True),
    "markdown.h4": Style(bold=True),
    "markdown.h5": Style(bold=True),
    "markdown.h6": Style(dim=True, italic=True),
    "markdown.code": Style(color="bright_cyan", bold=True),
    "markdown.code_block": Style(color="bright_cyan"),
    "markdown.item": Style(),
    "markdown.item.bullet": Style(),
    "markdown.item.number": Style(),
    "markdown.em": Style(italic=True),
    "markdown.strong": Style(bold=True),
    "markdown.s": Style(strike=True),
    "markdown.link": Style(color="bright_blue", underline=True),
    "markdown.link_url": Style(color="cyan", underline=True),
    "markdown.block_quote": Style(),
    "markdown.hr": Style(color="grey58"),
}


def _strip_background(text: Text) -> Text:
    """移除文本中所有背景颜色，返回一个副本。

    Args:
        text: 要处理的 Text 对象。

    Returns:
        移除了所有背景颜色的 Text 对象副本。
    """
    clean = Text(
        text.plain,
        justify=text.justify,
        overflow=text.overflow,
        no_wrap=text.no_wrap,
        end=text.end,
        tab_size=text.tab_size,
    )

    if text.style:
        base_style = text.style
        if not isinstance(base_style, Style):
            base_style = Style.parse(str(base_style))
        base_style = base_style.copy()
        if base_style._bgcolor is not None:
            base_style._bgcolor = None
        clean.stylize(base_style, 0, len(clean))

    for span in text.spans:
        style = span.style
        if style is None:
            continue
        new_style = Style.parse(str(style)) if not isinstance(style, Style) else style.copy()
        if new_style._bgcolor is not None:
            new_style._bgcolor = None
        clean.stylize(new_style, span.start, span.end)

    return clean


class MarkdownElement:
    """Markdown 元素基类。

    所有 Markdown 渲染元素的抽象基类，定义了元素的生命周期钩子方法。

    Attributes:
        new_line: 是否在元素后添加换行。
    """

    new_line: ClassVar[bool] = True

    @classmethod
    def create(cls, markdown: Markdown, token: Token) -> MarkdownElement:
        """创建 Markdown 元素的工厂方法。

        Args:
            markdown: 父级 Markdown 对象。
            token: markdown-it 解析器产生的节点。

        Returns:
            新创建的 MarkdownElement 实例。
        """
        return cls()

    def on_enter(self, context: MarkdownContext) -> None:
        """当解析器进入该节点时调用。

        Args:
            context: Markdown 渲染上下文。
        """

    def on_text(self, context: MarkdownContext, text: TextType) -> None:
        """当解析文本时调用。

        Args:
            context: Markdown 渲染上下文。
            text: 解析得到的文本内容。
        """

    def on_leave(self, context: MarkdownContext) -> None:
        """当解析器离开该元素时调用。

        Args:
            context: Markdown 渲染上下文。
        """

    def on_child_close(self, context: MarkdownContext, child: MarkdownElement) -> bool:
        """当子元素关闭时调用。

        此方法允许父元素接管其子元素的渲染。

        Args:
            context: Markdown 渲染上下文。
            child: 子 Markdown 元素。

        Returns:
            返回 True 表示渲染该元素，返回 False 表示不渲染。
        """
        return True

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        return ()


class UnknownElement(MarkdownElement):
    """未知元素。

    用于处理文档中未知的 Markdown 元素。理想情况下，文档中的所有元素都应有对应的 MarkdownElement 实现。
    """


class TextElement(MarkdownElement):
    """文本渲染元素的基类。

    用于渲染文本内容的元素基类。

    Attributes:
        style_name: 样式名称。
    """

    style_name = "none"

    def on_enter(self, context: MarkdownContext) -> None:
        self.style = context.enter_style(self.style_name)
        self.text = Text(justify="left")

    def on_text(self, context: MarkdownContext, text: TextType) -> None:
        self.text.append(text, context.current_style if isinstance(text, str) else None)

    def on_leave(self, context: MarkdownContext) -> None:
        context.leave_style()


class Paragraph(TextElement):
    """段落元素。

    用于渲染 Markdown 段落。

    Attributes:
        style_name: 样式名称。
        justify: 文本对齐方式。
    """

    style_name = "markdown.paragraph"
    justify: JustifyMethod

    @classmethod
    def create(cls, markdown: Markdown, token: Token) -> Paragraph:
        return cls(justify=markdown.justify or "left")

    def __init__(self, justify: JustifyMethod) -> None:
        self.justify = justify

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        self.text.justify = self.justify
        yield self.text


class Heading(TextElement):
    """标题元素。

    用于渲染 Markdown 标题（h1-h6）。

    Attributes:
        tag: HTML 标签名称（如 'h1', 'h2' 等）。
        style_name: 样式名称。
    """

    @classmethod
    def create(cls, markdown: Markdown, token: Token) -> Heading:
        return cls(token.tag)

    def on_enter(self, context: MarkdownContext) -> None:
        self.text = Text()
        context.enter_style(self.style_name)

    def __init__(self, tag: str) -> None:
        self.tag = tag
        self.style_name = f"markdown.{tag}"
        super().__init__()

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        text = self.text
        text.justify = "left"
        width = max(1, text.cell_len)

        if self.tag == "h1":
            underline = Text("═" * width)
            underline.stylize("markdown.h1.underline")
            yield text
            yield underline
        else:
            yield text


class CodeBlock(TextElement):
    """代码块元素。

    用于渲染带有语法高亮的 Markdown 代码块。

    Attributes:
        style_name: 样式名称。
        lexer_name: 语法分析器名称。
        theme: 代码主题。
    """

    style_name = "markdown.code_block"

    @classmethod
    def create(cls, markdown: Markdown, token: Token) -> CodeBlock:
        node_info = token.info or ""
        lexer_name = node_info.partition(" ")[0]
        return cls(lexer_name or "text", markdown.code_theme)

    def __init__(self, lexer_name: str, theme: str | SyntaxTheme) -> None:
        self.lexer_name = lexer_name
        self.theme = theme

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        code = str(self.text).rstrip()
        syntax = Syntax(
            code,
            self.lexer_name,
            theme=self.theme,
            word_wrap=True,
            background_color=None,
            padding=0,
        )
        highlighted = syntax.highlight(code)
        highlighted.rstrip()
        stripped = _strip_background(highlighted)
        stripped.rstrip()
        yield stripped


class BlockQuote(TextElement):
    """引用块元素。

    用于渲染 Markdown 引用块。

    Attributes:
        style_name: 样式名称。
        elements: 包含的子渲染元素列表。
    """

    style_name = "markdown.block_quote"

    def __init__(self) -> None:
        self.elements: Renderables = Renderables()

    def on_child_close(self, context: MarkdownContext, child: MarkdownElement) -> bool:
        self.elements.append(child)
        return False

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        render_options = options.update(width=options.max_width - 4)
        style = self.style.without_color
        lines = console.render_lines(self.elements, render_options, style=style)
        new_line = Segment("\n")
        padding = Segment("▌ ", style)
        for line in lines:
            yield padding
            yield from line
            yield new_line


class HorizontalRule(MarkdownElement):
    """水平分割线元素。

    用于渲染分隔章节的水平线。

    Attributes:
        new_line: 是否在元素后添加换行。
    """

    new_line = False

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        style = _FALLBACK_STYLES["markdown.hr"].copy()
        yield Rule(style=style)


class TableElement(MarkdownElement):
    """表格元素。

    对应于 `table_open` 标记的 MarkdownElement。

    Attributes:
        header: 表格头部元素。
        body: 表格主体元素。
    """

    def __init__(self) -> None:
        self.header: TableHeaderElement | None = None
        self.body: TableBodyElement | None = None

    def on_child_close(self, context: MarkdownContext, child: MarkdownElement) -> bool:
        if isinstance(child, TableHeaderElement):
            self.header = child
        elif isinstance(child, TableBodyElement):
            self.body = child
        else:
            raise RuntimeError("无法处理 Markdown 表格。")
        return False

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        table = Table(box=box.SIMPLE_HEAVY, show_edge=False)

        if self.header is not None and self.header.row is not None:
            for column in self.header.row.cells:
                table.add_column(column.content)

        if self.body is not None:
            for row in self.body.rows:
                row_content = [element.content for element in row.cells]
                table.add_row(*row_content)

        yield table


class TableHeaderElement(MarkdownElement):
    """表格头部元素。

    对应于 `thead_open` 和 `thead_close` 标记的 MarkdownElement。

    Attributes:
        row: 表格行元素。
    """

    def __init__(self) -> None:
        self.row: TableRowElement | None = None

    def on_child_close(self, context: MarkdownContext, child: MarkdownElement) -> bool:
        assert isinstance(child, TableRowElement)
        self.row = child
        return False


class TableBodyElement(MarkdownElement):
    """表格主体元素。

    对应于 `tbody_open` 和 `tbody_close` 标记的 MarkdownElement。

    Attributes:
        rows: 表格行元素列表。
    """

    def __init__(self) -> None:
        self.rows: list[TableRowElement] = []

    def on_child_close(self, context: MarkdownContext, child: MarkdownElement) -> bool:
        assert isinstance(child, TableRowElement)
        self.rows.append(child)
        return False


class TableRowElement(MarkdownElement):
    """表格行元素。

    对应于 `tr_open` 和 `tr_close` 标记的 MarkdownElement。

    Attributes:
        cells: 表格单元格元素列表。
    """

    def __init__(self) -> None:
        self.cells: list[TableDataElement] = []

    def on_child_close(self, context: MarkdownContext, child: MarkdownElement) -> bool:
        assert isinstance(child, TableDataElement)
        self.cells.append(child)
        return False


class TableDataElement(MarkdownElement):
    """表格单元格元素。

    对应于 `td_open`、`td_close`、`th_open` 和 `th_close` 标记的 MarkdownElement。

    Attributes:
        content: 单元格文本内容。
        justify: 文本对齐方式。
    """

    @classmethod
    def create(cls, markdown: Markdown, token: Token) -> MarkdownElement:
        style = str(token.attrs.get("style")) or ""

        justify: JustifyMethod
        if "text-align:right" in style:
            justify = "right"
        elif "text-align:center" in style:
            justify = "center"
        elif "text-align:left" in style:
            justify = "left"
        else:
            justify = "default"

        assert justify in get_args(JustifyMethod)
        return cls(justify=justify)

    def __init__(self, justify: JustifyMethod) -> None:
        self.content: Text = Text("", justify=justify)
        self.justify = justify

    def on_text(self, context: MarkdownContext, text: TextType) -> None:
        text = Text(text) if isinstance(text, str) else text
        text.stylize(context.current_style)
        self.content.append_text(text)


class ListElement(MarkdownElement):
    """列表元素。

    用于渲染 Markdown 有序或无序列表。

    Attributes:
        items: 列表项元素列表。
        list_type: 列表类型（'bullet_list_open' 或 'ordered_list_open'）。
        list_start: 有序列表的起始编号。
    """

    @classmethod
    def create(cls, markdown: Markdown, token: Token) -> ListElement:
        return cls(token.type, int(token.attrs.get("start", 1)))

    def __init__(self, list_type: str, list_start: int | None) -> None:
        self.items: list[ListItem] = []
        self.list_type = list_type
        self.list_start = list_start

    def on_child_close(self, context: MarkdownContext, child: MarkdownElement) -> bool:
        assert isinstance(child, ListItem)
        self.items.append(child)
        return False

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        if self.list_type == "bullet_list_open":
            for item in self.items:
                yield from item.render_bullet(console, options)
        else:
            number = 1 if self.list_start is None else self.list_start
            last_number = number + len(self.items)
            for index, item in enumerate(self.items):
                yield from item.render_number(console, options, number + index, last_number)


class ListItem(TextElement):
    """列表项元素。

    用于渲染 Markdown 列表中的单个项目。

    Attributes:
        style_name: 样式名称。
        indent: 缩进层级。
        elements: 包含的子渲染元素列表。
    """

    style_name = "markdown.item"

    @staticmethod
    def _line_starts_with_list_marker(text: str) -> bool:
        stripped = text.lstrip()
        if not stripped:
            return False
        if stripped.startswith(("• ", "- ", "* ")):
            return True
        index = 0
        while index < len(stripped) and stripped[index].isdigit():
            index += 1
        if index == 0 or index >= len(stripped):
            return False
        marker = stripped[index]
        has_space = index + 1 < len(stripped) and stripped[index + 1] == " "
        return marker in {".", ")"} and has_space

    @classmethod
    def create(cls, markdown: Markdown, token: Token) -> MarkdownElement:
        # `list_item_open` 的层级按嵌套深度每层增加 2
        depth = max(0, (token.level - 1) // 2)
        return cls(indent=depth)

    def __init__(self, indent: int = 0) -> None:
        self.indent = indent
        self.elements: Renderables = Renderables()

    def on_child_close(self, context: MarkdownContext, child: MarkdownElement) -> bool:
        self.elements.append(child)
        return False

    def render_bullet(self, console: Console, options: ConsoleOptions) -> RenderResult:
        lines = console.render_lines(self.elements, options, style=self.style)
        indent_padding_len = LIST_INDENT_WIDTH * self.indent
        indent_text = " " * indent_padding_len
        bullet = Segment("• ")
        new_line = Segment("\n")
        bullet_width = len(bullet.text)
        for first, line in loop_first(lines):
            if first:
                if indent_text:
                    yield Segment(indent_text)
                yield bullet
            else:
                plain = "".join(segment.text for segment in line)
                if self._line_starts_with_list_marker(plain):
                    prefix = ""
                else:
                    existing = len(plain) - len(plain.lstrip(" "))
                    target = indent_padding_len + bullet_width
                    missing = max(0, target - existing)
                    prefix = " " * missing
                if prefix:
                    yield Segment(prefix)
            yield from line
            yield new_line

    def render_number(
        self, console: Console, options: ConsoleOptions, number: int, last_number: int
    ) -> RenderResult:
        lines = console.render_lines(self.elements, options, style=self.style)
        new_line = Segment("\n")
        indent_padding_len = LIST_INDENT_WIDTH * self.indent
        indent_text = " " * indent_padding_len
        numeral_text = f"{number}. "
        numeral = Segment(numeral_text)
        numeral_width = len(numeral_text)
        for first, line in loop_first(lines):
            if first:
                if indent_text:
                    yield Segment(indent_text)
                yield numeral
            else:
                plain = "".join(segment.text for segment in line)
                if self._line_starts_with_list_marker(plain):
                    prefix = ""
                else:
                    existing = len(plain) - len(plain.lstrip(" "))
                    target = indent_padding_len + numeral_width
                    missing = max(0, target - existing)
                    prefix = " " * missing
                if prefix:
                    yield Segment(prefix)
            yield from line
            yield new_line


class Link(TextElement):
    """链接元素。

    用于渲染 Markdown 链接。

    Attributes:
        text: 链接显示文本。
        href: 链接目标 URL。
    """

    @classmethod
    def create(cls, markdown: Markdown, token: Token) -> MarkdownElement:
        url = token.attrs.get("href", "#")
        return cls(token.content, str(url))

    def __init__(self, text: str, href: str):
        self.text = Text(text)
        self.href = href


class ImageItem(TextElement):
    """图片元素。

    用于渲染图片占位符。

    Attributes:
        new_line: 是否在元素后添加换行。
        destination: 图片 URL。
        hyperlinks: 是否启用超链接。
        link: 链接样式中的 URL。
    """

    new_line = False

    @classmethod
    def create(cls, markdown: Markdown, token: Token) -> MarkdownElement:
        """创建 Markdown 元素的工厂方法。

        Args:
            markdown: 父级 Markdown 对象。
            token: markdown-it 解析器产生的标记。

        Returns:
            新创建的 MarkdownElement 实例。
        """
        return cls(str(token.attrs.get("src", "")), markdown.hyperlinks)

    def __init__(self, destination: str, hyperlinks: bool) -> None:
        self.destination = destination
        self.hyperlinks = hyperlinks
        self.link: str | None = None
        super().__init__()

    def on_enter(self, context: MarkdownContext) -> None:
        self.link = context.current_style.link
        self.text = Text(justify="left")
        super().on_enter(context)

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        link_style = Style(link=self.link or self.destination or None)
        title = self.text or Text(self.destination.strip("/").rsplit("/", 1)[-1])
        if self.hyperlinks:
            title.stylize(link_style)
        text = Text.assemble("🌆 ", title, " ", end="")
        yield text


class MarkdownContext:
    """管理控制台渲染状态的上下文类。

    用于跟踪渲染过程中的样式栈、元素栈等状态信息。

    Attributes:
        console: 控制台实例。
        options: 控制台渲染选项。
        style_stack: 样式栈，用于管理嵌套样式。
        stack: Markdown 元素栈。
        _fallback_styles: 备用样式映射表。
        _syntax: 用于内联代码高亮的 Syntax 实例。
    """

    def __init__(
        self,
        console: Console,
        options: ConsoleOptions,
        style: Style,
        fallback_styles: Mapping[str, Style],
        inline_code_lexer: str | None = None,
        inline_code_theme: str | SyntaxTheme = NOVEL_ANSI_THEME_NAME,
    ) -> None:
        self.console = console
        self.options = options
        self.style_stack: StyleStack = StyleStack(style)
        self.stack: Stack[MarkdownElement] = Stack()
        self._fallback_styles = fallback_styles

        self._syntax: Syntax | None = None
        if inline_code_lexer is not None:
            self._syntax = Syntax("", inline_code_lexer, theme=inline_code_theme)

    @property
    def current_style(self) -> Style:
        """当前样式，由样式栈上所有样式组合而成。"""
        return self.style_stack.current

    def on_text(self, text: str, node_type: str) -> None:
        """当解析器访问文本时调用。

        Args:
            text: 解析得到的文本内容。
            node_type: 节点类型。
        """
        if node_type in {"fence", "code_inline"} and self._syntax is not None:
            highlighted = self._syntax.highlight(text)
            highlighted.rstrip()
            stripped = _strip_background(highlighted)
            combined = Text.assemble(stripped, style=self.style_stack.current)
            self.stack.top.on_text(self, combined)
        else:
            self.stack.top.on_text(self, text)

    def enter_style(self, style_name: str | Style) -> Style:
        """进入样式上下文。

        Args:
            style_name: 样式名称或 Style 对象。

        Returns:
            进入样式上下文后的当前样式。
        """
        if isinstance(style_name, Style):
            style = style_name
        else:
            fallback = self._fallback_styles.get(style_name, Style())
            style = self.console.get_style(style_name, default=fallback)
            style = fallback + style
        style = style.copy()
        if isinstance(style_name, str) and style_name == "markdown.block_quote":
            style = style.without_color
        if (
            isinstance(style_name, str)
            and style_name in {"markdown.code", "markdown.code_block"}
            and style._bgcolor is not None
        ):
            style._bgcolor = None
        self.style_stack.push(style)
        return self.current_style

    def leave_style(self) -> Style:
        """离开样式上下文。

        Returns:
            离开样式上下文前的样式。
        """
        style = self.style_stack.pop()
        return style


class Markdown(JupyterMixin):
    """Markdown 渲染对象。

    用于将 Markdown 文本渲染到控制台。

    Args:
        markup: 包含 Markdown 内容的字符串。
        code_theme: 代码块的 Pygments 主题。默认为 "novel-ansi"。
            可参考 https://pygments.org/styles/ 查看可用主题。
        justify: 段落的对齐方式。默认为 None。
        style: 应用到 Markdown 的可选样式。
        hyperlinks: 是否启用超链接。默认为 True。
        inline_code_lexer: 内联代码高亮使用的语法分析器。默认为 None。
        inline_code_theme: 内联代码高亮的 Pygments 主题，None 表示不高亮。默认为 None。

    Attributes:
        elements: Markdown 标记类型到元素类的映射字典。
        inlines: 内联样式标记集合。
        markup: Markdown 源文本。
        parsed: 解析后的标记列表。
        code_theme: 代码主题。
        justify: 对齐方式。
        style: 样式。
        hyperlinks: 是否启用超链接。
        inline_code_lexer: 内联代码语法分析器。
        inline_code_theme: 内联代码主题。
    """

    elements: ClassVar[dict[str, type[MarkdownElement]]] = {
        "paragraph_open": Paragraph,
        "heading_open": Heading,
        "fence": CodeBlock,
        "code_block": CodeBlock,
        "blockquote_open": BlockQuote,
        "hr": HorizontalRule,
        "bullet_list_open": ListElement,
        "ordered_list_open": ListElement,
        "list_item_open": ListItem,
        "image": ImageItem,
        "table_open": TableElement,
        "tbody_open": TableBodyElement,
        "thead_open": TableHeaderElement,
        "tr_open": TableRowElement,
        "td_open": TableDataElement,
        "th_open": TableDataElement,
    }

    inlines = {"em", "strong", "code", "s"}

    def __init__(
        self,
        markup: str,
        code_theme: str = NOVEL_ANSI_THEME_NAME,
        justify: JustifyMethod | None = None,
        style: str | Style = "none",
        hyperlinks: bool = True,
        inline_code_lexer: str | None = None,
        inline_code_theme: str | None = None,
    ) -> None:
        parser = MarkdownIt().enable("strikethrough").enable("table")
        self.markup = markup
        self.parsed = parser.parse(markup)
        self.code_theme = resolve_code_theme(code_theme)
        self.justify: JustifyMethod | None = justify
        self.style = style
        self.hyperlinks = hyperlinks
        self.inline_code_lexer = inline_code_lexer
        self.inline_code_theme = resolve_code_theme(inline_code_theme or code_theme)

    def _flatten_tokens(self, tokens: Iterable[Token]) -> Iterable[Token]:
        """扁平化标记流。

        Args:
            tokens: 原始标记流。

        Returns:
            扁平化后的标记流。
        """
        for token in tokens:
            is_fence = token.type == "fence"
            is_image = token.tag == "img"
            if token.children and not (is_image or is_fence):
                yield from self._flatten_tokens(token.children)
            else:
                yield token

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        """将 Markdown 渲染到控制台。

        Args:
            console: 控制台实例。
            options: 控制台渲染选项。

        Returns:
            渲染结果。
        """
        style = console.get_style(self.style, default="none")
        options = options.update(height=None)
        context = MarkdownContext(
            console,
            options,
            style,
            _FALLBACK_STYLES,
            inline_code_lexer=self.inline_code_lexer,
            inline_code_theme=self.inline_code_theme,
        )
        tokens = self.parsed
        inline_style_tags = self.inlines
        new_line = False
        _new_line_segment = Segment.line()
        render_started = False

        for token in self._flatten_tokens(tokens):
            node_type = token.type
            tag = token.tag

            entering = token.nesting == 1
            exiting = token.nesting == -1
            self_closing = token.nesting == 0

            if node_type in {"text", "html_inline", "html_block"}:
                # 将 HTML 标记渲染为纯文本，以保持 safeword 标记可见
                if context.stack:
                    context.on_text(token.content, node_type)
                else:
                    # 孤立的文本/HTML 块可能出现在任何元素之外（例如 <analysis>）
                    paragraph = Paragraph(justify=self.justify or "left")
                    paragraph.on_enter(context)
                    paragraph.on_text(context, token.content)
                    paragraph.on_leave(context)
                    if new_line and render_started:
                        yield _new_line_segment
                    rendered = console.render(paragraph, context.options)
                    for segment in rendered:
                        render_started = True
                        yield segment
                    new_line = paragraph.new_line
            elif node_type == "hardbreak":
                context.on_text("\n", node_type)
            elif node_type == "softbreak":
                context.on_text(" ", node_type)
            elif node_type == "link_open":
                href = str(token.attrs.get("href", ""))
                if self.hyperlinks:
                    link_style = console.get_style("markdown.link_url", default="none")
                    link_style += Style(link=href)
                    context.enter_style(link_style)
                else:
                    context.stack.push(Link.create(self, token))
            elif node_type == "link_close":
                if self.hyperlinks:
                    context.leave_style()
                else:
                    element = context.stack.pop()
                    assert isinstance(element, Link)
                    link_style = console.get_style("markdown.link", default="none")
                    context.enter_style(link_style)
                    context.on_text(element.text.plain, node_type)
                    context.leave_style()
                    context.on_text(" (", node_type)
                    link_url_style = console.get_style("markdown.link_url", default="none")
                    context.enter_style(link_url_style)
                    context.on_text(element.href, node_type)
                    context.leave_style()
                    context.on_text(")", node_type)
            elif tag in inline_style_tags and node_type != "fence" and node_type != "code_block":
                if entering:
                    # 如果是开内联标记（如 strong, em 等），则进入样式上下文，即压入栈
                    context.enter_style(f"markdown.{tag}")
                elif exiting:
                    # 如果是闭内联样式，则从栈中弹出样式，离开该样式上下文
                    context.leave_style()
                else:
                    # 如果是自闭合内联样式（如 `code_inline`）
                    context.enter_style(f"markdown.{tag}")
                    if token.content:
                        context.on_text(token.content, node_type)
                    context.leave_style()
            else:
                # 将 markdown 标记映射到 MarkdownElement 渲染对象
                element_class = self.elements.get(token.type) or UnknownElement
                element = element_class.create(self, token)

                if entering or self_closing:
                    context.stack.push(element)
                    element.on_enter(context)

                if exiting:  # 关闭标记
                    element = context.stack.pop()

                    should_render = not context.stack or (
                        context.stack and context.stack.top.on_child_close(context, element)
                    )

                    if should_render:
                        if new_line and render_started:
                            yield _new_line_segment

                        rendered = console.render(element, context.options)
                        for segment in rendered:
                            render_started = True
                            yield segment
                elif self_closing:  # 自闭合标记（如 text, code, image）
                    context.stack.pop()
                    text = token.content
                    if text is not None:
                        element.on_text(context, text)

                    should_render = (
                        not context.stack
                        or context.stack
                        and context.stack.top.on_child_close(context, element)
                    )
                    if should_render:
                        if new_line and node_type != "inline" and render_started:
                            yield _new_line_segment
                        rendered = console.render(element, context.options)
                        for segment in rendered:
                            render_started = True
                            yield segment

                if exiting or self_closing:
                    element.on_leave(context)
                    new_line = element.new_line


if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Render Markdown to the console with Rich")
    parser.add_argument(
        "path",
        metavar="PATH",
        help="path to markdown file, or - for stdin",
    )
    parser.add_argument(
        "-c",
        "--force-color",
        dest="force_color",
        action="store_true",
        default=None,
        help="force color for non-terminals",
    )
    parser.add_argument(
        "-t",
        "--code-theme",
        dest="code_theme",
        default=NOVEL_ANSI_THEME_NAME,
        help='code theme (pygments name or "novel-ansi")',
    )
    parser.add_argument(
        "-i",
        "--inline-code-lexer",
        dest="inline_code_lexer",
        default=None,
        help="inline_code_lexer",
    )
    parser.add_argument(
        "-y",
        "--hyperlinks",
        dest="hyperlinks",
        action="store_true",
        help="enable hyperlinks",
    )
    parser.add_argument(
        "-w",
        "--width",
        type=int,
        dest="width",
        default=None,
        help="width of output (default will auto-detect)",
    )
    parser.add_argument(
        "-j",
        "--justify",
        dest="justify",
        action="store_true",
        help="enable full text justify",
    )
    parser.add_argument(
        "-p",
        "--page",
        dest="page",
        action="store_true",
        help="use pager to scroll output",
    )
    args = parser.parse_args()

    from rich.console import Console

    if args.path == "-":
        markdown_body = sys.stdin.read()
    else:
        with open(args.path, encoding="utf-8") as markdown_file:
            markdown_body = markdown_file.read()

    markdown = Markdown(
        markdown_body,
        justify="full" if args.justify else "left",
        code_theme=args.code_theme,
        hyperlinks=args.hyperlinks,
        inline_code_lexer=args.inline_code_lexer,
    )
    if args.page:
        import io
        import pydoc

        fileio = io.StringIO()
        console = Console(file=fileio, force_terminal=args.force_color, width=args.width)
        console.print(markdown)
        pydoc.pager(fileio.getvalue())

    else:
        console = Console(force_terminal=args.force_color, width=args.width, record=True)
        console.print(markdown)
