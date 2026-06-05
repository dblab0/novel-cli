"""插件安装、卸载和列表管理模块。

提供插件的生命周期管理功能。
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from novel_cli.plugin import (
    PLUGIN_JSON,
    PluginError,
    PluginRuntime,
    PluginSpec,
    inject_config,
    parse_plugin_json,
    write_runtime,
)
from novel_cli.share import get_share_dir

if TYPE_CHECKING:
    from novel_cli.config import Config


def get_plugins_dir() -> Path:
    """返回插件安装目录路径（~/.novel/plugins/）。"""
    return get_share_dir() / "plugins"


def collect_host_values(config: Config) -> dict[str, str]:
    """收集用于插件注入的主机值（api_key、base_url）。

    从默认 provider 解析凭据，使用静态 API key。
    在正常启动流程之外运行（如 install_cmd）的调用者，
    应在调用此函数之前对 provider 应用环境变量覆盖
    （augment_provider_with_env_vars）；主应用启动已执行此操作。

    Args:
        config: 配置对象。

    Returns:
        主机值映射字典。
    """
    values: dict[str, str] = {}
    if not config.default_model or config.default_model not in config.models:
        return values
    model = config.models[config.default_model]
    if model.provider not in config.providers:
        return values
    provider = config.providers[model.provider]
    api_key = provider.api_key.get_secret_value()
    if api_key:
        values["api_key"] = api_key
    values["base_url"] = provider.base_url
    return values


def _validate_name(name: str, plugins_dir: Path) -> Path:
    """解析并验证插件名称，返回安全的目标路径。

    Args:
        name: 插件名称。
        plugins_dir: 插件安装目录。

    Returns:
        安全的目标路径。

    Raises:
        PluginError: 如果插件名称无效（路径逃逸）。
    """
    dest = (plugins_dir / name).resolve()
    if not dest.is_relative_to(plugins_dir.resolve()):
        raise PluginError(f"Invalid plugin name: {name}")
    return dest


def install_plugin(
    *,
    source: Path,
    plugins_dir: Path,
    host_values: dict[str, str],
    host_name: str,
    host_version: str,
) -> PluginSpec:
    """从源目录安装插件。

    首先将新副本暂存到临时目录，确保失败的升级不会破坏现有安装。

    Args:
        source: 源目录路径。
        plugins_dir: 插件安装目录。
        host_values: 主机值映射。
        host_name: 主机名称。
        host_version: 主机版本。

    Returns:
        安装后的 PluginSpec 对象。

    Raises:
        PluginError: 源目录缺少 plugin.json 或安装失败。
    """
    source_plugin_json = source / PLUGIN_JSON
    if not source_plugin_json.exists():
        raise PluginError(f"No plugin.json found in {source}")

    spec = parse_plugin_json(source_plugin_json)
    dest = _validate_name(spec.name, plugins_dir)

    # 在 plugins_dir 内创建临时目录，确保同文件系统上的重命名是原子操作
    plugins_dir.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{spec.name}-", dir=plugins_dir))
    try:
        # 复制源目录到暂存目录
        staging_plugin = staging / spec.name
        shutil.copytree(source, staging_plugin)

        # 对暂存副本应用注入和运行时信息
        inject_config(staging_plugin, spec, host_values)
        runtime = PluginRuntime(host=host_name, host_version=host_version)
        write_runtime(staging_plugin, runtime)

        # 交换：删除旧版本，移动暂存副本到位
        if dest.exists():
            shutil.rmtree(dest)
        staging_plugin.rename(dest)
    except Exception:
        # 失败时清理暂存目录，保留现有安装
        shutil.rmtree(staging, ignore_errors=True)
        raise
    finally:
        # 清理暂存目录外壳（成功重命名后可能为空）
        shutil.rmtree(staging, ignore_errors=True)

    # 重新读取以返回安装后的规范（包含运行时信息）
    return parse_plugin_json(dest / PLUGIN_JSON)


def refresh_plugin_configs(plugins_dir: Path, host_values: dict[str, str]) -> None:
    """重新注入主机值到所有已安装插件的配置文件。

    在启动时调用，确保凭据保持最新，即使初始安装后凭据已更新。

    Args:
        plugins_dir: 插件安装目录。
        host_values: 主机值映射。
    """
    if not plugins_dir.is_dir():
        return

    for child in sorted(plugins_dir.iterdir()):
        plugin_json = child / PLUGIN_JSON
        if not child.is_dir() or not plugin_json.is_file():
            continue
        try:
            spec = parse_plugin_json(plugin_json)
            if spec.inject and spec.config_file:
                inject_config(child, spec, host_values)
        except Exception:
            continue


def list_plugins(plugins_dir: Path) -> list[PluginSpec]:
    """列出所有已安装插件。

    Args:
        plugins_dir: 插件安装目录。

    Returns:
        PluginSpec 列表。
    """
    if not plugins_dir.is_dir():
        return []

    plugins: list[PluginSpec] = []
    for child in sorted(plugins_dir.iterdir()):
        plugin_json = child / PLUGIN_JSON
        if child.is_dir() and plugin_json.is_file():
            try:
                plugins.append(parse_plugin_json(plugin_json))
            except PluginError:
                continue
    return plugins


def remove_plugin(name: str, plugins_dir: Path) -> None:
    """卸载已安装插件。

    Args:
        name: 插件名称。
        plugins_dir: 插件安装目录。

    Raises:
        PluginError: 插件不存在。
    """
    dest = _validate_name(name, plugins_dir)
    if not dest.exists():
        raise PluginError(f"Plugin '{name}' not found in {plugins_dir}")
    shutil.rmtree(dest)
