"""Context 提取器测试。"""

import json
from pathlib import Path

import pytest

from novel_eval.context import ContextExtractor


@pytest.fixture
def sample_context_jsonl(tmp_path: Path) -> Path:
    """创建示例 context.jsonl 文件。"""
    context_path = tmp_path / "context.jsonl"
    messages = [
        {"role": "_system_prompt", "content": "You are a helpful assistant."},
        {"role": "_checkpoint", "id": 0},
        {"role": "user", "content": "韩立是谁？"},
        {"role": "_checkpoint", "id": 1},
        {"role": "_usage", "token_count": 1000},
        {"role": "assistant", "content": {"type": "think", "think": "用户问韩立是谁...", "encrypted": "abc123"}},
        {"role": "tool", "content": "<system>已找到实体：韩立</system>"},
        {"role": "_usage", "token_count": 2000},
        {"role": "assistant", "content": "韩立是《凡人修仙传》的主角。"},
    ]
    with open(context_path, "w", encoding="utf-8") as f:
        for msg in messages:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")
    return context_path


@pytest.fixture
def extractor(tmp_path: Path) -> ContextExtractor:
    """创建 ContextExtractor 实例。"""
    return ContextExtractor(tmp_path)


def test_load_and_filter(sample_context_jsonl: Path, extractor: ContextExtractor) -> None:
    """测试读取并过滤 context.jsonl。"""
    messages = extractor._load_and_filter(sample_context_jsonl)

    # 应该过滤掉 _checkpoint 和 _usage
    assert len(messages) == 5

    # 检查 role 转换
    assert messages[0]["role"] == "system"  # _system_prompt -> system
    assert messages[1]["role"] == "user"
    assert messages[2]["role"] == "assistant"
    assert messages[3]["role"] == "tool"
    assert messages[4]["role"] == "assistant"

    # 检查 assistant 的 encrypted 被移除
    assert "encrypted" not in messages[2]["content"]
    assert messages[2]["content"]["think"] == "用户问韩立是谁..."


def test_extract_single_case(sample_context_jsonl: Path, extractor: ContextExtractor, tmp_path: Path) -> None:
    """测试提取单个用例的上下文。"""
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    # 模拟 session 目录结构
    session_dir = tmp_path / "eval-L1-001"
    session_dir.mkdir()
    session_context = session_dir / "context.jsonl"
    session_context.write_text(sample_context_jsonl.read_text(encoding="utf-8"), encoding="utf-8")

    # 执行提取
    result = extractor.extract("L1-001", output_dir)

    assert result is True

    # 检查输出文件
    case_dir = output_dir / "L1-001"
    assert case_dir.exists()
    assert (case_dir / "messages.jsonl").exists()
    assert (case_dir / "messages.md").exists()


def test_extract_case_not_found(extractor: ContextExtractor, tmp_path: Path) -> None:
    """测试 context.jsonl 不存在的情况。"""
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    result = extractor.extract("L1-999", output_dir)

    assert result is False


def test_extract_batch(sample_context_jsonl: Path, extractor: ContextExtractor, tmp_path: Path) -> None:
    """测试批量提取。"""
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    # 创建两个 session
    for case_id in ["L1-001", "L1-002"]:
        session_dir = tmp_path / f"eval-{case_id}"
        session_dir.mkdir()
        session_context = session_dir / "context.jsonl"
        session_context.write_text(sample_context_jsonl.read_text(encoding="utf-8"), encoding="utf-8")

    # 批量提取
    results = extractor.extract_batch(["L1-001", "L1-002", "L1-999"], output_dir)

    assert results["L1-001"] is True
    assert results["L1-002"] is True
    assert results["L1-999"] is False

    # 检查输出文件
    assert (output_dir / "L1-001" / "messages.jsonl").exists()
    assert (output_dir / "L1-002" / "messages.jsonl").exists()
    assert not (output_dir / "L1-999").exists()


def test_jsonl_output_format(sample_context_jsonl: Path, extractor: ContextExtractor, tmp_path: Path) -> None:
    """测试 messages.jsonl 格式。"""
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    session_dir = tmp_path / "eval-L1-001"
    session_dir.mkdir()
    session_context = session_dir / "context.jsonl"
    session_context.write_text(sample_context_jsonl.read_text(encoding="utf-8"), encoding="utf-8")

    extractor.extract("L1-001", output_dir)

    jsonl_path = output_dir / "L1-001" / "messages.jsonl"
    with open(jsonl_path, encoding="utf-8") as f:
        lines = f.readlines()

    assert len(lines) == 5  # 过滤后剩余 5 条

    # 验证第一条是 system
    first_msg = json.loads(lines[0])
    assert first_msg["role"] == "system"
    assert first_msg["content"] == "You are a helpful assistant."


def test_markdown_output_format(sample_context_jsonl: Path, extractor: ContextExtractor, tmp_path: Path) -> None:
    """测试 messages.md 格式。"""
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    session_dir = tmp_path / "eval-L1-001"
    session_dir.mkdir()
    session_context = session_dir / "context.jsonl"
    session_context.write_text(sample_context_jsonl.read_text(encoding="utf-8"), encoding="utf-8")

    extractor.extract("L1-001", output_dir)

    md_path = output_dir / "L1-001" / "messages.md"
    md_content = md_path.read_text(encoding="utf-8")

    # 检查基本结构
    assert "# L1-001: Context Messages" in md_content
    assert "## [System]" in md_content
    assert "## [User]" in md_content
    assert "## [Assistant]" in md_content
    assert "## [Tool]" in md_content
    assert "**思考：**" in md_content


def test_markdown_list_content_with_think_and_text(tmp_path: Path) -> None:
    """测试 list 格式 content 同时包含 think 和 text block 时，markdown 正确输出两者。"""
    context_path = tmp_path / "context.jsonl"
    messages = [
        {"role": "user", "content": "虚天鼎是什么？"},
        {
            "role": "assistant",
            "content": [
                {"type": "think", "think": "让我想想...虚天鼎是通天灵宝。"},
                {"type": "text", "text": "虚天鼎是乱星海第一秘宝，属于通天灵宝。"},
            ],
        },
    ]
    with open(context_path, "w", encoding="utf-8") as f:
        for msg in messages:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")

    extractor = ContextExtractor(tmp_path)
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    session_dir = tmp_path / "eval-L1-010"
    session_dir.mkdir()
    session_context = session_dir / "context.jsonl"
    session_context.write_text(context_path.read_text(encoding="utf-8"), encoding="utf-8")

    extractor.extract("L1-010", output_dir)

    md_path = output_dir / "L1-010" / "messages.md"
    md_content = md_path.read_text(encoding="utf-8")

    # think 和 text 都应该出现在 markdown 中
    assert "**思考：**" in md_content
    assert "让我想想...虚天鼎是通天灵宝。" in md_content
    assert "虚天鼎是乱星海第一秘宝，属于通天灵宝。" in md_content
