"""信息命令模块。

显示版本和协议信息。
"""

from __future__ import annotations

import json
import platform
from typing import Annotated, TypedDict

import typer


class InfoData(TypedDict):
    """信息数据字典类型。

    Attributes:
        novel_cli_version: Novel CLI 版本号。
        agent_spec_versions: 支持的 agent spec 版本列表。
        wire_protocol_version: Wire 协议版本号。
        python_version: Python 版本号。
    """

    novel_cli_version: str
    agent_spec_versions: list[str]
    wire_protocol_version: str
    python_version: str


def _collect_info() -> InfoData:
    """收集系统信息。

    Returns:
        包含版本和协议信息的字典。
    """
    from novel_cli.agentspec import SUPPORTED_AGENT_SPEC_VERSIONS
    from novel_cli.constant import get_version
    from novel_cli.wire.protocol import WIRE_PROTOCOL_VERSION

    return {
        "novel_cli_version": get_version(),
        "agent_spec_versions": [str(version) for version in SUPPORTED_AGENT_SPEC_VERSIONS],
        "wire_protocol_version": WIRE_PROTOCOL_VERSION,
        "python_version": platform.python_version(),
    }


def _emit_info(json_output: bool) -> None:
    """输出信息到终端。

    Args:
        json_output: 是否以 JSON 格式输出。
    """
    info = _collect_info()
    if json_output:
        typer.echo(json.dumps(info, ensure_ascii=False))
        return

    agent_versions_text = ", ".join(str(version) for version in info["agent_spec_versions"])

    lines = [
        f"novel-cli version: {info['novel_cli_version']}",
        f"agent spec versions: {agent_versions_text}",
        f"wire protocol: {info['wire_protocol_version']}",
        f"python version: {info['python_version']}",
    ]
    for line in lines:
        typer.echo(line)


cli = typer.Typer(help="显示版本和协议信息。")


@cli.callback(invoke_without_command=True)
def info(
    json_output: Annotated[
        bool,
        typer.Option(
            "--json",
            help="以 JSON 格式输出信息。",
        ),
    ] = False,
):
    """显示版本和协议信息。

    Args:
        json_output: 是否以 JSON 格式输出。
    """
    _emit_info(json_output)
