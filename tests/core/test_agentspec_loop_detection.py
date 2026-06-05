"""LoopDetectionConfig 配置解析测试。

测试 LoopDetectionConfig 解析、ResolvedAgentSpec 传递、extend 继承等场景。
"""

import pytest

from novel_cli.agentspec import LoopDetectionConfig


def test_loop_detection_config_defaults() -> None:
    """默认阈值为 5。"""
    config = LoopDetectionConfig()
    assert config.threshold == 5


def test_loop_detection_config_custom_threshold() -> None:
    """自定义阈值 3。"""
    config = LoopDetectionConfig(threshold=3)
    assert config.threshold == 3


def test_loop_detection_config_minimum_threshold() -> None:
    """阈值最小值为 2。"""
    config = LoopDetectionConfig(threshold=2)
    assert config.threshold == 2


def test_loop_detection_config_rejects_threshold_below_minimum() -> None:
    """阈值小于 2 应抛出验证错误。"""
    with pytest.raises(Exception):
        LoopDetectionConfig(threshold=1)


def test_resolved_spec_carries_loop_detection(tmp_path) -> None:
    """ResolvedAgentSpec 传递 loop_detection 配置。"""
    agent_file = tmp_path / "agent.yaml"
    agent_file.write_text(
        """
version: "1"
agent:
  name: test
  system_prompt_path: system.md
  tools: []
  loop_detection:
    threshold: 3
"""
    )
    (tmp_path / "system.md").write_text("test system prompt")
    from novel_cli.agentspec import load_agent_spec

    resolved = load_agent_spec(agent_file)
    assert resolved.loop_detection is not None
    assert resolved.loop_detection.threshold == 3


def test_resolved_spec_no_loop_detection_defaults_to_none(tmp_path) -> None:
    """未配置 loop_detection 时，ResolvedAgentSpec.loop_detection 为 None。"""
    agent_file = tmp_path / "agent.yaml"
    agent_file.write_text(
        """
version: "1"
agent:
  name: test
  system_prompt_path: system.md
  tools: []
"""
    )
    (tmp_path / "system.md").write_text("test system prompt")
    from novel_cli.agentspec import load_agent_spec

    resolved = load_agent_spec(agent_file)
    assert resolved.loop_detection is None


def test_extend_inherits_loop_detection(tmp_path) -> None:
    """子 agent 未配置 loop_detection 时继承父 agent 的配置。"""
    # 创建父 agent
    parent_file = tmp_path / "parent.yaml"
    parent_file.write_text(
        """
version: "1"
agent:
  name: parent
  system_prompt_path: system.md
  tools: []
  loop_detection:
    threshold: 3
"""
    )
    # 创建子 agent（extend parent）
    child_file = tmp_path / "child.yaml"
    child_file.write_text(
        f"""
version: "1"
agent:
  name: child
  extend: {parent_file}
  system_prompt_path: system.md
  tools: []
"""
    )
    (tmp_path / "system.md").write_text("test system prompt")
    from novel_cli.agentspec import load_agent_spec

    resolved = load_agent_spec(child_file)
    assert resolved.loop_detection is not None
    assert resolved.loop_detection.threshold == 3


def test_extend_overrides_loop_detection(tmp_path) -> None:
    """子 agent 配置 loop_detection 时覆盖父 agent 的值。"""
    parent_file = tmp_path / "parent.yaml"
    parent_file.write_text(
        """
version: "1"
agent:
  name: parent
  system_prompt_path: system.md
  tools: []
  loop_detection:
    threshold: 3
"""
    )
    child_file = tmp_path / "child.yaml"
    child_file.write_text(
        f"""
version: "1"
agent:
  name: child
  extend: {parent_file}
  system_prompt_path: system.md
  tools: []
  loop_detection:
    threshold: 10
"""
    )
    (tmp_path / "system.md").write_text("test system prompt")
    from novel_cli.agentspec import load_agent_spec

    resolved = load_agent_spec(child_file)
    assert resolved.loop_detection is not None
    assert resolved.loop_detection.threshold == 10
