"""斜杠命令注册与解析模块。

提供斜杠命令的注册、查找和解析功能，支持命令别名和装饰器风格的命令定义。
"""

import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import overload


@dataclass(frozen=True, slots=True, kw_only=True)
class SlashCommand[F: Callable[..., None | Awaitable[None]]]:
    """斜杠命令数据类。

    Attributes:
        name: 命令主名称。
        description: 命令描述文本。
        func: 命令处理函数。
        aliases: 命令别名列表。
    """

    name: str
    description: str
    func: F
    aliases: list[str]

    def slash_name(self) -> str:
        """返回格式化的命令名称字符串。

        如果存在别名，格式为 "/name (alias1, alias2)"，
        否则返回 "/name"。

        Returns:
            格式化的命令名称字符串。
        """
        if self.aliases:
            return f"/{self.name} ({', '.join(self.aliases)})"
        return f"/{self.name}"


class SlashCommandRegistry[F: Callable[..., None | Awaitable[None]]]:
    """斜杠命令注册表。

    管理斜杠命令的注册、查找和列表功能。

    Attributes:
        _commands: 命令主名称到 SlashCommand 的映射。
        _command_aliases: 命令名称或别名到 SlashCommand 的映射。
    """

    def __init__(self) -> None:
        self._commands: dict[str, SlashCommand[F]] = {}
        """命令主名称 -> SlashCommand"""
        self._command_aliases: dict[str, SlashCommand[F]] = {}
        """命令名称或别名 -> SlashCommand"""

    @overload
    def command(self, func: F, /) -> F: ...

    @overload
    def command(
        self,
        *,
        name: str | None = None,
        aliases: Sequence[str] | None = None,
    ) -> Callable[[F], F]: ...

    def command(
        self,
        func: F | None = None,
        *,
        name: str | None = None,
        aliases: Sequence[str] | None = None,
    ) -> F | Callable[[F], F]:
        """注册斜杠命令的装饰器。

        支持可选的自定义名称和别名。

        使用示例:
            @registry.command
            def help(app: App, args: str): ...

            @registry.command(name="run")
            def start(app: App, args: str): ...

            @registry.command(aliases=["h", "?", "assist"])
            def help(app: App, args: str): ...

        Args:
            func: 被装饰的函数（无括号调用时）。
            name: 可选的自定义命令名称，默认使用函数名。
            aliases: 可选的命令别名列表。

        Returns:
            装饰后的函数或装饰器。
        """

        def _register(f: F) -> F:
            primary = name or f.__name__
            alias_list = list(aliases) if aliases else []

            # 创建带有别名的主命令
            cmd = SlashCommand[F](
                name=primary,
                description=(f.__doc__ or "").strip(),
                func=f,
                aliases=alias_list,
            )

            # 注册主命令
            self._commands[primary] = cmd
            self._command_aliases[primary] = cmd

            # 注册别名，指向同一命令
            for alias in alias_list:
                self._command_aliases[alias] = cmd

            return f

        if func is not None:
            return _register(func)
        return _register

    def find_command(self, name: str) -> SlashCommand[F] | None:
        """根据名称或别名查找命令。

        Args:
            name: 命令名称或别名。

        Returns:
            找到的 SlashCommand，如果不存在则返回 None。
        """
        return self._command_aliases.get(name)

    def list_commands(self) -> list[SlashCommand[F]]:
        """获取所有唯一的主命令列表（不包含别名重复）。

        Returns:
            SlashCommand 列表。
        """
        return list(self._commands.values())


@dataclass(frozen=True, slots=True, kw_only=True)
class SlashCommandCall:
    """斜杠命令调用数据。

    Attributes:
        name: 命令名称（不含斜杠前缀）。
        args: 原始参数字符串。
        raw_input: 原始用户输入字符串。
    """

    name: str
    args: str
    raw_input: str


def parse_slash_command_call(user_input: str) -> SlashCommandCall | None:
    """从用户输入中解析斜杠命令调用。

    Args:
        user_input: 用户输入的字符串。

    Returns:
        如果找到斜杠命令则返回 SlashCommandCall，否则返回 None。
        args 字段包含命令名称后的原始参数字符串。
    """
    user_input = user_input.strip()
    if not user_input or not user_input.startswith("/"):
        return None

    name_match = re.match(r"^\/([a-zA-Z0-9_-]+(?::[a-zA-Z0-9_-]+)*)", user_input)

    if not name_match:
        return None

    command_name = name_match.group(1)
    if len(user_input) > name_match.end() and not user_input[name_match.end()].isspace():
        return None
    raw_args = user_input[name_match.end() :].lstrip()
    return SlashCommandCall(name=command_name, args=raw_args, raw_input=user_input)