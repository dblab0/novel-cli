"""日期时间格式化工具。

本模块提供相对时间和持续时间的格式化功能，用于用户友好的时间显示。
"""

from datetime import datetime, timedelta


def format_relative_time(timestamp: float) -> str:
    """将时间戳格式化为相对时间字符串。

    Args:
        timestamp: Unix 时间戳。

    Returns:
        相对时间字符串，如 "just now"、"5m ago"、"2h ago"、"3d ago" 或 "MM-DD"。
    """
    now = datetime.now()
    dt = datetime.fromtimestamp(timestamp)
    diff = now - dt
    if diff < timedelta(minutes=5):
        return "just now"
    if diff < timedelta(hours=1):
        minutes = int(diff.total_seconds() / 60)
        return f"{minutes}m ago"
    if diff < timedelta(days=1):
        hours = int(diff.total_seconds() / 3600)
        return f"{hours}h ago"
    if diff < timedelta(days=7):
        return f"{diff.days}d ago"
    return dt.strftime("%m-%d")


def format_duration(seconds: int) -> str:
    """将秒数格式化为持续时间字符串。

    Args:
        seconds: 持续时间（秒）。

    Returns:
        使用短单位表示的持续时间字符串，如 "1d 2h 3m" 或 "0s"。
    """
    delta = timedelta(seconds=seconds)
    parts: list[str] = []
    days = delta.days
    if days:
        parts.append(f"{days}d")
    hours, remainder = divmod(delta.seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if secs and not parts:
        parts.append(f"{secs}s")
    return " ".join(parts) or "0s"