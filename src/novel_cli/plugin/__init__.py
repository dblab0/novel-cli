"""插件规范解析与配置注入模块。

提供 plugin.json 文件的解析、验证和主机配置注入功能。
"""

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PluginError(Exception):
    """插件操作异常，当 plugin.json 无效或操作失败时抛出。"""


class PluginRuntime(BaseModel):
    """插件运行时信息，由主机在安装后写入。

    Attributes:
        host: 主机名称。
        host_version: 主机版本。
    """

    host: str
    host_version: str


class PluginToolSpec(BaseModel):
    """插件声明的工具规范。

    Attributes:
        name: 工具名称。
        description: 工具描述。
        command: 执行命令列表。
        parameters: 参数定义字典。
    """

    name: str
    description: str
    command: list[str]
    parameters: dict[str, object] = Field(default_factory=dict)


class PluginSpec(BaseModel):
    """解析后的 plugin.json 文件表示。

    Attributes:
        name: 插件名称。
        version: 插件版本。
        description: 插件描述。
        config_file: 配置文件路径，可选。
        inject: 注入键映射，用于将主机值注入配置文件。
        tools: 工具声明列表。
        runtime: 运行时信息，可选。
    """

    model_config = ConfigDict(extra="ignore")

    name: str
    version: str
    description: str = ""
    config_file: str | None = None
    inject: dict[str, str] = Field(default_factory=dict)
    tools: list[PluginToolSpec] = Field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    runtime: PluginRuntime | None = None


PLUGIN_JSON = "plugin.json"


def parse_plugin_json(path: Path) -> PluginSpec:
    """解析 plugin.json 文件并返回验证后的 PluginSpec。

    Args:
        path: plugin.json 文件路径。

    Returns:
        验证后的 PluginSpec 对象。

    Raises:
        PluginError: 文件读取失败、JSON 解析失败或验证失败。
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PluginError(f"Failed to read {path}: {exc}") from exc

    if "name" not in data:
        raise PluginError(f"Missing required field 'name' in {path}")
    if "version" not in data:
        raise PluginError(f"Missing required field 'version' in {path}")
    if data.get("inject") and not data.get("config_file"):
        raise PluginError(f"'inject' requires 'config_file' in {path}")

    try:
        return PluginSpec.model_validate(data)
    except Exception as exc:
        raise PluginError(f"Invalid plugin.json schema in {path}: {exc}") from exc


def inject_config(plugin_dir: Path, spec: PluginSpec, values: dict[str, str]) -> None:
    """将主机值注入插件的配置文件。

    Args:
        plugin_dir: 已安装插件的根目录。
        spec: 解析后的插件规范。
        values: 标准注入键到实际值的映射，例如 {"api_key": "sk-xxx"}。

    Raises:
        PluginError: 配置文件路径无效或读取失败。
    """
    if not spec.inject or not spec.config_file:
        return

    config_path = (plugin_dir / spec.config_file).resolve()
    if not config_path.is_relative_to(plugin_dir.resolve()):
        raise PluginError(f"config_file escapes plugin directory: {spec.config_file}")
    if not config_path.exists():
        raise PluginError(f"Config file not found: {config_path}")

    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PluginError(f"Failed to read config file {config_path}: {exc}") from exc

    for target_path, source_key in spec.inject.items():
        if source_key not in values:
            raise PluginError(f"Host does not provide required inject key '{source_key}'")
        _set_nested(config, target_path, values[source_key])

    config_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def write_runtime(plugin_dir: Path, runtime: PluginRuntime) -> None:
    """将运行时信息写入 plugin.json。

    Args:
        plugin_dir: 插件目录路径。
        runtime: 运行时信息对象。

    Raises:
        PluginError: 文件读取或写入失败。
    """
    plugin_json_path = plugin_dir / PLUGIN_JSON
    try:
        data = json.loads(plugin_json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PluginError(f"Failed to read {plugin_json_path}: {exc}") from exc
    data["runtime"] = runtime.model_dump()
    plugin_json_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _set_nested(obj: dict[str, Any], dotted_path: str, value: object) -> None:
    """使用点分隔路径在嵌套字典中设置值。

    如果中间层级不存在，会自动创建字典。

    Args:
        obj: 目标字典。
        dotted_path: 点分隔的路径字符串。
        value: 要设置的值。
    """
    keys = dotted_path.split(".")
    for key in keys[:-1]:
        if key not in obj or not isinstance(obj[key], dict):
            obj[key] = {}
        obj = obj[key]
    obj[keys[-1]] = value
