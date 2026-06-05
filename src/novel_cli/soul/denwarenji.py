"""denwarenji 模块。

提供 D-Mail 发送机制，允许向历史检查点发送消息。
用于实现时间旅行式的消息传递功能。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class DMail(BaseModel):
    """D-Mail 消息模型。

    Attributes:
        message: 要发送的消息内容。
        checkpoint_id: 目标检查点 ID，消息将发送回该检查点。
    """

    message: str = Field(description="The message to send.")
    checkpoint_id: int = Field(description="The checkpoint to send the message back to.", ge=0)
    # TODO: 允许将文件系统状态恢复到检查点


class DenwaRenjiError(Exception):
    """denwarenji 操作错误。"""

    pass


class DenwaRenji:
    """denwarenji 管理器，处理 D-Mail 的发送和接收。

    D-Mail 允许向历史检查点发送消息，实现类似时间旅行的功能。

    Args:
        无。

    Attributes:
        _pending_dmail: 待处理的 D-Mail 消息。
        _n_checkpoints: 当前检查点数量。
    """

    def __init__(self):
        self._pending_dmail: DMail | None = None
        self._n_checkpoints: int = 0

    def send_dmail(self, dmail: DMail):
        """发送 D-Mail。由 SendDMail 工具调用。

        Args:
            dmail: 要发送的 D-Mail 消息。

        Raises:
            DenwaRenjiError: 如果已有待处理的 D-Mail，或检查点 ID 无效。
        """
        if self._pending_dmail is not None:
            raise DenwaRenjiError("Only one D-Mail can be sent at a time")
        if dmail.checkpoint_id < 0:
            raise DenwaRenjiError("The checkpoint ID can not be negative")
        if dmail.checkpoint_id >= self._n_checkpoints:
            raise DenwaRenjiError("There is no checkpoint with the given ID")
        self._pending_dmail = dmail

    def set_n_checkpoints(self, n_checkpoints: int):
        """设置检查点数量。由 soul 调用。

        Args:
            n_checkpoints: 新的检查点数量。
        """
        self._n_checkpoints = n_checkpoints

    def fetch_pending_dmail(self) -> DMail | None:
        """获取待处理的 D-Mail。由 soul 调用。

        获取后会清除待处理状态。

        Returns:
            待处理的 D-Mail 消息，如果没有则返回 None。
        """
        pending_dmail = self._pending_dmail
        self._pending_dmail = None
        return pending_dmail