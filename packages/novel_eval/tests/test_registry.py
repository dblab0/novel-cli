"""任务注册表测试。"""


def test_register_and_get_task() -> None:
    """装饰器正确注册任务类，get_task_cls 返回正确的类。"""
    from novel_eval.tasks.registry import get_task_cls
    from novel_eval.tasks.tool_usage import ToolUsageTask
    from novel_eval.tasks.skill_generation import SkillGenerationTask

    assert get_task_cls("tool_usage") is ToolUsageTask
    assert get_task_cls("skill_generation") is SkillGenerationTask


def test_get_unknown_task() -> None:
    """查询未知任务名抛出 ValueError。"""
    from novel_eval.tasks.registry import get_task_cls
    import pytest

    with pytest.raises(ValueError, match="未知任务类型"):
        get_task_cls("nonexistent")


def test_list_tasks() -> None:
    """list_tasks 返回所有已注册任务。"""
    from novel_eval.tasks.registry import list_tasks

    tasks = list_tasks()
    assert "tool_usage" in tasks
    assert "skill_generation" in tasks


def test_auto_discover() -> None:
    """模块加载后注册表非空。"""
    from novel_eval.tasks.registry import list_tasks

    assert len(list_tasks()) >= 2
