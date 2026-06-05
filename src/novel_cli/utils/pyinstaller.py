"""PyInstaller 打包钩子配置。

本模块定义 PyInstaller 打包时需要包含的隐藏导入和数据文件，
确保所有必要的模块和资源被正确打包。
"""

from __future__ import annotations

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# 隐藏导入：工具模块和进程标题设置库
hiddenimports = collect_submodules("novel_cli.tools") + ["setproctitle"]

# 数据文件：包含运行时需要的配置、模板和静态资源
datas = (
    collect_data_files(
        "novel_cli",
        includes=[
            "agents/**/*.yaml",
            "agents/**/*.md",
            "deps/bin/**",
            "prompts/**/*.md",
            "skills/**",
            "tools/**/*.md",
            "web/static/**",
            "vis/static/**",
            "CHANGELOG.md",
        ],
        excludes=[
            "tools/*.md",
        ],
    )
    + collect_data_files(
        "dateparser",
        includes=["**/*.pkl"],
    )
    + collect_data_files(
        "fastmcp",
        includes=["../fastmcp-*.dist-info/*"],
    )
)