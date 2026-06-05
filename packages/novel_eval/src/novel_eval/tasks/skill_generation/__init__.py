"""技能生成评估任务。

该模块提供技能生成评估任务的完整实现，包括：
- 用例生成：从 CSV 实体数据中抽取高频实体生成评估用例
- 用例加载：从 YAML 文件加载测试用例
- 用例执行：调用 Runner 子进程执行评估
- 评分判断：使用 5 维度 Judge LLM 评分

使用方式：
    from novel_eval.tasks.skill_generation import SkillGenerationTask

    task = SkillGenerationTask(config)
    cases = task.generate_cases(book="凡人修仙传")
"""

from novel_eval.tasks.skill_generation.task import SkillGenerationTask

__all__ = ["SkillGenerationTask"]
