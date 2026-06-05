"""
novel_eval 模块：Novel Agent 工具使用评估体系。

该模块提供独立的评估框架，用于评估 agent 对 SearchNovel 工具的使用能力。
主要功能包括：
- 测试用例生成
- CLI 子进程运行器
- LLM Judge 评分
- 评估报告生成

模块结构：
- cli: CLI 命令行接口
- config: 配置加载
- models: 通用数据模型
- runner: 运行器（CLI 子进程驱动 + wire.jsonl 解析）
- report: 报告生成
- tasks: 可插拔的评估任务类型
"""

from novel_eval import cli
from novel_eval import config
from novel_eval import models
from novel_eval import runner
from novel_eval import report
from novel_eval import tasks

__all__ = [
    "cli",
    "config",
    "models",
    "runner",
    "report",
    "tasks",
]