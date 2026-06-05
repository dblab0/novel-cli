"""小说工具基类模块。

提供 NovelToolBase 基类，封装数据库连接管理和 SkipThisTool 逻辑，
供 SearchEntity、SearchGraph、SearchCorpus 等具体工具继承。
"""

from typing import Generic, TypeVar

from kosong.tooling import CallableTool2
from pydantic import BaseModel

from novel_cli.config import Config, NovelDBConfig
from novel_cli.store import NovelStore
from novel_cli.tools import SkipThisTool

# 参数类型变量，约束为 BaseModel 的子类
P = TypeVar("P", bound=BaseModel)


class NovelToolBase(CallableTool2[P], Generic[P]):
    """小说工具基类，封装连接管理和 SkipThisTool 逻辑。

    子类只需实现 __call__ 方法即可，无需关心数据库连接的建立和管理。
    构造时仅保存配置信息而不建立数据库连接，
    首次执行查询时才通过 _ensure_connected() 懒加载建立连接。

    Attributes:
        _config: 数据库配置对象。
        _store: 小说存储门面实例，首次查询时懒加载初始化。
    """

    def __init__(self, config: Config) -> None:
        """初始化小说工具基类。

        接收 Config 实例，采用懒连接模式：
        构造时仅保存配置信息，首次查询时才建立连接。
        如果数据库密码为空则视为未配置，抛出 SkipThisTool 跳过工具加载。

        Args:
            config: 应用配置对象，包含数据库连接信息。

        Raises:
            SkipThisTool: 如果数据库连接未配置（密码为空），则跳过此工具。
        """
        super().__init__()
        self._config: NovelDBConfig = config.services.novel_db
        self._store: NovelStore | None = None
        # 数据库密码为空时视为未配置，跳过工具加载
        if not self._config.pg_password.get_secret_value():
            raise SkipThisTool()

    async def _ensure_connected(self) -> None:
        """确保数据库连接已建立。

        首次调用时执行连接操作（懒加载），后续调用直接返回（复用连接）。

        Raises:
            Exception: 数据库连接失败时抛出异常。
        """
        if self._store is None:
            self._store = NovelStore(self._config)
            await self._store.connect()
