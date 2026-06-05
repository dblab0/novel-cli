"""当前 KAOS 实例的上下文管理模块。

本模块提供基于 contextvars 的当前 KAOS 实例管理，
允许在异步上下文中获取和设置当前活跃的 KAOS 实例。
"""

from contextvars import ContextVar

from kaos import Kaos
from kaos.local import local_kaos

current_kaos: ContextVar[Kaos] = ContextVar[Kaos]("current_kaos", default=local_kaos)
"""当前 KAOS 实例的上下文变量，默认为本地 KAOS 实例。"""
