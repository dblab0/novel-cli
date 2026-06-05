"""类型处理工具。

本模块提供类型联合（Union）的扁平化处理功能，
用于简化类型注解的解析和处理。
"""

from types import UnionType
from typing import Any, TypeAliasType, Union, get_args, get_origin


def flatten_union(tp: Any) -> tuple[Any, ...]:
    """扁平化联合类型。

    如果 `tp` 是联合类型（UnionType），返回其扁平化后的参数元组。
    否则返回仅包含 `tp` 的元组。

    Args:
        tp: 要处理的类型。

    Returns:
        扁平化后的类型参数元组。
    """
    if isinstance(tp, TypeAliasType):
        tp = tp.__value__
    origin = get_origin(tp)
    if origin in (UnionType, Union):
        args = get_args(tp)
        flattened_args: list[Any] = []
        for arg in args:
            flattened_args.extend(flatten_union(arg))
        return tuple(flattened_args)
    else:
        return (tp,)