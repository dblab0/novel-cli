"""Hook 事件输入构建器模块，为各钩子事件类型构建输入 payload。"""

from __future__ import annotations

from typing import Any


def _base(event: str, session_id: str, cwd: str) -> dict[str, Any]:
    """构建基础 payload 结构。

    Args:
        event: 事件类型名称。
        session_id: 会话唯一标识符。
        cwd: 当前工作目录。

    Returns:
        包含基础字段的事件 payload 字典。
    """
    return {"hook_event_name": event, "session_id": session_id, "cwd": cwd}


def pre_tool_use(
    *,
    session_id: str,
    cwd: str,
    tool_name: str,
    tool_input: dict[str, Any],
    tool_call_id: str = "",
) -> dict[str, Any]:
    """构建 PreToolUse 事件的 payload。

    Args:
        session_id: 会话唯一标识符。
        cwd: 当前工作目录。
        tool_name: 即将执行的工具名称。
        tool_input: 工具的输入参数。
        tool_call_id: 工具调用的唯一标识符。

    Returns:
        PreToolUse 事件的 payload 字典。
    """
    return {
        **_base("PreToolUse", session_id, cwd),
        "tool_name": tool_name,
        "tool_input": tool_input,
        "tool_call_id": tool_call_id,
    }


def post_tool_use(
    *,
    session_id: str,
    cwd: str,
    tool_name: str,
    tool_input: dict[str, Any],
    tool_output: str = "",
    tool_call_id: str = "",
) -> dict[str, Any]:
    """构建 PostToolUse 事件的 payload。

    Args:
        session_id: 会话唯一标识符。
        cwd: 当前工作目录。
        tool_name: 已执行的工具名称。
        tool_input: 工具的输入参数。
        tool_output: 工具的输出结果。
        tool_call_id: 工具调用的唯一标识符。

    Returns:
        PostToolUse 事件的 payload 字典。
    """
    return {
        **_base("PostToolUse", session_id, cwd),
        "tool_name": tool_name,
        "tool_input": tool_input,
        "tool_output": tool_output,
        "tool_call_id": tool_call_id,
    }


def post_tool_use_failure(
    *,
    session_id: str,
    cwd: str,
    tool_name: str,
    tool_input: dict[str, Any],
    error: str,
    tool_call_id: str = "",
) -> dict[str, Any]:
    """构建 PostToolUseFailure 事件的 payload。

    Args:
        session_id: 会话唯一标识符。
        cwd: 当前工作目录。
        tool_name: 执行失败的工具名称。
        tool_input: 工具的输入参数。
        error: 错误信息。
        tool_call_id: 工具调用的唯一标识符。

    Returns:
        PostToolUseFailure 事件的 payload 字典。
    """
    return {
        **_base("PostToolUseFailure", session_id, cwd),
        "tool_name": tool_name,
        "tool_input": tool_input,
        "error": error,
        "tool_call_id": tool_call_id,
    }


def user_prompt_submit(
    *,
    session_id: str,
    cwd: str,
    prompt: str,
) -> dict[str, Any]:
    """构建 UserPromptSubmit 事件的 payload。

    Args:
        session_id: 会话唯一标识符。
        cwd: 当前工作目录。
        prompt: 用户提交的提示内容。

    Returns:
        UserPromptSubmit 事件的 payload 字典。
    """
    return {**_base("UserPromptSubmit", session_id, cwd), "prompt": prompt}


def stop(
    *,
    session_id: str,
    cwd: str,
    stop_hook_active: bool = False,
) -> dict[str, Any]:
    """构建 Stop 事件的 payload。

    Args:
        session_id: 会话唯一标识符。
        cwd: 当前工作目录。
        stop_hook_active: 是否启用停止钩子。

    Returns:
        Stop 事件的 payload 字典。
    """
    return {
        **_base("Stop", session_id, cwd),
        "stop_hook_active": stop_hook_active,
    }


def stop_failure(
    *,
    session_id: str,
    cwd: str,
    error_type: str,
    error_message: str,
) -> dict[str, Any]:
    """构建 StopFailure 事件的 payload。

    Args:
        session_id: 会话唯一标识符。
        cwd: 当前工作目录。
        error_type: 错误类型。
        error_message: 错误信息。

    Returns:
        StopFailure 事件的 payload 字典。
    """
    return {
        **_base("StopFailure", session_id, cwd),
        "error_type": error_type,
        "error_message": error_message,
    }


def session_start(
    *,
    session_id: str,
    cwd: str,
    source: str,
) -> dict[str, Any]:
    """构建 SessionStart 事件的 payload。

    Args:
        session_id: 会话唯一标识符。
        cwd: 当前工作目录。
        source: 会话启动来源。

    Returns:
        SessionStart 事件的 payload 字典。
    """
    return {**_base("SessionStart", session_id, cwd), "source": source}


def session_end(
    *,
    session_id: str,
    cwd: str,
    reason: str,
) -> dict[str, Any]:
    """构建 SessionEnd 事件的 payload。

    Args:
        session_id: 会话唯一标识符。
        cwd: 当前工作目录。
        reason: 会话结束原因。

    Returns:
        SessionEnd 事件的 payload 字典。
    """
    return {**_base("SessionEnd", session_id, cwd), "reason": reason}


def subagent_start(
    *,
    session_id: str,
    cwd: str,
    agent_name: str,
    prompt: str,
) -> dict[str, Any]:
    """构建 SubagentStart 事件的 payload。

    Args:
        session_id: 会话唯一标识符。
        cwd: 当前工作目录。
        agent_name: 子代理名称。
        prompt: 子代理接收的提示内容。

    Returns:
        SubagentStart 事件的 payload 字典。
    """
    return {
        **_base("SubagentStart", session_id, cwd),
        "agent_name": agent_name,
        "prompt": prompt,
    }


def subagent_stop(
    *,
    session_id: str,
    cwd: str,
    agent_name: str,
    response: str = "",
) -> dict[str, Any]:
    """构建 SubagentStop 事件的 payload。

    Args:
        session_id: 会话唯一标识符。
        cwd: 当前工作目录。
        agent_name: 子代理名称。
        response: 子代理的响应内容。

    Returns:
        SubagentStop 事件的 payload 字典。
    """
    return {
        **_base("SubagentStop", session_id, cwd),
        "agent_name": agent_name,
        "response": response,
    }


def pre_compact(
    *,
    session_id: str,
    cwd: str,
    trigger: str,
    token_count: int,
) -> dict[str, Any]:
    """构建 PreCompact 事件的 payload。

    Args:
        session_id: 会话唯一标识符。
        cwd: 当前工作目录。
        trigger: 压缩触发原因。
        token_count: 当前 Token 数量。

    Returns:
        PreCompact 事件的 payload 字典。
    """
    return {
        **_base("PreCompact", session_id, cwd),
        "trigger": trigger,
        "token_count": token_count,
    }


def post_compact(
    *,
    session_id: str,
    cwd: str,
    trigger: str,
    estimated_token_count: int,
) -> dict[str, Any]:
    """构建 PostCompact 事件的 payload。

    Args:
        session_id: 会话唯一标识符。
        cwd: 当前工作目录。
        trigger: 压缩触发原因。
        estimated_token_count: 压缩后预估的 Token 数量。

    Returns:
        PostCompact 事件的 payload 字典。
    """
    return {
        **_base("PostCompact", session_id, cwd),
        "trigger": trigger,
        "estimated_token_count": estimated_token_count,
    }


def notification(
    *,
    session_id: str,
    cwd: str,
    sink: str,
    notification_type: str,
    title: str = "",
    body: str = "",
    severity: str = "info",
) -> dict[str, Any]:
    """构建 Notification 事件的 payload。

    Args:
        session_id: 会话唯一标识符。
        cwd: 当前工作目录。
        sink: 通知目标。
        notification_type: 通知类型。
        title: 通知标题。
        body: 通知正文内容。
        severity: 严重程度，默认 "info"。

    Returns:
        Notification 事件的 payload 字典。
    """
    return {
        **_base("Notification", session_id, cwd),
        "sink": sink,
        "notification_type": notification_type,
        "title": title,
        "body": body,
        "severity": severity,
    }