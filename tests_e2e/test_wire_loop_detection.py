"""wire 协议层循环检测 E2E 测试。

使用 _scripted_echo 模拟 LLM 连续输出相同 tool_call，验证循环阻断。
"""

from __future__ import annotations

from inline_snapshot import snapshot

from tests_e2e.wire_helpers import (
    build_shell_tool_call,
    collect_until_response,
    make_home_dir,
    make_work_dir,
    normalize_response,
    send_initialize,
    start_wire,
    write_scripted_config,
)


def test_loop_detection_on_repeated_tool_calls(tmp_path) -> None:
    """连续 5 次相同 shell 调用，验证 wire 进程正确退出（循环阻断）。"""
    # 构造 5 个 step，每个都调用相同 tool_call
    tool_call_line = build_shell_tool_call("tc-loop", "echo stuck")
    scripts = [tool_call_line + "\ntext: retrying..." for _ in range(5)]

    config_path = write_scripted_config(tmp_path, scripts)
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
                "params": {"user_input": "run"},
            }
        )
        resp, messages = collect_until_response(wire, "prompt-1")
        # 第 5 次调用后应收到 RunCancelled → wire 返回错误响应
        assert "error" in normalize_response(resp)
    finally:
        wire.close()


def test_mixed_calls_no_false_positive(tmp_path) -> None:
    """中间穿插不同调用不触发 false positive。"""
    # 交替构造 shell("echo a") 和 shell("echo b")，各 5 次
    scripts = []
    for _ in range(5):
        scripts.append(build_shell_tool_call("tc-a", "echo a") + "\ntext: step a")
        scripts.append(build_shell_tool_call("tc-b", "echo b") + "\ntext: step b")
    # 最后一步正常结束
    scripts.append("text: done")

    config_path = write_scripted_config(tmp_path, scripts)
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
                "params": {"user_input": "run"},
            }
        )
        resp, messages = collect_until_response(wire, "prompt-1")
        # 交替调用不应触发循环检测，应正常完成
        normalized = normalize_response(resp)
        assert "result" in normalized
    finally:
        wire.close()
