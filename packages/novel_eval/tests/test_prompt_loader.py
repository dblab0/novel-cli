"""PromptLoader 单测。

测试模板加载、变量渲染、条件渲染和双消息构建。
"""

from pathlib import Path

import pytest
from jinja2 import UndefinedError

from novel_eval.prompts import PromptLoader


class TestPromptLoaderRender:
    """测试 PromptLoader.render 方法。"""

    def test_render_judge_system_template(self) -> None:
        """测试渲染 judge_system 模板。"""
        loader = PromptLoader()
        result = loader.render("judge_system.md.j2")

        # system message 应包含 JSON 格式约束
        assert "JSON" in result
        assert "tool_selection" in result
        assert "param_quality" in result
        assert "call_efficiency" in result
        assert "result_utilization" in result
        assert "commentary" in result

    def test_render_judge_user_template(self) -> None:
        """测试渲染 judge_user 模板并注入变量。"""
        loader = PromptLoader()
        result = loader.render(
            "judge_user.md.j2",
            tool_description="工具描述内容",
            question="韩立是谁？",
            tool_calls_str="步骤 1: action=entity",
            final_answer="韩立是男主角。",
        )

        assert "韩立是谁？" in result
        assert "工具描述内容" in result
        assert "步骤 1: action=entity" in result
        assert "韩立是男主角。" in result

    def test_render_generator_user_template(self) -> None:
        """测试渲染 generator_user 模板并注入变量。"""
        loader = PromptLoader()
        result = loader.render(
            "generator_user.md.j2",
            level=1,
            level_definition="Level 1 定义",
            settings_content="设定内容",
            level_examples="- 示例问题",
            instruction=None,
        )

        assert "Level 1" in result
        assert "Level 1 定义" in result
        assert "设定内容" in result
        assert "示例问题" in result

    def test_render_with_missing_variable_raises(self) -> None:
        """测试 StrictUndefined 模式：缺失变量时抛出 UndefinedError。"""
        loader = PromptLoader()

        with pytest.raises(UndefinedError):
            loader.render("judge_user.md.j2")
            # 缺少 tool_description, question, tool_calls_str, final_answer


class TestConditionalRender:
    """测试 Jinja2 条件渲染。"""

    def test_instruction_present(self) -> None:
        """测试 instruction 有值时渲染用户自定义指令段。"""
        loader = PromptLoader()
        result = loader.render(
            "generator_user.md.j2",
            level=1,
            level_definition="Level 1 定义",
            settings_content="设定内容",
            level_examples="示例",
            instruction="集中在人物关系",
        )

        assert "集中在人物关系" in result

    def test_instruction_absent(self) -> None:
        """测试 instruction 为 None 时不渲染用户自定义指令段。"""
        loader = PromptLoader()
        result = loader.render(
            "generator_user.md.j2",
            level=1,
            level_definition="Level 1 定义",
            settings_content="设定内容",
            level_examples="示例",
            instruction=None,
        )

        assert "自定义指令" not in result

    def test_instruction_empty_string(self) -> None:
        """测试 instruction 为空字符串时不渲染用户自定义指令段。"""
        loader = PromptLoader()
        result = loader.render(
            "generator_user.md.j2",
            level=1,
            level_definition="Level 1 定义",
            settings_content="设定内容",
            level_examples="示例",
            instruction="",
        )

        assert "自定义指令" not in result


class TestRenderMessages:
    """测试 PromptLoader.render_messages 方法。"""

    def test_render_judge_messages(self) -> None:
        """测试构建 Judge 双消息。"""
        loader = PromptLoader()
        messages = loader.render_messages(
            "judge",
            tool_description="工具描述",
            question="韩立是谁？",
            tool_calls_str="步骤 1: action=entity",
            final_answer="韩立是男主角。",
        )

        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        # system 消息应包含 JSON 格式约束
        assert "JSON" in messages[0]["content"]
        # user 消息应包含评估内容
        assert "韩立是谁？" in messages[1]["content"]

    def test_render_generator_messages(self) -> None:
        """测试构建 Generator 双消息。"""
        loader = PromptLoader()
        messages = loader.render_messages(
            "generator",
            level=2,
            level_definition="Level 2 定义",
            settings_content="设定内容",
            level_examples="示例",
            instruction=None,
        )

        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        # system 消息应包含 JSON 数组输出约束
        assert "JSON 数组" in messages[0]["content"]
        # user 消息应包含 Level 2
        assert "Level 2" in messages[1]["content"]

    def test_nonexistent_template_raises(self) -> None:
        """测试加载不存在的模板时抛出异常。"""
        loader = PromptLoader()

        with pytest.raises(Exception):
            loader.render("nonexistent.md.j2")
