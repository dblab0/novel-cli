"""SessionState.current_book 字段测试。

测试 SessionState 中新增的 current_book 字段，
验证默认值、序列化和反序列化的正确性。
"""

from __future__ import annotations

from novel_cli.session_state import SessionState


def test_current_book_default() -> None:
    """测试 current_book 默认值为 None。

    验证新创建的 SessionState 实例中 current_book 字段默认为 None。
    """
    state = SessionState()
    assert state.current_book is None


def test_current_book_serialization() -> None:
    """测试 current_book 序列化。

    验证设置了 current_book 值后能正确序列化为 JSON。
    """
    state = SessionState(current_book="凡人修仙传")
    data = state.model_dump(mode="json")
    assert data["current_book"] == "凡人修仙传"


def test_current_book_deserialization() -> None:
    """测试 current_book 反序列化。

    验证 JSON 数据能正确反序列化为 SessionState 实例。
    """
    data = {"current_book": "凡人修仙传"}
    state = SessionState.model_validate(data)
    assert state.current_book == "凡人修仙传"


def test_current_book_none_serialization() -> None:
    """测试 current_book 为 None 时的序列化。

    验证 current_book 为 None 时序列化结果也为 None。
    """
    state = SessionState(current_book=None)
    data = state.model_dump(mode="json")
    assert data["current_book"] is None


def test_current_book_empty_string() -> None:
    """测试 current_book 为空字符串时的行为。

    验证空字符串与 None 的区分处理。
    """
    state = SessionState(current_book="")
    data = state.model_dump(mode="json")
    assert data["current_book"] == ""

    # 反序列化空字符串
    state2 = SessionState.model_validate({"current_book": ""})
    assert state2.current_book == ""


def test_current_book_roundtrip() -> None:
    """测试 current_book 的完整序列化/反序列化往返。

    验证序列化后再反序列化能恢复原始值。
    """
    original = SessionState(current_book="凡人修仙之仙界篇")
    data = original.model_dump(mode="json")
    restored = SessionState.model_validate(data)
    assert restored.current_book == "凡人修仙之仙界篇"


def test_current_book_persistence_with_other_fields() -> None:
    """测试 current_book 与其他字段共存时的序列化。

    验证 current_book 不影响其他字段的正常序列化。
    """
    from novel_cli.session_state import ApprovalStateData

    state = SessionState(
        current_book="凡人修仙传",
        approval=ApprovalStateData(yolo=True),
        custom_title="测试会话",
    )
    data = state.model_dump(mode="json")

    assert data["current_book"] == "凡人修仙传"
    assert data["approval"]["yolo"] is True
    assert data["custom_title"] == "测试会话"


def test_session_state_without_current_book_field() -> None:
    """测试旧数据无 current_book 字段时的兼容性。

    验证当 JSON 数据中不包含 current_book 字段时，
    反序列化能正常处理（使用默认值 None）。
    """
    # 模拟旧版本 state.json 的数据格式
    data = {
        "version": 1,
        "approval": {"yolo": False, "auto_approve_actions": []},
    }
    state = SessionState.model_validate(data)
    assert state.current_book is None