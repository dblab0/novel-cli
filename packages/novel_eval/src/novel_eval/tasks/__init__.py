"""
评估任务模块。

提供可插拔的评估任务类型，每个任务实现 BaseTask 抽象基类。

当前支持的任务：
- tool_usage: 工具使用评估
- skill_generation: 技能生成评估

任务扩展：
在 tasks/ 目录下新建子目录，实现 BaseTask 的三个抽象方法即可：
- generate_cases: 生成测试用例
- run_case: 执行单个测试用例
- judge_case: 评估单个用例结果
"""

from novel_eval.tasks.base import BaseTask
from novel_eval.tasks.registry import get_task_cls, list_tasks, register_task

__all__ = ["BaseTask", "get_task_cls", "list_tasks", "register_task"]