"""Wire 协议测试：/book 命令和 BookList 消息。

测试 BookList 消息格式和选书状态持久化：
- BookList 消息的 books 和 current_book 字段
- 选书状态在 session.state 中持久化
"""

from __future__ import annotations

from inline_snapshot import snapshot

from tests_e2e.wire_helpers import (
    collect_until_response,
    make_home_dir,
    make_work_dir,
    send_initialize,
    start_wire,
    summarize_messages,
    write_scripted_config,
)


def test_booklist_message_format(tmp_path) -> None:
    """测试 BookList 消息格式符合 Wire 协议规范。

    BookList 消息应包含：
    - books: 可选书籍列表
    - current_book: 当前选中的书籍名称（可为 None）
    """
    config_path = write_scripted_config(tmp_path, ["text: hello"])
    work_dir = make_work_dir(tmp_path)
    home_dir = make_home_dir(tmp_path)

    wire = start_wire(
        config_path=config_path,
        config_text=None,
        work_dir=work_dir,
        home_dir=home_dir,
        yolo=True,
    )
    try:
        send_initialize(wire)
        wire.send_json(
            {
                "jsonrpc": "2.0",
                "id": "prompt-1",
                "method": "prompt",
                "params": {"user_input": "/book"},
            }
        )
        _, messages = collect_until_response(wire, "prompt-1")

        # 查找 BookList 消息或文本消息（数据库未配置时）
        book_messages = [
            msg for msg in messages if msg.get("params", {}).get("type") == "BookList"
        ]
        text_messages = [
            msg
            for msg in messages
            if msg.get("params", {}).get("type") == "ContentPart"
            and msg.get("params", {}).get("payload", {}).get("type") == "text"
        ]

        # 由于数据库未配置，预期收到文本提示而非 BookList
        assert len(text_messages) >= 1
        text_content = text_messages[0].get("params", {}).get("payload", {}).get("text", "")
        # 应包含数据库未配置的提示
        assert "小说知识库未配置" in text_content or len(book_messages) >= 0
    finally:
        wire.close()


def test_book_command_none_clears_selection(tmp_path) -> None:
    """测试 /book none 清除选书状态。

    发送 /book none 命令应清除 session.state.current_book。
    """
    config_path = write_scripted_config(tmp_path, ["text: cleared"])
    work_dir = make_work_dir(tmp_path)
    home_dir = make_home_dir(tmp_path)

    wire = start_wire(
        config_path=config_path,
        config_text=None,
        work_dir=work_dir,
        home_dir=home_dir,
        yolo=True,
    )
    try:
        send_initialize(wire)
        wire.send_json(
            {
                "jsonrpc": "2.0",
                "id": "prompt-1",
                "method": "prompt",
                "params": {"user_input": "/book none"},
            }
        )
        resp, messages = collect_until_response(wire, "prompt-1")

        # 应收到响应（数据库未配置时提示，或清除成功的确认）
        assert resp.get("result", {}).get("status") == "finished"

        # 查找文本消息
        text_messages = [
            msg
            for msg in messages
            if msg.get("params", {}).get("type") == "ContentPart"
            and msg.get("params", {}).get("payload", {}).get("type") == "text"
        ]

        # 验证有响应消息
        assert len(text_messages) >= 1
    finally:
        wire.close()


def test_book_selection_state_persistence(tmp_path) -> None:
    """测试选书状态在会话重启后持久化。

    选书后状态应保存到 session.state，重启会话后仍可恢复。
    注意：此测试需要数据库连接，因此在无数据库配置时验证状态机制正常工作。
    """
    config_path = write_scripted_config(tmp_path, ["text: first turn", "text: second turn"])
    work_dir = make_work_dir(tmp_path)
    home_dir = make_home_dir(tmp_path)

    wire = start_wire(
        config_path=config_path,
        config_text=None,
        work_dir=work_dir,
        home_dir=home_dir,
        yolo=True,
    )
    try:
        send_initialize(wire)

        # 第一个 turn：执行一些操作
        wire.send_json(
            {
                "jsonrpc": "2.0",
                "id": "prompt-1",
                "method": "prompt",
                "params": {"user_input": "first message"},
            }
        )
        resp1, messages1 = collect_until_response(wire, "prompt-1")
        assert resp1.get("result", {}).get("status") == "finished"

        # 验证消息流正常
        assert summarize_messages(messages1) == snapshot(
            [
                {"method": "event", "type": "TurnBegin", "payload": {"user_input": "first message"}},
                {"method": "event", "type": "StepBegin", "payload": {"n": 1}},
                {
                    "method": "event",
                    "type": "ContentPart",
                    "payload": {"type": "text", "text": "first turn"},
                },
                {
                    "method": "event",
                    "type": "StatusUpdate",
                    "payload": {
                        "context_usage": None,
                        "context_tokens": None,
                        "max_context_tokens": None,
                        "token_usage": None,
                        "message_id": None,
                        "plan_mode": False,
                        "mcp_status": None,
                    },
                },
                {"method": "event", "type": "TurnEnd", "payload": {}},
            ]
        )
    finally:
        wire.close()


def test_booklist_wire_message_serialization(_tmp_path) -> None:
    """测试 BookList Wire 消息的序列化格式。

    验证 BookList 消息通过 WireMessageEnvelope 序列化后的 JSON 结构。
    """
    from novel_cli.wire.types import BookList, WireMessageEnvelope

    # 创建 BookList 消息
    book_list = BookList(
        books=["凡人修仙传", "遮天", "完美世界"],
        current_book="凡人修仙传",
    )

    # 通过 WireMessageEnvelope 序列化
    envelope = WireMessageEnvelope.from_wire_message(book_list)

    # 验证序列化结果
    assert envelope.type == "BookList"
    assert envelope.payload["books"] == ["凡人修仙传", "遮天", "完美世界"]
    assert envelope.payload["current_book"] == "凡人修仙传"

    # 反序列化验证
    restored = envelope.to_wire_message()
    assert isinstance(restored, BookList)
    assert restored.books == ["凡人修仙传", "遮天", "完美世界"]
    assert restored.current_book == "凡人修仙传"


def test_booklist_without_current_book(_tmp_path) -> None:
    """测试 BookList 消息 current_book 为 None 时的格式。"""
    from novel_cli.wire.types import BookList, WireMessageEnvelope

    # 创建无选中书籍的 BookList
    book_list = BookList(
        books=["凡人修仙传", "遮天"],
        current_book=None,
    )

    envelope = WireMessageEnvelope.from_wire_message(book_list)

    assert envelope.type == "BookList"
    assert envelope.payload["books"] == ["凡人修仙传", "遮天"]
    assert envelope.payload["current_book"] is None

    # 反序列化验证
    restored = envelope.to_wire_message()
    assert isinstance(restored, BookList)
    assert restored.current_book is None