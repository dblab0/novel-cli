"""插件管理命令模块。

提供插件的安装、列表、移除和信息查看功能。
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from novel_cli.plugin import PluginError

cli = typer.Typer(help="管理插件。")


def _parse_git_url(target: str) -> tuple[str, str | None, str | None]:
    """解析 git URL 为 (克隆地址, 子路径, 分支)。

    在 .git 边界处分割 .git URL。对于 GitHub/GitLab 短 URL，
    将前两个路径段视为 owner/repo，其余作为子路径。
    从浏览器复制的 URL 中剥离 ``tree/{branch}/`` 或 ``-/tree/{branch}/``
    前缀并返回分支名。

    Args:
        target: git URL 字符串。

    Returns:
        元组 (clone_url, subpath, branch)。
    """
    # 路径 1: URL 包含 .git 后跟 / 或字符串结尾
    idx = target.find(".git/")
    if idx == -1 and target.endswith(".git"):
        return target, None, None
    if idx != -1:
        clone_url = target[: idx + 4]  # 到 ".git"（包含）
        rest = target[idx + 5 :]  # ".git/" 之后
        subpath = rest.strip("/") or None
        return clone_url, subpath, None

    # 路径 2: GitHub/GitLab 短 URL（无 .git）
    from urllib.parse import urlparse

    parsed = urlparse(target)
    segments = [s for s in parsed.path.split("/") if s]
    if len(segments) < 2:
        return target, None, None

    owner_repo = "/".join(segments[:2])
    clone_url = f"{parsed.scheme}://{parsed.netloc}/{owner_repo}"
    rest_segments = segments[2:]

    # GitLab 使用 /-/tree/{branch}/，去除前导 "-"
    if rest_segments and rest_segments[0] == "-":
        rest_segments = rest_segments[1:]

    # 剥离 tree/{branch}/ 前缀并提取分支
    branch: str | None = None
    if len(rest_segments) >= 2 and rest_segments[0] == "tree":
        branch = rest_segments[1]
        rest_segments = rest_segments[2:]

    subpath = "/".join(rest_segments) or None
    return clone_url, subpath, branch


def _resolve_source(target: str) -> tuple[Path, Path | None]:
    """解析插件源为 (本地目录, 临时目录)。

    返回源目录和可选的临时目录，临时目录需要调用者在使用后清理。

    Args:
        target: 插件源路径（目录、.zip 或 git URL）。

    Returns:
        元组 (local_dir, tmp_to_cleanup)，tmp_to_cleanup 为 None 表示无需清理。

    Raises:
        typer.Exit: 当源路径无效或解析失败时。
    """
    import shutil
    import tempfile

    # Git URL
    if target.startswith(("https://", "git@", "http://")) and (
        ".git/" in target
        or target.endswith(".git")
        or "github.com/" in target
        or "gitlab.com/" in target
    ):
        import subprocess

        clone_url, subpath, branch = _parse_git_url(target)

        tmp = Path(tempfile.mkdtemp(prefix="novel-plugin-"))
        typer.echo(f"Cloning {clone_url}...")
        clone_cmd = ["git", "clone", "--depth", "1"]
        if branch:
            clone_cmd += ["--branch", branch]
        clone_cmd += [clone_url, str(tmp / "repo")]
        result = subprocess.run(
            clone_cmd,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            shutil.rmtree(tmp, ignore_errors=True)
            typer.echo(
                f"Error: git clone failed: {result.stderr.strip()}",
                err=True,
            )
            raise typer.Exit(1)

        repo_root = tmp / "repo"

        if subpath:
            source = (repo_root / subpath).resolve()
            if not source.is_relative_to(repo_root.resolve()):
                shutil.rmtree(tmp, ignore_errors=True)
                typer.echo(
                    f"Error: subpath escapes repository: {subpath}",
                    err=True,
                )
                raise typer.Exit(1)
            if not source.is_dir():
                shutil.rmtree(tmp, ignore_errors=True)
                typer.echo(
                    f"Error: subpath '{subpath}' not found in repository",
                    err=True,
                )
                raise typer.Exit(1)
            if not (source / "plugin.json").exists():
                shutil.rmtree(tmp, ignore_errors=True)
                typer.echo(
                    f"Error: no plugin.json in '{subpath}'",
                    err=True,
                )
                raise typer.Exit(1)
            return source, tmp

        # 无子路径 —— 先检查根目录
        if (repo_root / "plugin.json").exists():
            return repo_root, tmp

        # 扫描一层查找可用插件
        available = sorted(
            d.name for d in repo_root.iterdir() if d.is_dir() and (d / "plugin.json").exists()
        )
        if available:
            names = "\n".join(f"  - {n}" for n in available)
            typer.echo(
                f"Error: No plugin.json at repository root. "
                f"Available plugins:\n{names}\n"
                f"Use: novel plugin install <url>/<plugin-name>",
                err=True,
            )
        else:
            typer.echo(
                "Error: No plugin.json found in repository",
                err=True,
            )
        shutil.rmtree(tmp, ignore_errors=True)
        raise typer.Exit(1)

    p = Path(target).expanduser().resolve()

    # Zip 文件
    if p.is_file() and p.suffix == ".zip":
        import zipfile

        tmp = Path(tempfile.mkdtemp(prefix="novel-plugin-"))
        typer.echo(f"Extracting {p.name}...")
        with zipfile.ZipFile(p, "r") as zf:
            # 拒绝逃逸提取目录的 zip 成员
            for member in zf.namelist():
                member_path = (tmp / member).resolve()
                if not member_path.is_relative_to(tmp.resolve()):
                    shutil.rmtree(tmp, ignore_errors=True)
                    typer.echo(f"Error: zip contains unsafe path: {member}", err=True)
                    raise typer.Exit(1)
            zf.extractall(tmp)
        # 查找包含 plugin.json 的目录（可能嵌套一层）
        for candidate in [tmp] + sorted(tmp.iterdir()):
            if candidate.is_dir() and (candidate / "plugin.json").exists():
                return candidate, tmp
        # 检查 __MACOSX 和类似产物
        dirs = [d for d in tmp.iterdir() if d.is_dir() and not d.name.startswith("_")]
        if len(dirs) == 1 and (dirs[0] / "plugin.json").exists():
            return dirs[0], tmp
        shutil.rmtree(tmp, ignore_errors=True)
        typer.echo("Error: No plugin.json found in zip", err=True)
        raise typer.Exit(1)

    # 本地目录
    if p.is_dir():
        return p, None

    typer.echo(f"Error: {target} is not a directory, zip file, or git URL", err=True)
    raise typer.Exit(1)


@cli.command("install")
def install_cmd(
    target: Annotated[str, typer.Argument(help="插件源：目录、.zip 或 git URL")],
) -> None:
    """安装插件并注入宿主配置。

    Args:
        target: 插件源路径。

    Raises:
        typer.Exit: 当安装失败时。
    """
    import shutil

    from novel_cli.config import load_config
    from novel_cli.constant import VERSION
    from novel_cli.plugin.manager import get_plugins_dir, install_plugin

    source, tmp_dir = _resolve_source(target)

    try:
        config = load_config()

        from novel_cli.llm import augment_provider_with_env_vars
        from novel_cli.plugin.manager import collect_host_values

        # 应用环境变量覆盖（安装运行在正常启动之外）
        if config.default_model and config.default_model in config.models:
            model = config.models[config.default_model]
            if model.provider in config.providers:
                augment_provider_with_env_vars(config.providers[model.provider], model)

        host_values = collect_host_values(config)

        if not host_values.get("api_key"):
            typer.echo(
                "Warning: No LLM provider configured. "
                "Plugins requiring API key injection will fail. "
                "Configure a provider in config.toml first.",
                err=True,
            )

        spec = install_plugin(
            source=source,
            plugins_dir=get_plugins_dir(),
            host_values=host_values,
            host_name="kimi-code",
            host_version=VERSION,
        )
    except PluginError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    finally:
        # 清理 zip/git 提取的临时目录
        if tmp_dir is not None:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    typer.echo(f"Installed plugin '{spec.name}' v{spec.version}")
    if spec.runtime:
        typer.echo(f"  runtime: host={spec.runtime.host}, version={spec.runtime.host_version}")


@cli.command("list")
def list_cmd() -> None:
    """列出已安装的插件。"""
    from novel_cli.plugin.manager import get_plugins_dir, list_plugins

    plugins = list_plugins(get_plugins_dir())
    if not plugins:
        typer.echo("No plugins installed.")
        return

    for p in plugins:
        status = "installed" if p.runtime else "not configured"
        typer.echo(f"  {p.name} v{p.version} ({status})")


@cli.command("remove")
def remove_cmd(
    name: Annotated[str, typer.Argument(help="要移除的插件名称")],
) -> None:
    """移除已安装的插件。

    Args:
        name: 插件名称。

    Raises:
        typer.Exit: 当移除失败时。
    """
    from novel_cli.plugin.manager import get_plugins_dir, remove_plugin

    try:
        remove_plugin(name, get_plugins_dir())
    except PluginError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"Removed plugin '{name}'")


@cli.command("info")
def info_cmd(
    name: Annotated[str, typer.Argument(help="插件名称")],
) -> None:
    """显示插件详情。

    Args:
        name: 插件名称。

    Raises:
        typer.Exit: 当插件未找到或解析失败时。
    """
    from novel_cli.plugin import parse_plugin_json
    from novel_cli.plugin.manager import get_plugins_dir

    plugin_json = get_plugins_dir() / name / "plugin.json"
    if not plugin_json.exists():
        typer.echo(f"Error: Plugin '{name}' not found", err=True)
        raise typer.Exit(1)

    try:
        spec = parse_plugin_json(plugin_json)
    except PluginError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"Name:        {spec.name}")
    typer.echo(f"Version:     {spec.version}")
    typer.echo(f"Description: {spec.description or '(none)'}")
    typer.echo(f"Config file: {spec.config_file or '(none)'}")
    if spec.inject:
        typer.echo(f"Inject:      {', '.join(f'{k} <- {v}' for k, v in spec.inject.items())}")
    if spec.runtime:
        typer.echo(f"Runtime:     host={spec.runtime.host}, version={spec.runtime.host_version}")
    else:
        typer.echo("Runtime:     (not installed via host)")
