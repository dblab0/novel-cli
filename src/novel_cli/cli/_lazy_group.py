# pyright: reportAttributeAccessIssue=false, reportMissingParameterType=false, reportPrivateImportUsage=false, reportPrivateUsage=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownVariableType=false, reportUntypedBaseClass=false
"""延迟加载子命令组模块。

实现 CLI 子命令的延迟加载，仅在子命令实际被调用时才加载。
"""

from __future__ import annotations

from importlib import import_module
from typing import Any, cast

import click
import typer
from click.core import HelpFormatter
from typer.main import get_command


class LazySubcommandGroup(typer.core.TyperGroup):
    """延迟加载子命令组。

    仅在子命令实际被调用时才加载其实现模块。

    Attributes:
        lazy_subcommands: 延迟加载的子命令定义字典，格式为
            {命令名: (模块名, 属性名, 帮助文本)}。
        lazy_command_order: 子命令显示顺序。
        _optional_value_options: 支持可选值的选项字典。
    """

    lazy_subcommands: dict[str, tuple[str, str, str]] = {
        "info": ("novel_cli.cli.info", "cli", "显示版本和协议信息。"),
        "export": ("novel_cli.cli.export", "cli", "导出会话数据。"),
        "mcp": ("novel_cli.cli.mcp", "cli", "管理 MCP 服务器配置。"),
        "plugin": ("novel_cli.cli.plugin", "cli", "管理插件。"),
        "vis": ("novel_cli.cli.vis", "cli", "运行 Novel Agent Tracing Visualizer。"),
        "web": ("novel_cli.cli.web", "cli", "运行 Novel CLI web 界面。"),
    }
    lazy_command_order: tuple[str, ...] = (
        "info",
        "export",
        "mcp",
        "plugin",
        "vis",
        "web",
    )

    # Click 选项支持可选值。当选项标志存在但后面没有参数时，
    # 解析器返回映射的 *flag_value* 而不是抛出 "requires an argument"。
    _optional_value_options: dict[str, str] = {
        "session_id": "",  # --session / --resume 无值 → 选择器模式
    }

    def make_context(
        self, info_name: str | None, args: list[str], parent: click.Context | None = None, **extra
    ) -> click.Context:
        """创建上下文对象。

        Args:
            info_name: 命令名称。
            args: 命令行参数列表。
            parent: 父上下文对象。
            **extra: 额外参数。

        Returns:
            创建的上下文对象。
        """
        for param in self.params:
            if isinstance(param, click.Option) and param.name in self._optional_value_options:
                param._flag_needs_value = True
                param.flag_value = self._optional_value_options[param.name]
        return super().make_context(info_name, args, parent=parent, **extra)

    def list_commands(self, ctx: click.Context) -> list[str]:
        """列出所有命令名称。

        Args:
            ctx: Click 上下文对象。

        Returns:
            命令名称列表，按 lazy_command_order 排序。
        """
        commands = list(super().list_commands(ctx))
        for name in self.lazy_command_order:
            if name not in commands:
                commands.append(name)
        return commands

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        """获取命令对象。

        首先检查已加载的命令，然后尝试延迟加载。

        Args:
            ctx: Click 上下文对象。
            cmd_name: 命令名称。

        Returns:
            命令对象，如果未找到则返回 None。
        """
        command = super().get_command(ctx, cmd_name)
        if command is not None:
            return command

        lazy_spec = self.lazy_subcommands.get(cmd_name)
        if lazy_spec is None:
            return None

        module_name, attribute_name, _ = lazy_spec
        command = get_command(getattr(import_module(module_name), attribute_name))
        command.name = cmd_name
        self.commands[cmd_name] = command
        return command

    def format_help(self, ctx: click.Context, formatter: HelpFormatter) -> None:
        """格式化帮助信息（使用 Rich 样式）。

        Args:
            ctx: Click 上下文对象。
            formatter: 帮助格式化器。
        """
        if not typer.core.HAS_RICH or self.rich_markup_mode is None:
            return super().format_help(ctx, formatter)

        from typer import rich_utils

        rich_utils_any = cast(Any, rich_utils)
        console = rich_utils_any._get_rich_console()
        console.print(
            rich_utils_any.Padding(
                rich_utils_any.highlighter(self.get_usage(ctx)),
                1,
            ),
            style=rich_utils_any.STYLE_USAGE_COMMAND,
        )

        if self.help:
            console.print(
                rich_utils_any.Padding(
                    rich_utils_any.Align(
                        rich_utils_any._get_help_text(
                            obj=self,
                            markup_mode=self.rich_markup_mode,
                        ),
                        pad=False,
                    ),
                    (0, 1, 1, 1),
                )
            )

        panel_to_arguments: dict[str, list[click.Argument]] = {}
        panel_to_options: dict[str, list[click.Option]] = {}
        for param in self.get_params(ctx):
            if getattr(param, "hidden", False):
                continue
            if isinstance(param, click.Argument):
                panel_name = (
                    getattr(param, rich_utils_any._RICH_HELP_PANEL_NAME, None)
                    or rich_utils_any.ARGUMENTS_PANEL_TITLE
                )
                panel_to_arguments.setdefault(panel_name, []).append(param)
            elif isinstance(param, click.Option):
                panel_name = (
                    getattr(param, rich_utils_any._RICH_HELP_PANEL_NAME, None)
                    or rich_utils_any.OPTIONS_PANEL_TITLE
                )
                panel_to_options.setdefault(panel_name, []).append(param)

        default_arguments = panel_to_arguments.get(rich_utils_any.ARGUMENTS_PANEL_TITLE, [])
        rich_utils_any._print_options_panel(
            name=rich_utils_any.ARGUMENTS_PANEL_TITLE,
            params=default_arguments,
            ctx=ctx,
            markup_mode=self.rich_markup_mode,
            console=console,
        )
        for panel_name, arguments in panel_to_arguments.items():
            if panel_name == rich_utils_any.ARGUMENTS_PANEL_TITLE:
                continue
            rich_utils_any._print_options_panel(
                name=panel_name,
                params=arguments,
                ctx=ctx,
                markup_mode=self.rich_markup_mode,
                console=console,
            )

        default_options = panel_to_options.get(rich_utils_any.OPTIONS_PANEL_TITLE, [])
        rich_utils_any._print_options_panel(
            name=rich_utils_any.OPTIONS_PANEL_TITLE,
            params=default_options,
            ctx=ctx,
            markup_mode=self.rich_markup_mode,
            console=console,
        )
        for panel_name, options in panel_to_options.items():
            if panel_name == rich_utils_any.OPTIONS_PANEL_TITLE:
                continue
            rich_utils_any._print_options_panel(
                name=panel_name,
                params=options,
                ctx=ctx,
                markup_mode=self.rich_markup_mode,
                console=console,
            )

        panel_to_commands: dict[str, list[click.Command]] = {}
        for command_name in self.list_commands(ctx):
            command = self.commands.get(command_name)
            if command is None:
                lazy_spec = self.lazy_subcommands.get(command_name)
                if lazy_spec is None:
                    continue
                command = click.Command(command_name, help=lazy_spec[2])
            if command.hidden:
                continue
            panel_name = (
                getattr(command, rich_utils_any._RICH_HELP_PANEL_NAME, None)
                or rich_utils_any.COMMANDS_PANEL_TITLE
            )
            panel_to_commands.setdefault(panel_name, []).append(command)

        max_cmd_len = max(
            (
                len(command.name or "")
                for commands in panel_to_commands.values()
                for command in commands
            ),
            default=0,
        )
        default_commands = panel_to_commands.get(rich_utils_any.COMMANDS_PANEL_TITLE, [])
        rich_utils_any._print_commands_panel(
            name=rich_utils_any.COMMANDS_PANEL_TITLE,
            commands=default_commands,
            markup_mode=self.rich_markup_mode,
            console=console,
            cmd_len=max_cmd_len,
        )
        for panel_name, commands in panel_to_commands.items():
            if panel_name == rich_utils_any.COMMANDS_PANEL_TITLE:
                continue
            rich_utils_any._print_commands_panel(
                name=panel_name,
                commands=commands,
                markup_mode=self.rich_markup_mode,
                console=console,
                cmd_len=max_cmd_len,
            )

        if self.epilog:
            lines = self.epilog.split("\n\n")
            epilogue = "\n".join(x.replace("\n", " ").strip() for x in lines)
            epilogue_text = rich_utils_any._make_rich_text(
                text=epilogue,
                markup_mode=self.rich_markup_mode,
            )
            console.print(rich_utils_any.Padding(rich_utils_any.Align(epilogue_text, pad=False), 1))

    def format_commands(self, ctx: click.Context, formatter: HelpFormatter) -> None:
        """格式化命令列表。

        Args:
            ctx: Click 上下文对象。
            formatter: 帮助格式化器。
        """
        entries: list[tuple[str, str | None]] = []
        for subcommand in self.list_commands(ctx):
            command = self.commands.get(subcommand)
            if command is not None:
                if command.hidden:
                    continue
                entries.append((subcommand, None))
                continue

            lazy_spec = self.lazy_subcommands.get(subcommand)
            if lazy_spec is None:
                continue
            entries.append((subcommand, lazy_spec[2]))

        if not entries:
            return

        limit = formatter.width - 6 - max(len(name) for name, _ in entries)
        rows: list[tuple[str, str]] = []
        for subcommand, short_help in entries:
            command = self.commands.get(subcommand)
            if command is not None:
                rows.append((subcommand, command.get_short_help_str(limit)))
                continue
            rows.append((subcommand, short_help or ""))

        if rows:
            with formatter.section("Commands"):
                formatter.write_dl(rows)
