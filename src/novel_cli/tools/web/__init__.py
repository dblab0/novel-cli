"""Web 工具模块。

提供网络搜索和 URL 获取等 Web 操作工具。
"""

from .fetch import FetchURL
from .search import SearchWeb

__all__ = ("SearchWeb", "FetchURL")
