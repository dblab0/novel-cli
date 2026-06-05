"""工具封装层：直接实例化并调用 SearchEntity/SearchGraph/SearchCorpus/ReadChapter。

绕过 agent 框架，直接调用工具类的 __call__ 方法。
"""

from __future__ import annotations

from typing import Any

from novel_cli.config import load_config
from novel_cli.store import NovelStore
from novel_cli.tools.novel.entity import SearchEntity, SearchEntityParams
from novel_cli.tools.novel.graph import SearchGraph, SearchGraphParams
from novel_cli.tools.novel.corpus import SearchCorpus, SearchCorpusParams
from novel_cli.tools.novel.chapter import ReadChapter, ReadChapterParams


# 工具注册表：(工具类, 参数类)
_TOOL_REGISTRY: dict[str, tuple[type, type]] = {
    "SearchEntity": (SearchEntity, SearchEntityParams),
    "SearchGraph": (SearchGraph, SearchGraphParams),
    "SearchCorpus": (SearchCorpus, SearchCorpusParams),
    "ReadChapter": (ReadChapter, ReadChapterParams),
}


class ToolRunner:
    """工具运行器，直接调用工具类实例。"""

    def __init__(self) -> None:
        config = load_config()
        self._config = config
        self._instances: dict[str, Any] = {}
        for name, (cls, _) in _TOOL_REGISTRY.items():
            self._instances[name] = cls(config)
        self._store: NovelStore | None = None

    async def _ensure_store(self) -> NovelStore:
        """确保数据库连接已建立。"""
        if self._store is None:
            self._store = NovelStore(self._config.services.novel_db)
            await self._store.connect()
        return self._store

    async def list_books(self) -> list[str]:
        """获取所有已入库的书名列表。"""
        store = await self._ensure_store()
        return await store.list_books()

    @staticmethod
    def list_tools() -> list[dict[str, Any]]:
        """返回所有工具的参数定义（从 Params 类的 JSON Schema 提取）。"""
        result = []
        for name, (cls, params_cls) in _TOOL_REGISTRY.items():
            schema = params_cls.model_json_schema()
            # 从类属性 description 获取 md 文件加载的描述
            desc = getattr(cls, "description", "") or ""
            result.append({
                "name": name,
                "description": desc,
                "params_schema": schema,
            })
        return result

    async def call(self, name: str, params: dict, book: str | None = None) -> dict[str, Any]:
        """调用指定工具。

        Args:
            name: 工具名，如 "SearchEntity"。
            params: 工具参数字典。
            book: 书名，自动注入到 book 字段。

        Returns:
            工具返回值字典（ToolOk/ToolError 的 model_dump）。
        """
        if name not in _TOOL_REGISTRY:
            raise ValueError(f"未知工具: {name}")

        _, params_cls = _TOOL_REGISTRY[name]

        # 注入 book 参数（不覆盖显式传入的值）
        if book and "book" not in params:
            params = {**params, "book": book}

        # 前端可能将 list[str] 字段传为字符串，自动修正
        schema = params_cls.model_json_schema()
        props = schema.get("properties", {})
        for field_name, field_schema in props.items():
            if field_name not in params:
                continue
            # 检查字段是否为数组类型（可能是 type=object，也可能是 anyOf 包含 array）
            is_array = field_schema.get("type") == "array"
            if not is_array:
                for sub in field_schema.get("anyOf", []):
                    if sub.get("type") == "array":
                        is_array = True
                        break
            if is_array and isinstance(params[field_name], str):
                params[field_name] = [v.strip() for v in params[field_name].split(",") if v.strip()]

        params_obj = params_cls(**params)
        tool_instance = self._instances[name]
        result = await tool_instance(params_obj)
        return result.model_dump()
