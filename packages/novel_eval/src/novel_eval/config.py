"""配置加载模块：从 YAML 加载配置，支持环境变量替换。"""

import os
from pathlib import Path

import yaml

from novel_eval.tasks.tool_usage.models import EvalConfig


def load_config(path: Path | str | None = None) -> EvalConfig:
    """加载 YAML 配置文件。

    Args:
        path: 配置文件路径，默认为模块内的 config.yaml

    Returns:
        EvalConfig 配置对象
    """
    # 自动加载 .env 文件（从项目根目录）
    _load_dotenv()

    if path is None:
        # 默认配置文件路径：项目根目录
        path = Path(__file__).parent.parent.parent.parent.parent / "eval_config.yaml"

    path = Path(path)
    if not path.exists():
        return EvalConfig()  # 返回默认配置

    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    # 环境变量替换：agent.api_key_env 字段
    if "agent" in data and "api_key_env" in data["agent"]:
        env_var = data["agent"]["api_key_env"]
        data["agent"]["api_key"] = os.environ.get(env_var, "")

    # 环境变量替换：judge.api_key_env 字段
    if "judge" in data and "api_key_env" in data["judge"]:
        env_var = data["judge"]["api_key_env"]
        data["judge"]["api_key"] = os.environ.get(env_var, "")

    return EvalConfig(**data)


def _load_dotenv() -> None:
    """加载项目根目录的 .env 文件。"""
    # 尝试多个可能的 .env 路径
    dotenv_paths = [
        Path.cwd() / ".env",
        Path(__file__).parent.parent.parent.parent.parent / ".env",  # novel-cli/.env
        Path(__file__).parent.parent.parent.parent / ".env",  # packages/../.env
    ]

    for dotenv_path in dotenv_paths:
        if dotenv_path.exists():
            with open(dotenv_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        key, value = line.split("=", 1)
                        key = key.strip()
                        value = value.strip()
                        # 只设置未存在的环境变量
                        if key not in os.environ:
                            os.environ[key] = value
            break