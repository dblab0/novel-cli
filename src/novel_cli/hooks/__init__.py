"""Hook 模块，提供钩子定义、引擎和事件类型的导出。"""

from novel_cli.hooks.config import HOOK_EVENT_TYPES, HookDef, HookEventType
from novel_cli.hooks.engine import HookEngine

__all__ = ["HookDef", "HookEventType", "HOOK_EVENT_TYPES", "HookEngine"]