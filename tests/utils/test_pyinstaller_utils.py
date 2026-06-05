from __future__ import annotations

import platform
import sys
from pathlib import Path

from inline_snapshot import snapshot


def test_pyinstaller_datas():
    from novel_cli.utils.pyinstaller import datas

    project_root = Path(__file__).parent.parent.parent
    python_version = f"{sys.version_info.major}.{sys.version_info.minor}"
    site_packages = f".venv/lib/python{python_version}/site-packages"
    rg_binary = "rg.exe" if platform.system() == "Windows" else "rg"
    has_rg_binary = (project_root / "src/novel_cli/deps/bin" / rg_binary).exists()
    datas = [
        (
            Path(path)
            .relative_to(project_root)
            .as_posix()
            .replace(".venv/Lib/site-packages", site_packages),
            Path(dst).as_posix(),
        )
        for path, dst in datas
    ]

    datas = [(p, d) for p, d in datas if "web/static" not in d and "vis/static" not in d]

    expected_datas = [
        (
            f"{site_packages}/dateparser/data/dateparser_tz_cache.pkl",
            "dateparser/data",
        ),
        (
            f"{site_packages}/fastmcp/../fastmcp-2.12.5.dist-info/INSTALLER",
            "fastmcp/../fastmcp-2.12.5.dist-info",
        ),
        (
            f"{site_packages}/fastmcp/../fastmcp-2.12.5.dist-info/METADATA",
            "fastmcp/../fastmcp-2.12.5.dist-info",
        ),
        (
            f"{site_packages}/fastmcp/../fastmcp-2.12.5.dist-info/RECORD",
            "fastmcp/../fastmcp-2.12.5.dist-info",
        ),
        (
            f"{site_packages}/fastmcp/../fastmcp-2.12.5.dist-info/REQUESTED",
            "fastmcp/../fastmcp-2.12.5.dist-info",
        ),
        (
            f"{site_packages}/fastmcp/../fastmcp-2.12.5.dist-info/WHEEL",
            "fastmcp/../fastmcp-2.12.5.dist-info",
        ),
        (
            f"{site_packages}/fastmcp/../fastmcp-2.12.5.dist-info/entry_points.txt",
            "fastmcp/../fastmcp-2.12.5.dist-info",
        ),
        (
            f"{site_packages}/fastmcp/../fastmcp-2.12.5.dist-info/licenses/LICENSE",
            "fastmcp/../fastmcp-2.12.5.dist-info/licenses",
        ),
        (
            "src/novel_cli/CHANGELOG.md",
            "novel_cli",
        ),
        ("src/novel_cli/agents/default/agent.yaml", "novel_cli/agents/default"),
        ("src/novel_cli/agents/default/system.md", "novel_cli/agents/default"),
        ("src/novel_cli/agents/default/novel-researcher.yaml", "novel_cli/agents/default"),
        ("src/novel_cli/agents/default/novel-researcher.md", "novel_cli/agents/default"),
        ("src/novel_cli/prompts/compact.md", "novel_cli/prompts"),
        ("src/novel_cli/prompts/init.md", "novel_cli/prompts"),
        (
            "src/novel_cli/skills/novel-cli-help/SKILL.md",
            "novel_cli/skills/novel-cli-help",
        ),
        (
            "src/novel_cli/skills/skill-creator/SKILL.md",
            "novel_cli/skills/skill-creator",
        ),
        (
            "src/novel_cli/skills/generate-character/SKILL.md",
            "novel_cli/skills/generate-character",
        ),
        (
            "src/novel_cli/skills/generate-item/SKILL.md",
            "novel_cli/skills/generate-item",
        ),
        (
            "src/novel_cli/skills/generate-location/SKILL.md",
            "novel_cli/skills/generate-location",
        ),
        (
            "src/novel_cli/skills/generate-organization/SKILL.md",
            "novel_cli/skills/generate-organization",
        ),
        (
            "src/novel_cli/skills/generate-skill/SKILL.md",
            "novel_cli/skills/generate-skill",
        ),
        ("src/novel_cli/tools/agent/description.md", "novel_cli/tools/agent"),
        ("src/novel_cli/tools/ask_user/description.md", "novel_cli/tools/ask_user"),
        (
            "src/novel_cli/tools/dmail/dmail.md",
            "novel_cli/tools/dmail",
        ),
        ("src/novel_cli/tools/background/list.md", "novel_cli/tools/background"),
        ("src/novel_cli/tools/background/output.md", "novel_cli/tools/background"),
        ("src/novel_cli/tools/background/stop.md", "novel_cli/tools/background"),
        (
            "src/novel_cli/tools/file/glob.md",
            "novel_cli/tools/file",
        ),
        (
            "src/novel_cli/tools/file/grep.md",
            "novel_cli/tools/file",
        ),
        (
            "src/novel_cli/tools/file/read.md",
            "novel_cli/tools/file",
        ),
        (
            "src/novel_cli/tools/file/read_media.md",
            "novel_cli/tools/file",
        ),
        (
            "src/novel_cli/tools/file/replace.md",
            "novel_cli/tools/file",
        ),
        (
            "src/novel_cli/tools/file/write.md",
            "novel_cli/tools/file",
        ),
        ("src/novel_cli/tools/plan/description.md", "novel_cli/tools/plan"),
        ("src/novel_cli/tools/plan/enter_description.md", "novel_cli/tools/plan"),
        ("src/novel_cli/tools/novel/corpus.md", "novel_cli/tools/novel"),
        ("src/novel_cli/tools/novel/corpus_zh.md", "novel_cli/tools/novel"),
        ("src/novel_cli/tools/novel/entity.md", "novel_cli/tools/novel"),
        ("src/novel_cli/tools/novel/entity_zh.md", "novel_cli/tools/novel"),
        ("src/novel_cli/tools/novel/graph.md", "novel_cli/tools/novel"),
        ("src/novel_cli/tools/novel/graph_zh.md", "novel_cli/tools/novel"),
        ("src/novel_cli/tools/novel/chapter.md", "novel_cli/tools/novel"),
        ("src/novel_cli/tools/novel/chapter_zh.md", "novel_cli/tools/novel"),
        ("src/novel_cli/tools/shell/bash.md", "novel_cli/tools/shell"),
        ("src/novel_cli/tools/shell/powershell.md", "novel_cli/tools/shell"),
        (
            "src/novel_cli/tools/think/think.md",
            "novel_cli/tools/think",
        ),
        (
            "src/novel_cli/tools/todo/set_todo_list.md",
            "novel_cli/tools/todo",
        ),
        (
            "src/novel_cli/tools/web/fetch.md",
            "novel_cli/tools/web",
        ),
        (
            "src/novel_cli/tools/web/search.md",
            "novel_cli/tools/web",
        ),
    ]
    if has_rg_binary:
        expected_datas.append((f"src/novel_cli/deps/bin/{rg_binary}", "novel_cli/deps/bin"))

    assert sorted(datas) == sorted(expected_datas)


def test_pyinstaller_hiddenimports():
    from novel_cli.utils.pyinstaller import hiddenimports

    assert sorted(hiddenimports) == snapshot(
        [
            "novel_cli.tools",
            "novel_cli.tools.agent",
            "novel_cli.tools.ask_user",
            "novel_cli.tools.background",
            "novel_cli.tools.display",
            "novel_cli.tools.dmail",
            "novel_cli.tools.file",
            "novel_cli.tools.file.glob",
            "novel_cli.tools.file.grep_local",
            "novel_cli.tools.file.plan_mode",
            "novel_cli.tools.file.read",
            "novel_cli.tools.file.read_media",
            "novel_cli.tools.file.replace",
            "novel_cli.tools.file.utils",
            "novel_cli.tools.file.write",
            "novel_cli.tools.novel",
            "novel_cli.tools.novel._base", "novel_cli.tools.novel.chapter", "novel_cli.tools.novel.corpus",
            "novel_cli.tools.novel.entity",
            "novel_cli.tools.novel.errors",
            "novel_cli.tools.novel.formatter",
            "novel_cli.tools.novel.graph",
            "novel_cli.tools.plan",
            "novel_cli.tools.plan.enter",
            "novel_cli.tools.plan.heroes",
            "novel_cli.tools.shell",
            "novel_cli.tools.test",
            "novel_cli.tools.think",
            "novel_cli.tools.todo",
            "novel_cli.tools.utils",
            "novel_cli.tools.web",
            "novel_cli.tools.web.fetch",
            "novel_cli.tools.web.search",
            "setproctitle",
        ]
    )
