"""LoopDetector 核心逻辑测试。

测试循环检测器的基本触发、穿插重置、参数不同不触发、自定义阈值、reset 清零、参数顺序不变等场景。
"""

import pytest

from novel_cli.soul.toolset import LoopDetector, ToolCallLoopDetected


@pytest.fixture
def loop_detector() -> LoopDetector:
    """创建默认阈值（5）的循环检测器。"""
    return LoopDetector(threshold=5)


def test_consecutive_identical_calls_raises(loop_detector: LoopDetector) -> None:
    """连续 N 次相同调用，第 N 次抛出异常。"""
    for _ in range(4):
        loop_detector.check("read_file", {"path": "/tmp/a.txt"})
    # 第 5 次应触发
    with pytest.raises(ToolCallLoopDetected) as exc_info:
        loop_detector.check("read_file", {"path": "/tmp/a.txt"})
    assert exc_info.value.tool_name == "read_file"
    assert exc_info.value.threshold == 5


def test_mixed_calls_resets_counter(loop_detector: LoopDetector) -> None:
    """中间插入不同调用，计数重置。"""
    for _ in range(4):
        loop_detector.check("read_file", {"path": "/tmp/a.txt"})
    # 插入不同调用，计数重置
    loop_detector.check("write_file", {"path": "/tmp/b.txt", "content": "hi"})
    # 再调 4 次 read_file 不应触发
    for _ in range(4):
        loop_detector.check("read_file", {"path": "/tmp/a.txt"})


def test_same_tool_different_params_no_trigger(loop_detector: LoopDetector) -> None:
    """参数不同不算重复。"""
    for i in range(10):
        loop_detector.check("write_file", {"path": f"/tmp/{i}.txt", "content": "hi"})


def test_custom_threshold() -> None:
    """自定义阈值 3。"""
    detector = LoopDetector(threshold=3)
    for _ in range(2):
        detector.check("read_file", {"path": "/tmp/a.txt"})
    with pytest.raises(ToolCallLoopDetected):
        detector.check("read_file", {"path": "/tmp/a.txt"})


def test_reset_clears_history(loop_detector: LoopDetector) -> None:
    """reset 后计数清零。"""
    for _ in range(4):
        loop_detector.check("read_file", {"path": "/tmp/a.txt"})
    loop_detector.reset()
    # reset 后再调 4 次不应触发
    for _ in range(4):
        loop_detector.check("read_file", {"path": "/tmp/a.txt"})


def test_param_key_order_invariant(loop_detector: LoopDetector) -> None:
    """参数 key 顺序不影响匹配（sort_keys 保证）。"""
    params_a = {"path": "/tmp/a.txt", "encoding": "utf-8"}
    params_b = {"encoding": "utf-8", "path": "/tmp/a.txt"}
    for _ in range(4):
        loop_detector.check("read_file", params_a)
    # 参数值相同但 key 顺序不同，应视为同一调用
    with pytest.raises(ToolCallLoopDetected):
        loop_detector.check("read_file", params_b)
