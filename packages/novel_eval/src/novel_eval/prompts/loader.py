"""提示词模板加载器。

基于 Jinja2 从 prompts/ 目录加载 .md.j2 模板文件并渲染，
支持变量注入、条件渲染和 system/user 双消息构建。
"""

from pathlib import Path

from jinja2 import FileSystemLoader, StrictUndefined
from jinja2 import Environment as JinjaEnvironment

# 默认模板目录：本包的 prompts/ 目录
_DEFAULT_PROMPTS_DIR = Path(__file__).parent


class PromptLoader:
    """提示词模板加载器。

    基于 Jinja2 Environment 从指定目录加载 .md.j2 模板文件，
    渲染变量后返回文本。支持构建 system/user 双消息列表。

    Attributes:
        _env: Jinja2 Environment 实例。
    """

    def __init__(self, prompts_dir: Path | None = None) -> None:
        """初始化 PromptLoader。

        Args:
            prompts_dir: 模板文件目录路径。默认为本包的 prompts/ 目录。
        """
        target_dir = str(prompts_dir or _DEFAULT_PROMPTS_DIR)
        self._env = JinjaEnvironment(
            loader=FileSystemLoader(target_dir),
            undefined=StrictUndefined,
            keep_trailing_newline=True,
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def render(self, template_name: str, **kwargs: object) -> str:
        """渲染指定模板。

        Args:
            template_name: 模板文件名，如 "judge_system.md.j2"。
            **kwargs: 注入模板的变量。

        Returns:
            渲染后的文本。
        """
        template = self._env.get_template(template_name)
        return template.render(**kwargs)

    def render_messages(
        self, task: str, **kwargs: object
    ) -> list[dict[str, str]]:
        """渲染 system + user 双消息。

        根据 task 名称自动加载 {task}_system.md.j2 和 {task}_user.md.j2
        模板文件，渲染后返回标准 OpenAI 消息格式列表。

        Args:
            task: 任务名，如 "judge" 或 "generator"。
            **kwargs: 注入模板的变量。

        Returns:
            包含 system 和 user 两条消息的列表。
        """
        system = self.render(f"{task}_system.md.j2", **kwargs)
        user = self.render(f"{task}_user.md.j2", **kwargs)
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
